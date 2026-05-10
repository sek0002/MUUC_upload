from __future__ import annotations

import json
import base64
import binascii
import hashlib
import hmac
import os
import secrets
import sqlite3
import struct
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook
from dotenv import load_dotenv
from starlette.middleware.sessions import SessionMiddleware


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
load_dotenv(BASE_DIR / ".env")
STORAGE_ROOT = Path(os.environ.get("FILE_STORAGE_ROOT", str(BASE_DIR / "storage"))).expanduser()
UPLOAD_DIR = STORAGE_ROOT / "uploads"
DB_PATH = STORAGE_ROOT / "portal.db"
METADATA_XLSX_PATH = STORAGE_ROOT / "metadata" / "upload_metadata.xlsx"
SUMMARY_XLSX_PATH = STORAGE_ROOT / "metadata" / "upload_summary.xlsx"

USER_PIN = "6882"
ADMIN_OTP_SECRET = os.environ.get("ADMIN_OTP_SECRET", "").replace(" ", "").upper()
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff"}


app = FastAPI(title="MUUC Upload Portal")
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SESSION_SECRET", "muuc-upload-portal-secret"))
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_display_date(value: str | None) -> str:
    parsed = parse_iso_datetime(value)
    return parsed.strftime("%d/%m/%Y") if parsed else (value or "")


def format_display_datetime(value: str | None) -> str:
    parsed = parse_iso_datetime(value)
    return parsed.strftime("%d/%m/%Y %H:%M") if parsed else (value or "")


templates.env.filters["display_date"] = format_display_date
templates.env.filters["display_datetime"] = format_display_datetime


def get_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_XLSX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(get_db()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                uploaded_at TEXT NOT NULL,
                full_name TEXT NOT NULL,
                receipt_date TEXT NOT NULL,
                claim_details TEXT NOT NULL,
                misc_detail TEXT,
                additional_details TEXT,
                bsb TEXT,
                acc TEXT,
                value_to_claim TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                stored_relative_path TEXT NOT NULL DEFAULT '',
                mime_type TEXT NOT NULL,
                processed INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        existing_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(uploads)").fetchall()
        }
        if "value_to_claim" not in existing_columns:
            connection.execute("ALTER TABLE uploads ADD COLUMN value_to_claim TEXT")
        if "status" not in existing_columns:
            connection.execute("ALTER TABLE uploads ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
            connection.execute(
                "UPDATE uploads SET status = CASE WHEN processed = 1 THEN 'processed' ELSE 'pending' END"
            )
        if "stored_relative_path" not in existing_columns:
            connection.execute("ALTER TABLE uploads ADD COLUMN stored_relative_path TEXT NOT NULL DEFAULT ''")
            connection.execute(
                "UPDATE uploads SET stored_relative_path = stored_filename WHERE stored_relative_path = ''"
            )
        connection.commit()
    export_metadata_spreadsheet()


@app.on_event("startup")
def startup() -> None:
    init_db()


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(timespec="seconds")


def now_formatted() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


def sanitize_path_component(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {" ", "-", "_"} else "_" for char in value.strip())
    compacted = "_".join(cleaned.split())
    return compacted[:80] or "Unknown_Name"


def normalize_claim_value(value: str | None) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    try:
        number = float(cleaned)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Value to claim must be numeric") from exc
    if number.is_integer():
        return str(int(number))
    return cleaned


def normalize_status(value: str | None) -> str:
    status = (value or "pending").strip().lower()
    if status not in {"pending", "processed", "rejected"}:
        raise HTTPException(status_code=400, detail="Invalid status")
    return status


def status_to_processed(status: str) -> int:
    return 1 if status == "processed" else 0


def generate_totp(secret: str, interval: int | None = None) -> str:
    if interval is None:
        interval = int(time.time() // 30)
    try:
        padded_secret = secret + ("=" * ((8 - len(secret) % 8) % 8))
        key = base64.b32decode(padded_secret, casefold=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Admin OTP secret is invalid") from exc
    digest = hmac.new(key, struct.pack(">Q", interval), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 1_000_000:06d}"


def verify_admin_otp(value: str) -> bool:
    submitted = value.strip().replace(" ", "")
    if not ADMIN_OTP_SECRET:
        raise HTTPException(status_code=500, detail="Admin OTP is not configured")
    if not submitted.isdigit() or len(submitted) != 6:
        return False
    current_interval = int(time.time() // 30)
    return any(
        hmac.compare_digest(submitted, generate_totp(ADMIN_OTP_SECRET, current_interval + drift))
        for drift in (-1, 0, 1)
    )


def ensure_role(request: Request, role: str) -> None:
    if request.session.get("role") != role:
        raise HTTPException(status_code=403, detail="Access denied")


def ensure_logged_in(request: Request) -> None:
    if request.session.get("role") not in {"user", "admin"}:
        raise HTTPException(status_code=403, detail="Login required")


def save_upload(file: UploadFile, full_name: str) -> tuple[str, str, str]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file.filename}")
    owner_folder = sanitize_path_component(full_name)
    destination_dir = UPLOAD_DIR / owner_folder
    destination_dir.mkdir(parents=True, exist_ok=True)
    stored_filename = f"{secrets.token_hex(12)}_{sanitize_path_component(Path(file.filename or 'upload').stem)}{suffix}"
    destination = destination_dir / stored_filename
    with destination.open("wb") as output:
        output.write(file.file.read())
    mime_type = file.content_type or "application/octet-stream"
    relative_path = str(destination.relative_to(STORAGE_ROOT))
    return stored_filename, relative_path, mime_type


def parse_receipts(payload: str | None) -> list[dict[str, Any]]:
    if not payload:
        return []
    data = json.loads(payload)
    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="Invalid receipt payload")
    return data


def fetch_uploads(sort_by: str = "newest", status_filter: str = "all", admin: bool = False) -> list[sqlite3.Row]:
    order_by = {
        "newest": "uploaded_at DESC, id DESC",
        "oldest": "uploaded_at ASC, id ASC",
        "name": "full_name COLLATE NOCASE ASC, uploaded_at DESC",
    }.get(sort_by, "uploaded_at DESC, id DESC")
    where_clause = ""
    params: tuple[Any, ...] = ()
    if status_filter in {"processed", "pending", "rejected"}:
        where_clause = "WHERE status = ?"
        params = (status_filter,)
    select_columns = """
        id, created_at, uploaded_at, full_name, receipt_date, claim_details,
        misc_detail, additional_details, value_to_claim, status,
        original_filename, stored_filename, stored_relative_path, mime_type, processed
    """
    if admin:
        select_columns = """
            id, created_at, uploaded_at, full_name, receipt_date, claim_details,
            misc_detail, additional_details, bsb, acc, value_to_claim, status,
            original_filename, stored_filename, stored_relative_path, mime_type, processed
        """
    with closing(get_db()) as connection:
        return connection.execute(
            f"SELECT {select_columns} FROM uploads {where_clause} ORDER BY {order_by}",
            params,
        ).fetchall()


def get_upload(upload_id: int) -> sqlite3.Row:
    with closing(get_db()) as connection:
        row = connection.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    return row


def upload_file_path(upload: sqlite3.Row) -> Path:
    relative_path = upload["stored_relative_path"] or upload["stored_filename"]
    return STORAGE_ROOT / relative_path


def relocate_upload_if_needed(upload: sqlite3.Row, new_full_name: str) -> tuple[str, str]:
    current_path = upload_file_path(upload)
    target_dir = UPLOAD_DIR / sanitize_path_component(new_full_name)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / upload["stored_filename"]
    if current_path != target_path and current_path.exists():
        current_path.replace(target_path)
        if current_path.parent.exists() and current_path.parent != target_dir and not any(current_path.parent.iterdir()):
            current_path.parent.rmdir()
    relative_path = str(target_path.relative_to(STORAGE_ROOT))
    return upload["stored_filename"], relative_path


def export_metadata_spreadsheet() -> None:
    with closing(get_db()) as connection:
        rows = connection.execute(
            """
            SELECT
                id, created_at, uploaded_at, full_name, receipt_date, claim_details,
                misc_detail, additional_details, bsb, acc, value_to_claim, status,
                original_filename, stored_filename, stored_relative_path, mime_type, processed
            FROM uploads
            ORDER BY uploaded_at DESC, id DESC
            """
        ).fetchall()

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Uploads"
    headers = [
        "ID",
        "Created At",
        "Uploaded At",
        "Full Name",
        "Receipt Date",
        "Claim Details",
        "Misc Detail",
        "Additional Details",
        "BSB",
        "ACC",
        "Value To Claim",
        "Status",
        "Original Filename",
        "Stored Filename",
        "Stored Relative Path",
        "MIME Type",
        "Processed Legacy Flag",
    ]
    sheet.append(headers)
    for row in rows:
        sheet.append(
            [
                row["id"],
                row["created_at"],
                row["uploaded_at"],
                row["full_name"],
                row["receipt_date"],
                row["claim_details"],
                row["misc_detail"] or "",
                row["additional_details"] or "",
                row["bsb"] or "",
                row["acc"] or "",
                row["value_to_claim"] or "",
                row["status"],
                row["original_filename"],
                row["stored_filename"],
                row["stored_relative_path"],
                row["mime_type"],
                "Yes" if row["processed"] else "No",
            ]
        )
    for column_cells in sheet.columns:
        length = max(len(str(cell.value or "")) for cell in column_cells)
        sheet.column_dimensions[column_cells[0].column_letter].width = min(length + 4, 42)
    workbook.save(METADATA_XLSX_PATH)

    summary_workbook = Workbook()
    summary_sheet = summary_workbook.active
    summary_sheet.title = "Summary"
    summary_headers = [
        "ID",
        "Created At",
        "Uploaded At",
        "Full Name",
        "Receipt Date",
        "Claim Details",
        "Misc Detail",
        "Additional Details",
        "Value To Claim",
        "Status",
        "Original Filename",
        "Stored Filename",
        "Stored Relative Path",
        "MIME Type",
    ]
    summary_sheet.append(summary_headers)
    for row in rows:
        summary_sheet.append(
            [
                row["id"],
                row["created_at"],
                row["uploaded_at"],
                row["full_name"],
                row["receipt_date"],
                row["claim_details"],
                row["misc_detail"] or "",
                row["additional_details"] or "",
                row["value_to_claim"] or "",
                row["status"],
                row["original_filename"],
                row["stored_filename"],
                row["stored_relative_path"],
                row["mime_type"],
            ]
        )
    for column_cells in summary_sheet.columns:
        length = max(len(str(cell.value or "")) for cell in column_cells)
        summary_sheet.column_dimensions[column_cells[0].column_letter].width = min(length + 4, 42)
    summary_workbook.save(SUMMARY_XLSX_PATH)


def fetch_summary_rows() -> list[sqlite3.Row]:
    with closing(get_db()) as connection:
        return connection.execute(
            """
            SELECT
                id, uploaded_at, full_name, receipt_date, claim_details,
                misc_detail, additional_details, value_to_claim, status,
                original_filename
            FROM uploads
            ORDER BY uploaded_at DESC, id DESC
            """
        ).fetchall()


def insert_receipt(receipt: dict[str, Any], files: list[UploadFile]) -> None:
    if not files:
        return
    claim_values = receipt.get("claim_details") or []
    misc_detail = (receipt.get("misc_detail") or "").strip()
    value_to_claim = normalize_claim_value(receipt.get("value_to_claim"))
    status = "pending"
    if not claim_values:
        raise HTTPException(status_code=400, detail="At least one claim detail must be selected")
    with closing(get_db()) as connection:
        for upload_file in files:
            if not upload_file.filename:
                continue
            full_name = (receipt.get("full_name") or "").strip()
            stored_filename, stored_relative_path, mime_type = save_upload(upload_file, full_name)
            timestamp = now_iso()
            connection.execute(
                """
                INSERT INTO uploads (
                    created_at, uploaded_at, full_name, receipt_date, claim_details,
                    misc_detail, additional_details, bsb, acc, value_to_claim, status,
                    original_filename, stored_filename, stored_relative_path, mime_type, processed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    timestamp,
                    full_name,
                    (receipt.get("receipt_date") or "").strip(),
                    ", ".join(claim_values),
                    misc_detail,
                    (receipt.get("additional_details") or "").strip(),
                    (receipt.get("bsb") or "").strip(),
                    (receipt.get("acc") or "").strip(),
                    value_to_claim,
                    status,
                    upload_file.filename,
                    stored_filename,
                    stored_relative_path,
                    mime_type,
                    status_to_processed(status),
                ),
            )
        connection.commit()
    export_metadata_spreadsheet()


def dashboard_context(
    request: Request,
    admin: bool,
    sort_by: str,
    status_filter: str,
    message: str | None = None,
) -> dict[str, Any]:
    uploads = fetch_uploads(sort_by, status_filter, admin=admin)
    grouped_uploads: list[dict[str, Any]] = []
    last_group_key: str | None = None
    for upload in uploads:
        group_key = format_display_date(upload["uploaded_at"]) or "Unknown Date"
        if group_key != last_group_key:
            grouped_uploads.append({"date": group_key, "items": []})
            last_group_key = group_key
        grouped_uploads[-1]["items"].append(upload)
    return {
        "request": request,
        "admin": admin,
        "sort_by": sort_by,
        "status_filter": status_filter,
        "grouped_uploads": grouped_uploads,
        "message": message,
    }


@app.get("/", response_class=HTMLResponse)
def landing_page(request: Request, message: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        "landing.html",
        {"request": request, "message": message, "current_datetime": now_formatted()},
    )


@app.post("/login")
def login(request: Request, portal_type: str = Form(...), pin: str = Form(...)) -> RedirectResponse:
    target_role = "admin" if portal_type == "admin" else "user"
    if target_role == "admin":
        if not verify_admin_otp(pin):
            return RedirectResponse(url="/?message=Incorrect+admin+OTP", status_code=303)
    elif pin != USER_PIN:
        return RedirectResponse(url="/?message=Incorrect+PIN", status_code=303)
    request.session["role"] = target_role
    if target_role == "admin":
        return RedirectResponse(url="/admin", status_code=303)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def user_dashboard(
    request: Request,
    sort_by: str = "newest",
    status_filter: str = "all",
    message: str | None = None,
) -> HTMLResponse:
    ensure_logged_in(request)
    return templates.TemplateResponse(
        "dashboard.html",
        dashboard_context(request, False, sort_by, status_filter, message),
    )


@app.get("/upload", response_class=HTMLResponse)
def upload_portal(request: Request, message: str | None = None) -> HTMLResponse:
    ensure_logged_in(request)
    return templates.TemplateResponse(
        "upload.html",
        {
            "request": request,
            "admin": request.session.get("role") == "admin",
            "message": message,
            "current_datetime": now_formatted(),
        },
    )


@app.get("/summary", response_class=HTMLResponse)
def summary_page(request: Request, message: str | None = None) -> HTMLResponse:
    ensure_logged_in(request)
    return templates.TemplateResponse(
        "summary.html",
        {
            "request": request,
            "message": message,
            "rows": fetch_summary_rows(),
            "admin": request.session.get("role") == "admin",
        },
    )


@app.get("/summary/download")
def download_summary(request: Request) -> FileResponse:
    ensure_logged_in(request)
    if not SUMMARY_XLSX_PATH.exists():
        export_metadata_spreadsheet()
    return FileResponse(
        path=SUMMARY_XLSX_PATH,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="upload_summary.xlsx",
    )


@app.post("/upload")
async def submit_uploads(
    request: Request,
    receipts_payload: str = Form(...),
    receipt_files: list[UploadFile] = File(default=[]),
) -> RedirectResponse:
    ensure_logged_in(request)
    receipts = parse_receipts(receipts_payload)
    file_map: dict[str, list[UploadFile]] = {}
    for upload_file in receipt_files:
        field_name = upload_file.filename.split("::", 1)[0] if upload_file.filename and "::" in upload_file.filename else None
        if field_name:
            upload_file.filename = upload_file.filename.split("::", 1)[1]
            file_map.setdefault(field_name, []).append(upload_file)

    for receipt in receipts:
        receipt_key = receipt.get("receipt_key")
        files = file_map.get(receipt_key, [])
        insert_receipt(receipt, files)

    redirect_path = "/admin" if request.session.get("role") == "admin" else "/dashboard"
    return RedirectResponse(url=f"{redirect_path}?message=Uploads+saved+successfully", status_code=303)


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    sort_by: str = "newest",
    status_filter: str = "all",
    message: str | None = None,
) -> HTMLResponse:
    ensure_role(request, "admin")
    return templates.TemplateResponse(
        "dashboard.html",
        dashboard_context(request, True, sort_by, status_filter, message),
    )


@app.get("/admin/upload/{upload_id}/edit", response_class=HTMLResponse)
def edit_upload_page(request: Request, upload_id: int, message: str | None = None) -> HTMLResponse:
    ensure_role(request, "admin")
    return templates.TemplateResponse(
        "edit_upload.html",
        {
            "request": request,
            "upload": get_upload(upload_id),
            "message": message,
        },
    )


@app.post("/admin/upload/{upload_id}/status")
def set_status(request: Request, upload_id: int, status: str = Form(...)) -> RedirectResponse:
    ensure_role(request, "admin")
    normalized_status = normalize_status(status)
    with closing(get_db()) as connection:
        connection.execute(
            "UPDATE uploads SET status = ?, processed = ? WHERE id = ?",
            (normalized_status, status_to_processed(normalized_status), upload_id),
        )
        connection.commit()
    export_metadata_spreadsheet()
    return RedirectResponse(url="/admin?message=Status+updated", status_code=303)


@app.post("/admin/upload/{upload_id}/update")
def update_upload(
    request: Request,
    upload_id: int,
    receipt_date: str = Form(...),
    full_name: str = Form(...),
    claim_details: list[str] = Form(default=[]),
    misc_detail: str = Form(default=""),
    additional_details: str = Form(default=""),
    bsb: str = Form(default=""),
    acc: str = Form(default=""),
    status: str = Form(...),
    value_to_claim: str = Form(default=""),
) -> RedirectResponse:
    ensure_role(request, "admin")
    if not claim_details:
        return RedirectResponse(
            url=f"/admin/upload/{upload_id}/edit?message=Select+at+least+one+claim+detail",
            status_code=303,
        )
    upload = get_upload(upload_id)
    new_full_name = full_name.strip()
    normalized_value_to_claim = normalize_claim_value(value_to_claim)
    normalized_status = normalize_status(status)
    stored_filename, stored_relative_path = relocate_upload_if_needed(upload, new_full_name)
    with closing(get_db()) as connection:
        connection.execute(
            """
            UPDATE uploads
            SET receipt_date = ?, full_name = ?, claim_details = ?, misc_detail = ?,
                additional_details = ?, bsb = ?, acc = ?, value_to_claim = ?,
                stored_filename = ?, stored_relative_path = ?, status = ?, processed = ?
            WHERE id = ?
            """,
            (
                receipt_date.strip(),
                new_full_name,
                ", ".join(claim_details),
                misc_detail.strip(),
                additional_details.strip(),
                bsb.strip(),
                acc.strip(),
                normalized_value_to_claim,
                stored_filename,
                stored_relative_path,
                normalized_status,
                status_to_processed(normalized_status),
                upload_id,
            ),
        )
        connection.commit()
    export_metadata_spreadsheet()
    return RedirectResponse(url="/admin?message=Upload+updated", status_code=303)


@app.post("/admin/upload/{upload_id}/delete")
def delete_upload(request: Request, upload_id: int) -> RedirectResponse:
    ensure_role(request, "admin")
    upload = get_upload(upload_id)
    file_path = upload_file_path(upload)
    if file_path.exists():
        file_path.unlink()
    with closing(get_db()) as connection:
        connection.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
        connection.commit()
    if file_path.parent.exists() and not any(file_path.parent.iterdir()):
        file_path.parent.rmdir()
    export_metadata_spreadsheet()
    return RedirectResponse(url="/admin?message=Upload+deleted", status_code=303)


@app.post("/admin/upload/{upload_id}/duplicate")
def duplicate_upload(request: Request, upload_id: int) -> RedirectResponse:
    ensure_role(request, "admin")
    upload = get_upload(upload_id)
    original_path = upload_file_path(upload)
    if not original_path.exists():
        raise HTTPException(status_code=404, detail="Stored file missing")
    duplicate_dir = UPLOAD_DIR / sanitize_path_component(upload["full_name"])
    duplicate_dir.mkdir(parents=True, exist_ok=True)
    new_stored_filename = f"{secrets.token_hex(12)}_{sanitize_path_component(Path(upload['original_filename']).stem)}{original_path.suffix.lower()}"
    duplicate_path = duplicate_dir / new_stored_filename
    duplicate_path.write_bytes(original_path.read_bytes())
    stored_relative_path = str(duplicate_path.relative_to(STORAGE_ROOT))
    timestamp = now_iso()
    with closing(get_db()) as connection:
        connection.execute(
            """
            INSERT INTO uploads (
                created_at, uploaded_at, full_name, receipt_date, claim_details,
                misc_detail, additional_details, bsb, acc, value_to_claim, status,
                original_filename, stored_filename, stored_relative_path, mime_type, processed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                timestamp,
                upload["full_name"],
                upload["receipt_date"],
                upload["claim_details"],
                upload["misc_detail"],
                upload["additional_details"],
                upload["bsb"],
                upload["acc"],
                upload["value_to_claim"],
                "pending",
                upload["original_filename"],
                new_stored_filename,
                stored_relative_path,
                upload["mime_type"],
                0,
            ),
        )
        connection.commit()
    export_metadata_spreadsheet()
    return RedirectResponse(url="/admin?message=Upload+duplicated", status_code=303)


@app.get("/files/{upload_id}")
def get_file(request: Request, upload_id: int) -> FileResponse:
    ensure_logged_in(request)
    upload = get_upload(upload_id)
    file_path = upload_file_path(upload)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=file_path, media_type=upload["mime_type"], filename=upload["original_filename"])


@app.get("/preview/{upload_id}")
def preview_file(request: Request, upload_id: int) -> FileResponse:
    ensure_logged_in(request)
    upload = get_upload(upload_id)
    file_path = upload_file_path(upload)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=file_path, media_type=upload["mime_type"])


@app.get("/sample-pdf")
def sample_pdf() -> Response:
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        b"4 0 obj << /Length 60 >> stream\nBT /F1 18 Tf 30 120 Td (Sample PDF Receipt Preview) Tj ET\nendstream endobj\n"
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000241 00000 n \n0000000352 00000 n \n"
        b"trailer << /Root 1 0 R /Size 6 >>\nstartxref\n422\n%%EOF"
    )
    return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": "inline; filename=sample-receipt.pdf"})

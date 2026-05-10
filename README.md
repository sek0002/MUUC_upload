# MUUC Upload Portal

FastAPI upload portal for PDF and image receipts with:

- Landing page with upload/admin PIN login
- Upload portal with repeatable receipt metadata sections
- Multi-file upload support
- Recent uploads dashboard with sorting
- Admin tools for processed status, editing, duplication, and deletion
- Name-based storage folders under a configurable storage root
- Spreadsheet export of all metadata to Excel

## Run

```bash
python3 -m pip install -r requirements.txt
python3 -m uvicorn app:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000)

## Environment

Create `.env` in the project root:

```bash
FILE_STORAGE_ROOT=/absolute/path/for/muuc-data
SESSION_SECRET=change-this-secret
ADMIN_OTP_SECRET=BASE32SECRET
```

This storage root will contain:

- `uploads/<Full_Name>/...`
- `metadata/upload_metadata.xlsx`
- `portal.db`

## PINs

- Upload login: `6882`
- Admin login: 6-digit OTP from your authenticator app

Generate an admin OTP secret:

```bash
python3 -c "import base64, secrets; print(base64.b32encode(secrets.token_bytes(20)).decode().rstrip('='))"
```

Add the generated value to `.env` as `ADMIN_OTP_SECRET`, then add this account to an authenticator app using a manual setup key:

- Account name: `MUUC Upload Portal`
- Key: the generated `ADMIN_OTP_SECRET`
- Type: time based

## Deployment

For `systemd` and reverse proxy setup, see the files in [`deployment`](/Users/sekkevin/LocalR/MUUC_uploadportal/deployment).

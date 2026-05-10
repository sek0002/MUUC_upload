const menuWrap = document.querySelector(".menu-wrap");
const menuButton = document.querySelector(".menu-button");

if (menuWrap && menuButton) {
  menuButton.addEventListener("click", () => {
    const open = menuWrap.classList.toggle("open");
    menuButton.setAttribute("aria-expanded", open ? "true" : "false");
  });

  document.addEventListener("click", (event) => {
    if (!menuWrap.contains(event.target)) {
      menuWrap.classList.remove("open");
      menuButton.setAttribute("aria-expanded", "false");
    }
  });
}

const uploadForm = document.querySelector("#upload-form");
const receiptSections = document.querySelector("#receipt-sections");
const receiptTemplate = document.querySelector("#receipt-template");
const addReceiptButton = document.querySelector("#add-receipt-button");
const payloadInput = document.querySelector("#receipts_payload");
const zoomModal = document.querySelector("#zoom-modal");
const zoomStage = document.querySelector("[data-zoom-stage]");
const zoomInButton = document.querySelector("[data-zoom-in]");
const zoomOutButton = document.querySelector("[data-zoom-out]");
const zoomResetButton = document.querySelector("[data-zoom-reset]");
let zoomScale = 1;
let activeZoomNode = null;

function applyZoom() {
  if (!activeZoomNode) return;
  activeZoomNode.style.transform = `scale(${zoomScale})`;
  if (zoomResetButton) {
    zoomResetButton.textContent = `${Math.round(zoomScale * 100)}%`;
  }
}

function closeZoomModal() {
  if (!zoomModal || !zoomStage) return;
  zoomStage.innerHTML = "";
  activeZoomNode = null;
  zoomScale = 1;
  zoomModal.hidden = true;
  document.body.classList.remove("modal-open");
}

function openZoomModalFromSource(src, kind, fileName) {
  if (!zoomModal || !zoomStage) return;

  zoomStage.innerHTML = "";
  zoomScale = 1;

  if (kind === "image") {
    const image = document.createElement("img");
    image.src = src;
    image.alt = fileName;
    image.className = "zoom-media";
    activeZoomNode = image;
    zoomStage.appendChild(image);
  } else if (kind === "pdf") {
    const frame = document.createElement("iframe");
    frame.src = src;
    frame.title = fileName;
    frame.className = "zoom-media zoom-frame";
    activeZoomNode = frame;
    zoomStage.appendChild(frame);
  } else {
    activeZoomNode = null;
  }

  if (!activeZoomNode) return;

  applyZoom();
  zoomModal.hidden = false;
  document.body.classList.add("modal-open");
}

function openZoomModal(sourceNode, fileName) {
  if (sourceNode.tagName === "IMG") {
    openZoomModalFromSource(sourceNode.src, "image", sourceNode.alt || fileName);
  } else if (sourceNode.tagName === "IFRAME") {
    openZoomModalFromSource(sourceNode.src, "pdf", sourceNode.title || fileName);
  }
}

if (zoomInButton) {
  zoomInButton.addEventListener("click", () => {
    zoomScale = Math.min(zoomScale + 0.25, 3);
    applyZoom();
  });
}

if (zoomOutButton) {
  zoomOutButton.addEventListener("click", () => {
    zoomScale = Math.max(zoomScale - 0.25, 0.5);
    applyZoom();
  });
}

if (zoomResetButton) {
  zoomResetButton.addEventListener("click", () => {
    zoomScale = 1;
    applyZoom();
  });
}

if (zoomModal) {
  zoomModal.querySelectorAll("[data-zoom-close]").forEach((button) => {
    button.addEventListener("click", closeZoomModal);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !zoomModal.hidden) {
      closeZoomModal();
    }
  });

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-zoom-trigger]");
    if (!trigger) return;
    openZoomModalFromSource(
      trigger.dataset.zoomSrc,
      trigger.dataset.zoomKind,
      trigger.dataset.zoomLabel || "Preview"
    );
  });
}

function renderSelectedPreviews(block) {
  const fileInput = block.querySelector('[data-field="files"]');
  const previewList = block.querySelector("[data-preview-list]");
  if (!fileInput || !previewList) return;

  previewList.querySelectorAll("[data-object-url]").forEach((node) => {
    URL.revokeObjectURL(node.dataset.objectUrl);
  });
  previewList.innerHTML = "";

  const files = Array.from(fileInput.files || []);
  if (files.length === 0) {
    previewList.hidden = true;
    return;
  }

  previewList.hidden = false;

  files.forEach((file) => {
    const item = document.createElement("div");
    item.className = "selected-preview-item";

    const name = document.createElement("p");
    name.className = "selected-preview-name";
    name.textContent = file.name;
    item.appendChild(name);

    if (file.type.startsWith("image/")) {
      const objectUrl = URL.createObjectURL(file);
      const image = document.createElement("img");
      image.src = objectUrl;
      image.alt = `Preview of ${file.name}`;
      image.dataset.objectUrl = objectUrl;
      image.className = "preview-media";
      image.addEventListener("click", () => openZoomModal(image, file.name));
      item.appendChild(image);
    } else if (file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")) {
      const objectUrl = URL.createObjectURL(file);
      const frame = document.createElement("iframe");
      frame.src = `${objectUrl}#toolbar=0&navpanes=0&scrollbar=0`;
      frame.title = `Preview of ${file.name}`;
      frame.dataset.objectUrl = objectUrl;
      frame.className = "preview-media";
      frame.addEventListener("click", () => openZoomModal(frame, file.name));
      item.appendChild(frame);
    } else {
      const note = document.createElement("p");
      note.className = "selected-preview-note";
      note.textContent = "Preview unavailable for this file type.";
      item.appendChild(note);
    }

    previewList.appendChild(item);
  });
}

function buildReceiptSection(index) {
  const fragment = receiptTemplate.content.cloneNode(true);
  const block = fragment.querySelector(".receipt-block");
  const number = fragment.querySelector(".receipt-number");
  const removeButton = fragment.querySelector(".remove-receipt");
  const fileInput = fragment.querySelector('[data-field="files"]');
  const key = `receipt_${Date.now()}_${index}`;

  block.dataset.receiptKey = key;
  number.textContent = index + 1;

  if (fileInput) {
    fileInput.addEventListener("change", () => {
      renderSelectedPreviews(block);
    });
  }

  removeButton.addEventListener("click", () => {
    const previewList = block.querySelector("[data-preview-list]");
    if (previewList) {
      previewList.querySelectorAll("[data-object-url]").forEach((node) => {
        URL.revokeObjectURL(node.dataset.objectUrl);
      });
    }
    block.remove();
    refreshReceiptLabels();
  });

  return fragment;
}

function refreshReceiptLabels() {
  document.querySelectorAll(".receipt-block").forEach((block, index) => {
    const number = block.querySelector(".receipt-number");
    if (number) {
      number.textContent = index + 1;
    }
  });
}

function addReceiptSection() {
  if (!receiptSections || !receiptTemplate) return;
  const fragment = buildReceiptSection(receiptSections.children.length);
  receiptSections.appendChild(fragment);
  refreshReceiptLabels();
}

if (addReceiptButton) {
  addReceiptButton.addEventListener("click", addReceiptSection);
}

if (receiptSections && receiptTemplate && receiptSections.children.length === 0) {
  addReceiptSection();
}

if (uploadForm) {
  uploadForm.addEventListener("submit", async (event) => {
    const blocks = Array.from(document.querySelectorAll(".receipt-block"));
    const payload = [];
    let hasFiles = false;
    const formData = new FormData();

    blocks.forEach((block) => {
      const receiptKey = block.dataset.receiptKey;
      const fileInput = block.querySelector('[data-field="files"]');
      const checkedClaims = Array.from(block.querySelectorAll("[data-claim]:checked")).map((input) => input.value);
      const receipt = {
        receipt_key: receiptKey,
        receipt_date: block.querySelector('[data-field="receipt_date"]').value,
        full_name: block.querySelector('[data-field="full_name"]').value,
        claim_details: checkedClaims,
        misc_detail: block.querySelector('[data-field="misc_detail"]').value,
        additional_details: block.querySelector('[data-field="additional_details"]').value,
        bsb: block.querySelector('[data-field="bsb"]').value,
        acc: block.querySelector('[data-field="acc"]').value,
        value_to_claim: block.querySelector('[data-field="value_to_claim"]').value
      };
      payload.push(receipt);

      if (fileInput && fileInput.files.length > 0) {
        hasFiles = true;
        Array.from(fileInput.files).forEach((file) => {
          formData.append("receipt_files", file, `${receiptKey}::${file.name}`);
        });
      }
    });

    if (!hasFiles) {
      event.preventDefault();
      window.alert("Please attach at least one PDF or image file.");
      return;
    }

    payloadInput.value = JSON.stringify(payload);
    formData.append("receipts_payload", payloadInput.value);

    event.preventDefault();

    const response = await fetch(uploadForm.action, {
      method: "POST",
      body: formData,
      credentials: "same-origin",
      redirect: "follow"
    });

    window.location.href = response.url;
  });
}

const summaryTable = document.querySelector(".summary-table");
const summaryFilterInputs = document.querySelectorAll("[data-summary-filter]");

function applySummaryFilters() {
  if (!summaryTable) return;
  const rows = summaryTable.querySelectorAll("tbody tr");
  const filters = Array.from(summaryFilterInputs).map((input) => input.value.trim().toLowerCase());

  rows.forEach((row) => {
    const cells = Array.from(row.querySelectorAll("td"));
    const visible = filters.every((filterValue, index) => {
      if (!filterValue) return true;
      const cellText = (cells[index]?.textContent || "").trim().toLowerCase();
      return cellText.includes(filterValue);
    });
    row.hidden = !visible;
  });
}

summaryFilterInputs.forEach((input) => {
  input.addEventListener("input", applySummaryFilters);
});

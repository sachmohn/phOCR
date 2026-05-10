const form = document.getElementById("upload-form");
const imageInput = document.getElementById("image-input");
const imagePreview = document.getElementById("image-preview");
const previewFilename = document.getElementById("preview-filename");
const statusBanner = document.getElementById("status-banner");
const summaryCards = document.getElementById("summary-cards");
const extractedItems = document.getElementById("extracted-items");
const uploadMeta = document.getElementById("upload-meta");
const ocrOutput = document.getElementById("ocr-output");
const resultsBody = document.getElementById("results-body");
const submitButton = form.querySelector("button");

const cardLabels = [
  ["Requested", "requested_count"],
  ["Matched", "matched_count"],
  ["In Stock", "in_stock_count"],
  ["Not Found", "not_found_count"],
];

function setStatus(message, variant) {
  statusBanner.hidden = false;
  statusBanner.className = `status-banner ${variant}`;
  statusBanner.textContent = message;
}

function resetStatus() {
  statusBanner.hidden = true;
  statusBanner.textContent = "";
  statusBanner.className = "status-banner";
}

function renderSummary(summary = {}) {
  summaryCards.innerHTML = "";
  cardLabels.forEach(([label, key]) => {
    const card = document.createElement("article");
    card.className = "summary-card";
    card.innerHTML = `<span>${label}</span><strong>${summary[key] ?? 0}</strong>`;
    summaryCards.appendChild(card);
  });
}

function renderExtractedItems(items = []) {
  extractedItems.innerHTML = "";

  if (!items.length) {
    extractedItems.className = "pill-list empty-state";
    extractedItems.textContent = "OCR items will appear here after upload.";
    return;
  }

  extractedItems.className = "pill-list";
  items.forEach((item) => {
    const pill = document.createElement("article");
    pill.className = "pill";
    pill.innerHTML = `
      <strong>${item.item || "Unknown item"}</strong>
      <span>Dosage: ${item.dosage || "N/A"} | Qty: ${item.qty ?? 1}</span>
      ${item.raw_item_text && item.raw_item_text !== item.item ? `<span>OCR: ${item.raw_item_text}</span>` : ""}
    `;
    extractedItems.appendChild(pill);
  });
}

function renderResults(items = []) {
  resultsBody.innerHTML = "";

  if (!items.length) {
    resultsBody.innerHTML = '<tr><td colspan="9" class="empty-table">No prescription processed yet.</td></tr>';
    return;
  }

  items.forEach((item) => {
    const inventory = item.inventory_item || {};
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>
        <strong>${item.requested_item || "-"}</strong><br />
        ${item.raw_item_text && item.raw_item_text !== item.requested_item ? `<span>OCR: ${item.raw_item_text}</span><br />` : ""}
        <span>${item.requested_dosage || "No dosage"}</span>
      </td>
      <td>${inventory.brand_name || "-"}</td>
      <td>${inventory.generic_name || "-"}</td>
      <td><span class="status-chip ${item.stock_status}">${item.stock_status}</span></td>
      <td>${item.requested_qty ?? "-"}</td>
      <td>${inventory.stock_qty ?? "-"}</td>
      <td>${inventory.price_per_strip != null ? `Rs ${inventory.price_per_strip}` : "-"}</td>
      <td>${inventory.rack_location || "-"}</td>
      <td>${item.match_confidence != null ? Number(item.match_confidence).toFixed(2) : "-"}</td>
    `;
    resultsBody.appendChild(row);
  });
}

function clearDashboard(message = "Waiting for upload") {
  renderSummary();
  renderExtractedItems([]);
  renderResults([]);
  uploadMeta.textContent = message;
  ocrOutput.textContent = "No OCR response yet.";
}

imageInput.addEventListener("change", () => {
  resetStatus();

  const [file] = imageInput.files;
  if (!file) {
    imagePreview.hidden = true;
    previewFilename.textContent = "No file selected";
    return;
  }

  previewFilename.textContent = file.name;
  imagePreview.src = URL.createObjectURL(file);
  imagePreview.hidden = false;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  resetStatus();

  if (!imageInput.files.length) {
    setStatus("Select a prescription image first.", "error");
    return;
  }

  submitButton.disabled = true;
  submitButton.textContent = "Processing...";
  setStatus("Sending image to OCR and checking inventory...", "info");

  try {
    const formData = new FormData(form);
    if (window.crypto?.randomUUID) {
      formData.append("upload_nonce", window.crypto.randomUUID());
    }

    clearDashboard("Processing new upload...");
    const response = await fetch(form.dataset.endpoint, {
      method: "POST",
      cache: "no-store",
      body: formData,
    });

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Upload failed.");
    }

    renderSummary(payload.summary);
    renderExtractedItems(payload.extracted_items);
    renderResults(payload.items);
    const metaParts = [
      payload.upload?.filename,
      payload.request_id ? `Request ${payload.request_id.slice(0, 8)}` : null,
      payload.processed_at ? new Date(payload.processed_at).toLocaleString() : null,
    ].filter(Boolean);
    uploadMeta.textContent = metaParts.join(" | ") || "Upload complete";
    ocrOutput.textContent = payload.ocr?.raw_output || "No OCR text returned.";
    setStatus("Prescription processed successfully.", "info");
  } catch (error) {
    clearDashboard("Waiting for upload");
    setStatus(error.message, "error");
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Process Prescription";
  }
});

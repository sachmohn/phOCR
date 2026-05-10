import base64
import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request

from flask import Flask, jsonify, render_template, request


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("PHARMACY_DB_PATH", str(BASE_DIR / "pharmacy.db"))).resolve()
MATCH_THRESHOLD = 0.55
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
OLLAMA_MODEL_NAME = os.getenv("OLLAMA_MODEL_NAME", "glm-ocr:latest")
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://127.0.0.1:11434/api/generate")
OLLAMA_CHAT_URL = os.getenv("OLLAMA_CHAT_URL", OLLAMA_API_URL.replace("/generate", "/chat"))
OLLAMA_CONTEXT_WINDOW = int(os.getenv("OLLAMA_CONTEXT_WINDOW", "131072"))

INSTRUCTION_TOKENS = {
    "after",
    "afternoon",
    "alternate",
    "and",
    "apply",
    "at",
    "before",
    "bedtime",
    "breakfast",
    "daily",
    "dinner",
    "evening",
    "food",
    "for",
    "hs",
    "in",
    "lunch",
    "meal",
    "meals",
    "morning",
    "night",
    "noon",
    "od",
    "once",
    "sos",
    "then",
    "thrice",
    "tid",
    "twice",
    "weekly",
}
FORMULATION_TOKENS = {
    "ampoule",
    "cap",
    "caps",
    "capsule",
    "capsules",
    "cream",
    "drop",
    "drops",
    "gel",
    "gels",
    "inhaler",
    "lotion",
    "ointment",
    "patch",
    "powder",
    "serum",
    "solution",
    "spray",
    "suspension",
    "syrup",
    "tab",
    "tabs",
    "tablet",
    "tablets",
}
DOSAGE_PATTERN = re.compile(r"\b\d+(?:\.\d+)?\s?(?:mcg|mg|g|gm|gms|kg|ml|l|iu|%)\b", re.IGNORECASE)
SCHEDULE_PATTERN = re.compile(r"^\d+(?:\s*-\s*\d+){1,3}$")

PRESCRIPTION_EXTRACTION_PROMPT = """
You are a backend parser for a pharmacy POS workflow.
Read the prescription image and extract only the prescribed medicines.
Ignore patient details and any unrelated text.

Return STRICTLY valid raw JSON in this exact shape:
{
  "order_items": [
    {
      "drug_name": "string",
      "dosage": "string",
      "qty": 1
    }
  ]
}

Rules:
- Output JSON only. No markdown. No explanations.
- Use the medicine or brand name exactly as best as you can read it.
- Keep dosage if visible, for example 650mg or 20gm.
- If quantity is not visible, default qty to 1.
- Ensure commas between array objects.
""".strip()

app = Flask(__name__, template_folder="templates", static_folder="static")


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_text(value):
    if value is None:
        return ""
    normalized = re.sub(r"[^a-zA-Z0-9]+", " ", str(value).strip().lower())
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_dosage(value):
    return normalize_text(value).replace(" ", "")


def remove_dosage_fragments(value):
    return DOSAGE_PATTERN.sub(" ", value or "")


def split_meaningful_lines(value):
    lines = []
    for raw_line in str(value or "").replace("\r", "\n").split("\n"):
        cleaned = normalize_text(raw_line)
        if cleaned:
            lines.append(cleaned)
    return lines


def strip_noise_tokens(value):
    tokens = []
    for token in normalize_text(value).split():
        if token in INSTRUCTION_TOKENS or token in FORMULATION_TOKENS:
            continue
        if token.isdigit():
            continue
        if SCHEDULE_PATTERN.fullmatch(token):
            continue
        tokens.append(token)
    return " ".join(tokens)


def extract_dosage_from_text(value):
    match = DOSAGE_PATTERN.search(str(value or ""))
    if not match:
        return ""
    return normalize_text(match.group(0))


def build_name_aliases(value):
    aliases = set()
    raw_value = str(value or "").strip()

    if not raw_value:
        return aliases

    normalized = normalize_text(raw_value)
    if normalized:
        aliases.add(normalized)

    for line in split_meaningful_lines(raw_value):
        aliases.add(line)
        aliases.add(normalize_text(remove_dosage_fragments(line)))
        aliases.add(strip_noise_tokens(line))
        aliases.add(strip_noise_tokens(remove_dosage_fragments(line)))

    aliases.add(strip_noise_tokens(raw_value))
    aliases.add(strip_noise_tokens(remove_dosage_fragments(raw_value)))
    aliases.add(normalize_text(remove_dosage_fragments(raw_value)))

    return {alias for alias in aliases if alias and len(alias) > 1}


def sanitize_item_name(value):
    ranked_candidates = []

    for line in split_meaningful_lines(value):
        without_dosage = normalize_text(remove_dosage_fragments(line))
        without_noise = strip_noise_tokens(without_dosage)

        for priority, candidate in (
            (3, without_noise),
            (2, without_dosage),
            (1, normalize_text(line)),
        ):
            if candidate:
                token_count = len(candidate.split())
                ranked_candidates.append(
                    (
                        candidate,
                        priority,
                        1 <= token_count <= 4,
                        token_count,
                    )
                )

    if not ranked_candidates:
        return ""

    return max(
        ranked_candidates,
        key=lambda candidate: (
            candidate[1],
            candidate[2],
            -candidate[3],
            -len(candidate[0]),
        ),
    )[0]


def text_similarity(left, right):
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def token_overlap_score(left, right):
    left_tokens = set(normalize_text(left).split())
    right_tokens = set(normalize_text(right).split())

    if not left_tokens or not right_tokens:
        return 0.0

    overlap = len(left_tokens & right_tokens)
    return overlap / max(len(left_tokens), len(right_tokens))


def score_candidate(request_name, request_dosage, row):
    brand_name = row["brand_name"]
    generic_name = row["generic_name"]
    request_aliases = build_name_aliases(request_name)
    brand_aliases = build_name_aliases(brand_name)
    generic_aliases = build_name_aliases(generic_name)

    def score_against_aliases(target_aliases):
        best_alias_score = 0.0

        for request_alias in request_aliases:
            request_tokens = set(request_alias.split())
            for target_alias in target_aliases:
                target_tokens = set(target_alias.split())
                current_score = (0.7 * text_similarity(request_alias, target_alias)) + (
                    0.3 * token_overlap_score(request_alias, target_alias)
                )

                if request_alias == target_alias:
                    current_score = max(current_score, 0.99)
                elif request_alias in target_alias or target_alias in request_alias:
                    current_score = max(current_score, 0.93)
                elif request_tokens and target_tokens and (
                    request_tokens.issubset(target_tokens) or target_tokens.issubset(request_tokens)
                ):
                    current_score = max(current_score, 0.9)

                best_alias_score = max(best_alias_score, current_score)

        return best_alias_score

    brand_score = score_against_aliases(brand_aliases)
    generic_score = score_against_aliases(generic_aliases)

    best_score = brand_score
    matched_on = "brand_name"

    if generic_score > best_score:
        best_score = generic_score
        matched_on = "generic_name"

    requested_dose = normalize_dosage(request_dosage)
    inventory_dose = normalize_dosage(row["dosage"])

    if requested_dose and inventory_dose:
        if requested_dose == inventory_dose:
            best_score += 0.12
        elif requested_dose in inventory_dose or inventory_dose in requested_dose:
            best_score += 0.06
        else:
            best_score -= 0.08

    return min(max(best_score, 0.0), 1.0), matched_on


def parse_quantity(raw_value):
    if raw_value in (None, ""):
        return 1

    try:
        quantity = int(raw_value)
    except (TypeError, ValueError):
        return 1

    return max(quantity, 1)


def coerce_items(payload):
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict):
        raw_items = (
            payload.get("items")
            or payload.get("order_items")
            or payload.get("prescription_items")
            or payload.get("medicines")
        )
    else:
        raw_items = None

    if not isinstance(raw_items, list):
        raise ValueError("Expected a JSON object with an 'items' array or a top-level array.")

    normalized_items = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue

        item_name = (
            raw_item.get("item")
            or raw_item.get("drug_name")
            or raw_item.get("name")
            or raw_item.get("medicine")
            or raw_item.get("brand_name")
        )

        if not item_name or not str(item_name).strip():
            continue

        raw_item_text = str(item_name).strip()
        inferred_dosage = extract_dosage_from_text(raw_item_text)
        cleaned_item_name = sanitize_item_name(raw_item_text)

        normalized_items.append(
            {
                "item": cleaned_item_name or raw_item_text,
                "raw_item_text": raw_item_text,
                "dosage": str(raw_item.get("dosage") or inferred_dosage or "").strip(),
                "qty": parse_quantity(
                    raw_item.get("qty")
                    or raw_item.get("quantity")
                    or raw_item.get("requested_qty")
                ),
            }
        )

    if not normalized_items:
        raise ValueError("No valid prescription items were found in the request payload.")

    return normalized_items


def parse_json_request_payload():
    payload = request.get_json(silent=True)

    if payload is not None:
        return coerce_items(payload)

    raw_data = request.get_data(as_text=True).strip()
    if not raw_data:
        raise ValueError("Request body is empty.")

    cleaned_data = re.sub(r"}\s*{", "},{", raw_data)
    parsed_payload = json.loads(cleaned_data)
    return coerce_items(parsed_payload)


def get_uploaded_image():
    for field_name in ("image", "file", "prescription", "media"):
        uploaded = request.files.get(field_name)
        if uploaded and uploaded.filename:
            return uploaded

    if len(request.files) == 1:
        return next(iter(request.files.values()))

    raise ValueError("No image file found. Send multipart/form-data with an image field.")


def validate_image_upload(uploaded_file):
    suffix = Path(uploaded_file.filename or "").suffix.lower()

    if suffix and suffix in ALLOWED_IMAGE_EXTENSIONS:
        return

    if uploaded_file.mimetype and uploaded_file.mimetype.startswith("image/"):
        return

    allowed = ", ".join(sorted(ALLOWED_IMAGE_EXTENSIONS))
    raise ValueError(f"Unsupported file type. Allowed image types: {allowed}")


def call_ollama_chat_for_image(image_bytes, request_id):
    payload = {
        "model": OLLAMA_MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": PRESCRIPTION_EXTRACTION_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"Process only this one uploaded prescription image. "
                    f"Ignore any previous requests or earlier images. Request ID: {request_id}."
                ),
                "images": [base64.b64encode(image_bytes).decode("utf-8")],
            },
        ],
        "format": "json",
        "stream": False,
        "keep_alive": 0,
        "options": {
            "temperature": 0,
            "num_ctx": OLLAMA_CONTEXT_WINDOW,
        },
    }

    request_body = json.dumps(payload).encode("utf-8")
    http_request = urllib_request.Request(
        OLLAMA_CHAT_URL,
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib_request.urlopen(http_request) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Ollama returned HTTP {exc.code}: {message or exc.reason}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(
            f"Could not reach Ollama at {OLLAMA_CHAT_URL}. Ensure Ollama is running and the model is available."
        ) from exc

    raw_output = body.get("message", {}).get("content", "").strip()
    if not raw_output:
        raise RuntimeError("Ollama returned an empty response.")

    return raw_output, "chat"


def call_ollama_generate_for_image(image_bytes, request_id):
    payload = {
        "model": OLLAMA_MODEL_NAME,
        "prompt": (
            f"{PRESCRIPTION_EXTRACTION_PROMPT}\n\n"
            f"Treat this as a brand-new independent prescription request.\n"
            f"Ignore any previous images or earlier analysis.\n"
            f"Request ID: {request_id}."
        ),
        "images": [base64.b64encode(image_bytes).decode("utf-8")],
        "format": "json",
        "stream": False,
        "keep_alive": 0,
        "options": {
            "temperature": 0,
            "num_ctx": OLLAMA_CONTEXT_WINDOW,
        },
    }

    request_body = json.dumps(payload).encode("utf-8")
    http_request = urllib_request.Request(
        OLLAMA_API_URL,
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib_request.urlopen(http_request) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib_error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Ollama returned HTTP {exc.code}: {message or exc.reason}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(
            f"Could not reach Ollama at {OLLAMA_API_URL}. Ensure Ollama is running and the model is available."
        ) from exc

    raw_output = body.get("response", "").strip()
    if not raw_output:
        raise RuntimeError("Ollama returned an empty response.")

    return raw_output, "generate"


def call_ollama_for_image(image_bytes, request_id):
    try:
        return call_ollama_chat_for_image(image_bytes, request_id)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
    return call_ollama_generate_for_image(image_bytes, request_id)


def extract_json_block(raw_text):
    cleaned = raw_text.replace("```json", "").replace("```", "").strip()

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
        raise ValueError("OCR response did not contain a valid JSON object.")

    cleaned = cleaned[first_brace : last_brace + 1]
    return re.sub(r"}\s*{", "},{", cleaned)


def extract_items_from_uploaded_image():
    uploaded_file = get_uploaded_image()
    validate_image_upload(uploaded_file)

    image_bytes = uploaded_file.read()
    if not image_bytes:
        raise ValueError("Uploaded image is empty.")

    request_id = str(uuid.uuid4())
    file_hash = hashlib.sha256(image_bytes).hexdigest()
    raw_ocr_output, ollama_endpoint = call_ollama_for_image(image_bytes, request_id)
    parsed_payload = json.loads(extract_json_block(raw_ocr_output))
    items = coerce_items(parsed_payload)

    return {
        "request_id": request_id,
        "file_sha256": file_hash,
        "filename": uploaded_file.filename,
        "content_type": uploaded_file.mimetype,
        "raw_ocr_output": raw_ocr_output,
        "ollama_endpoint": ollama_endpoint,
        "items": items,
    }


def find_best_match(item_name, dosage, inventory_rows):
    best_row = None
    best_score = 0.0
    matched_on = None

    for row in inventory_rows:
        score, current_match_type = score_candidate(item_name, dosage, row)
        if score > best_score:
            best_row = row
            best_score = score
            matched_on = current_match_type

    if best_row is None or best_score < MATCH_THRESHOLD:
        return None, 0.0, None

    return best_row, round(best_score, 3), matched_on


def build_match_response(request_item, matched_row, confidence, matched_on):
    requested_qty = request_item["qty"]
    stock_qty = matched_row["stock_qty"]

    if stock_qty <= 0:
        stock_status = "OUT_OF_STOCK"
    elif stock_qty < requested_qty:
        stock_status = "PARTIAL_STOCK"
    else:
        stock_status = "IN_STOCK"

    return {
        "requested_item": request_item["item"],
        "raw_item_text": request_item.get("raw_item_text", request_item["item"]),
        "requested_dosage": request_item["dosage"],
        "requested_qty": requested_qty,
        "match_status": "MATCHED",
        "matched_on": matched_on,
        "match_confidence": confidence,
        "inventory_item": {
            "id": matched_row["id"],
            "brand_name": matched_row["brand_name"],
            "generic_name": matched_row["generic_name"],
            "dosage": matched_row["dosage"],
            "stock_qty": stock_qty,
            "price_per_strip": matched_row["price_per_strip"],
            "rack_location": matched_row["rack_location"],
        },
        "stock_status": stock_status,
        "can_fulfill_requested_qty": stock_qty >= requested_qty,
    }


def build_not_found_response(request_item):
    return {
        "requested_item": request_item["item"],
        "raw_item_text": request_item.get("raw_item_text", request_item["item"]),
        "requested_dosage": request_item["dosage"],
        "requested_qty": request_item["qty"],
        "match_status": "NOT_FOUND",
        "matched_on": None,
        "match_confidence": 0.0,
        "inventory_item": None,
        "stock_status": "NOT_FOUND",
        "can_fulfill_requested_qty": False,
    }


def run_inventory_lookup(request_items):
    conn = get_db_connection()
    inventory_rows = conn.execute("SELECT * FROM inventory").fetchall()
    conn.close()

    response_items = []
    matched_count = 0
    in_stock_count = 0
    partial_stock_count = 0
    out_of_stock_count = 0

    for request_item in request_items:
        matched_row, confidence, matched_on = find_best_match(
            request_item["item"], request_item["dosage"], inventory_rows
        )

        if matched_row is None:
            response_items.append(build_not_found_response(request_item))
            continue

        item_response = build_match_response(request_item, matched_row, confidence, matched_on)
        response_items.append(item_response)
        matched_count += 1

        if item_response["stock_status"] == "IN_STOCK":
            in_stock_count += 1
        elif item_response["stock_status"] == "PARTIAL_STOCK":
            partial_stock_count += 1
        elif item_response["stock_status"] == "OUT_OF_STOCK":
            out_of_stock_count += 1

    return {
        "success": True,
        "items": response_items,
        "summary": {
            "requested_count": len(request_items),
            "matched_count": matched_count,
            "in_stock_count": in_stock_count,
            "partial_stock_count": partial_stock_count,
            "out_of_stock_count": out_of_stock_count,
            "not_found_count": len(request_items) - matched_count,
            "inventory_catalog_size": len(inventory_rows),
        },
    }


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.get("/")
@app.get("/pos")
def pos_dashboard():
    return render_template(
        "index.html",
        model_name=OLLAMA_MODEL_NAME,
        upload_endpoint="/api/prescriptions/upload",
    )


@app.get("/health")
def health_check():
    return jsonify(
        {
            "status": "ok",
            "database": str(DB_PATH),
            "ollama_api_url": OLLAMA_API_URL,
            "ollama_chat_url": OLLAMA_CHAT_URL,
            "ollama_model_name": OLLAMA_MODEL_NAME,
            "ollama_context_window": OLLAMA_CONTEXT_WINDOW,
        }
    )


@app.post("/check_inventory")
@app.post("/api/check_inventory")
def check_inventory():
    try:
        request_items = parse_json_request_payload()
        result = run_inventory_lookup(request_items)
        result["request_id"] = str(uuid.uuid4())
        result["processed_at"] = datetime.now(timezone.utc).isoformat()
        result["source"] = "json"
        result["extracted_items"] = request_items
        return jsonify(result)
    except json.JSONDecodeError as exc:
        return jsonify({"error": f"Invalid JSON payload: {exc.msg}"}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/upload_prescription")
@app.post("/api/prescriptions/upload")
def upload_prescription():
    try:
        extraction = extract_items_from_uploaded_image()
        result = run_inventory_lookup(extraction["items"])
        result["request_id"] = extraction["request_id"]
        result["processed_at"] = datetime.now(timezone.utc).isoformat()
        result["source"] = "multipart/form-data"
        result["extracted_items"] = extraction["items"]
        result["upload"] = {
            "filename": extraction["filename"],
            "content_type": extraction["content_type"],
            "file_sha256": extraction["file_sha256"],
        }
        result["ocr"] = {
            "model": OLLAMA_MODEL_NAME,
            "endpoint": extraction["ollama_endpoint"],
            "raw_output": extraction["raw_ocr_output"],
        }
        return jsonify(result)
    except json.JSONDecodeError as exc:
        return jsonify({"error": f"OCR returned invalid JSON: {exc.msg}"}), 502
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502


if __name__ == "__main__":
    print(f"Starting Pharmacy POS backend on http://127.0.0.1:5000 using {DB_PATH}")
    app.run(debug=True, host="0.0.0.0", port=5000)

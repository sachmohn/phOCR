# phOCR
# Pharmacy POS Backend MVP

This folder contains an isolated Flask backend for:

- JSON inventory lookup
- multipart prescription-image upload
- a staff-facing POS review screen

## Files

- `app.py`: Flask app with OCR upload endpoint, JSON inventory endpoint, and POS page
- `setup_db.py`: Creates `backend/pharmacy.db` with sample inventory data
- `sample_request.json`: Dummy JSON payload for quick testing
- `templates/index.html`: POS review UI
- `static/style.css`: POS styling
- `static/app.js`: Browser logic for uploads and rendering

## Run

```powershell
cd E:\PhOCR\backend
python -m pip install -r requirements.txt
python setup_db.py
python app.py
```

Open the staff UI at:

```text
http://127.0.0.1:5000/
```

## JSON Test

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:5000/check_inventory `
  -ContentType "application/json" `
  -InFile E:\PhOCR\backend\sample_request.json | ConvertTo-Json -Depth 8
```

## Multipart Image Upload Test

Use `curl.exe` from PowerShell:

```powershell
curl.exe -X POST `
  -F "image=@E:\PhOCR\pres.png" `
  http://127.0.0.1:5000/api/prescriptions/upload
```

If you prefer the shorter alias route:

```powershell
curl.exe -X POST `
  -F "image=@E:\PhOCR\pres.png" `
  http://127.0.0.1:5000/upload_prescription
```

## Endpoints

- `GET /`: POS review screen
- `GET /pos`: Same POS review screen
- `GET /health`: Health check with database path and Ollama config
- `POST /check_inventory`: JSON prescription item lookup
- `POST /api/check_inventory`: Same JSON lookup route
- `POST /api/prescriptions/upload`: Multipart image upload route
- `POST /upload_prescription`: Same multipart upload route

## Accepted JSON Input Shapes

The JSON endpoint accepts either:

```json
{
  "items": [
    { "item": "Dolo 650", "dosage": "650mg", "qty": 2 }
  ]
}
```

or:

```json
{
  "order_items": [
    { "drug_name": "Dolo 650", "dosage": "650mg", "qty": 2 }
  ]
}
```

## Multipart Form Data

The upload endpoint accepts an image file in any of these form fields:

- `image`
- `file`
- `prescription`
- `media`

## Requirements For Image Upload

- Ollama must be running locally
- the GLM OCR model must be available
- by default the app calls:

```text
http://127.0.0.1:11434/api/generate
```

- default model name:

```text
glm-ocr:latest
```

Override these with environment variables if needed:

```powershell
$env:OLLAMA_API_URL="http://127.0.0.1:11434/api/generate"
$env:OLLAMA_MODEL_NAME="glm-ocr:latest"
python app.py
```

one day i sent a prescription to my local pharmacy and i was frustrated that they didnt even look at it until i called em on phone

<img width="1919" height="1038" alt="image" src="https://github.com/user-attachments/assets/62471f22-b215-424d-9123-ed6eb61f12c9" />

<img width="1919" height="1034" alt="image" src="https://github.com/user-attachments/assets/113a316a-5444-4b93-9296-2e4a0eea9b2d" />

designed to be used in n8n to automate whatsapp responses

it works with any typa prescription/requirements list(computer text)

if handwritten notes are to be processed, consider adding an image processing api key like gemini or such

works like charm 
:)

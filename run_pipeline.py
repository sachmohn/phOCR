import base64
import requests
import json
import re
import sys

# --- CONFIGURATION ---
# Change this to whatever you named your GLM model in Ollama
OLLAMA_MODEL_NAME = "glm-ocr:latest" 
OLLAMA_API_URL = "http://localhost:11434/api/generate"
FLASK_API_URL = "http://127.0.0.1:5000/check_inventory"

# The bulletproof prompt we crafted
SYSTEM_PROMPT = """
You are a backend data parser for a pharmacy POS system. 
Extract the prescribed medications from the provided prescription image. 
Ignore patient PII (name, phone, address) and focus ONLY on the drug table.

Output your response STRICTLY as a raw JSON object matching the schema below. 
Do not include any conversational text, explanations, or markdown blocks. 

{
  "order_items": [
    {
      "drug_name": "string (extract the brand name)",
      "dosage": "string",
      "frequency": "string",
      "duration": "string",
      "remarks": "string"
    }
  ]
}

CRITICAL: Ensure valid JSON syntax. You MUST include commas separating each object within the array (e.g., }, {).
"""

def image_to_base64(image_path):
    """Converts the image to a base64 string for Ollama."""
    try:
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except FileNotFoundError:
        print(f"❌ Error: Could not find image at {image_path}")
        sys.exit(1)

def run_vision_ocr(base64_image):
    """Sends the image and prompt to local Ollama."""
    print("🧠 Sending image to Ollama (GLM OCR)... This might take a few seconds.")
    
    payload = {
        "model": OLLAMA_MODEL_NAME,
        "prompt": SYSTEM_PROMPT,
        "images": [base64_image],
        "stream": False
    }

    try:
        response = requests.post(OLLAMA_API_URL, json=payload)
        response.raise_for_status()
        return response.json().get("response", "")
    except requests.exceptions.RequestException as e:
        print(f"❌ Error communicating with Ollama: {e}")
        sys.exit(1)

def process_and_send(raw_output):
    """Cleans the output, verifies JSON, and hits the Flask backend."""
    print("\n🧹 Cleaning and parsing OCR output...")
    
    # 1. Strip markdown code blocks just in case GLM adds ```json
    clean_text = raw_output.replace("```json", "").replace("```", "").strip()
    
    # 2. The Regex Band-Aid (fixes missing commas between objects)
    clean_text = re.sub(r'}\s*{', '},{', clean_text)
    
    try:
        # Verify it parses locally before sending to Flask
        json_payload = json.loads(clean_text)
        print(f"✅ Successfully parsed {len(json_payload.get('order_items', []))} items from image.")
    except json.JSONDecodeError as e:
        print("❌ OCR Output is completely broken JSON. Cannot send to database.")
        print(f"Raw Output was:\n{clean_text}")
        sys.exit(1)

    # 3. Send the verified JSON to our Flask Brain
    print("📡 Sending data to Flask Inventory API...")
    try:
        flask_response = requests.post(FLASK_API_URL, json=json_payload)
        flask_response.raise_for_status()
        
        # 4. Display the final POS results!
        print("\n================ POS RESULTS ================")
        results = flask_response.json()
        print(json.dumps(results, indent=4))
        print("=============================================\n")
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Error communicating with Flask: {e}")
        print("Make sure 'python app.py' is running in another terminal window!")

if __name__ == "__main__":
    # You can change this to whatever your test image is named
    TARGET_IMAGE = "pres.png" 
    
    b64_img = image_to_base64(TARGET_IMAGE)
    raw_ocr_text = run_vision_ocr(b64_img)
    process_and_send(raw_ocr_text)
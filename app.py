import json
import re
import sqlite3
import difflib
from flask import Flask, request, jsonify

app = Flask(__name__)

def get_db_connection():
    # Connects to the dummy database we created earlier
    conn = sqlite3.connect('pharmacy.db')
    conn.row_factory = sqlite3.Row # Returns rows as dictionaries
    return conn

@app.route('/check_inventory', methods=['POST'])
def check_inventory():
    # 1. Get raw text to apply our Regex Band-Aid
    raw_data = request.get_data(as_text=True)
    
    # 2. Fix the missing commas from the unquantized model
    cleaned_data = re.sub(r'}\s*{', '},{', raw_data)
    
    # 3. Parse the cleaned JSON
    try:
        data = json.loads(cleaned_data)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON: {e}")
        return jsonify({"error": "Failed to parse OCR data."}), 400

    if not data or 'order_items' not in data:
        return jsonify({"error": "Invalid JSON structure. Missing 'order_items'."}), 400

    # 4. Fetch current inventory from the database
    conn = get_db_connection()
    inventory = conn.execute('SELECT * FROM inventory').fetchall()
    conn.close()

    # Create lists for fuzzy matching
    brand_names = [row['brand_name'].lower() for row in inventory]
    generic_names = [row['generic_name'].lower() for row in inventory]

    results = []

    # 5. Process each drug found in the OCR payload
    for item in data['order_items']:
        ocr_drug = item.get('drug_name', '').strip().lower()
        
        # Skip blank items (like the '----' from your test)
        if len(ocr_drug) < 3:
            continue

        match_found = False
        matched_item = None

        # Step A: Fuzzy match against Brand Names
        brand_match = difflib.get_close_matches(ocr_drug, brand_names, n=1, cutoff=0.4)
        if brand_match:
            matched_item = next(row for row in inventory if row['brand_name'].lower() == brand_match[0])
            match_found = True
        else:
            # Step B: Fuzzy match against Generic Names
            generic_match = difflib.get_close_matches(ocr_drug, generic_names, n=1, cutoff=0.4)
            if generic_match:
                matched_item = next(row for row in inventory if row['generic_name'].lower() == generic_match[0])
                match_found = True

        # 6. Build the response payload for the frontend/POS window
        if match_found:
            results.append({
                "ocr_scanned_name": item['drug_name'],
                "status": "IN STOCK" if matched_item['stock_qty'] > 0 else "OUT OF STOCK",
                "db_match": matched_item['brand_name'],
                "stock_qty": matched_item['stock_qty'],
                "price": matched_item['price_per_strip'],
                "location": matched_item['rack_location']
            })
        else:
             results.append({
                "ocr_scanned_name": item['drug_name'],
                "status": "NOT FOUND",
                "db_match": None,
                "stock_qty": 0,
                "price": 0.0,
                "location": None
            })

    return jsonify({"processed_order": results})

if __name__ == '__main__':
    # Starts the local server on port 5000
    print("ðŸ§  Pharmacy POS Backend starting on http://127.0.0.1:5000")
    app.run(debug=True, port=5000)

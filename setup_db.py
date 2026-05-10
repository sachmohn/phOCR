import sqlite3

def setup_database():
    # This will create a file named 'pharmacy.db' in your current folder
    conn = sqlite3.connect('pharmacy.db')
    cursor = conn.cursor()

    # Create the inventory table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        brand_name TEXT NOT NULL,
        generic_name TEXT NOT NULL,
        dosage TEXT,
        stock_qty INTEGER NOT NULL,
        price_per_strip REAL,
        rack_location TEXT
    )
    ''')

    # Clear out old data if you run this script multiple times during testing
    cursor.execute('DELETE FROM inventory')

    # Insert our dummy stock
    dummy_stock = [
        ('Dolo 650', 'Paracetamol', '650mg', 50, 30.00, 'Rack A1'),
        ('Augmentin 625', 'Amoxicillin Potassium Clavulanate', '625mg', 12, 200.00, 'Rack B2'),
        ('Pan 40', 'Pantoprazole', '40mg', 100, 150.00, 'Rack C1'),
        ('Citrazine', 'Cetirizine', '10mg', 45, 18.00, 'Rack A3'),
        ('Azithral 500', 'Azithromycin', '500mg', 8, 120.00, 'Rack B1'),
        ('Deriva BPO', 'Adapalene and Benzoyl Peroxide', '20gm', 15, 250.00, 'Rack D1'),
        ('Clindac A', 'Clindamycin', '20gm', 25, 180.00, 'Rack D2')
    ]

    cursor.executemany('''
    INSERT INTO inventory (brand_name, generic_name, dosage, stock_qty, price_per_strip, rack_location)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', dummy_stock)

    conn.commit()
    conn.close()
    print("✅ Success: 'pharmacy.db' created and populated with dummy stock!")

if __name__ == '__main__':
    setup_database()
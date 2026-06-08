import sqlite3
import os
from models import init_quant_db, get_session, User, engine

DB_PATH = os.path.join(os.path.dirname(__file__), "market_data.db")

def migrate():
    print("Migrating database...")
    
    # 1. Initialize new SQLAlchemy tables (like 'users')
    init_quant_db()
    
    # 2. Create a default admin user if none exists
    session = get_session()
    admin = session.query(User).filter_by(email="admin@example.com").first()
    if not admin:
        import bcrypt
        hashed = bcrypt.hashpw(b"admin", bcrypt.gensalt()).decode("utf-8")
        admin = User(email="admin@example.com", password_hash=hashed)
        session.add(admin)
        session.commit()
    admin_id = admin.id
    print(f"Default user ID: {admin_id}")
    session.close()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 3. Add user_id to tracked_stocks
    try:
        cursor.execute("ALTER TABLE tracked_stocks ADD COLUMN user_id INTEGER DEFAULT 1;")
        print("Added user_id to tracked_stocks.")
    except sqlite3.OperationalError as e:
        print(f"tracked_stocks already has user_id or error: {e}")

    # 4. We need to update existing tracked_stocks to use admin_id, and then rebuild the primary key?
    # SQLite does not support ALTER TABLE to change primary key.
    # We have to create a new table, copy data, drop old, rename new.
    try:
        cursor.executescript(f"""
            CREATE TABLE IF NOT EXISTS tracked_stocks_new (
                user_id         INTEGER DEFAULT {admin_id},
                ticker          TEXT,
                name            TEXT,
                currency        TEXT,
                sector          TEXT,
                industry        TEXT,
                exchange        TEXT,
                country         TEXT,
                website         TEXT,
                description     TEXT,
                market_cap      REAL,
                employees       INTEGER,
                added_at        TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, ticker)
            );
            
            INSERT OR IGNORE INTO tracked_stocks_new (user_id, ticker, name, currency, sector, industry, exchange, country, website, description, market_cap, employees, added_at)
            SELECT {admin_id}, ticker, name, currency, sector, industry, exchange, country, website, description, market_cap, employees, added_at
            FROM tracked_stocks;
            
            DROP TABLE tracked_stocks;
            ALTER TABLE tracked_stocks_new RENAME TO tracked_stocks;
        """)
        print("Rebuilt tracked_stocks with composite primary key.")
    except Exception as e:
        print(f"Error rebuilding tracked_stocks: {e}")

    # 5. Add user_id to Custom tables (SQLAlchemy added them to models, but SQLite needs ALTER TABLE)
    for table in ["custom_objects", "custom_fields", "custom_records"]:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER DEFAULT {admin_id};")
            print(f"Added user_id to {table}.")
        except sqlite3.OperationalError as e:
            print(f"{table} already has user_id or error: {e}")
            
    # Update existing records
    for table in ["custom_objects", "custom_fields", "custom_records"]:
        cursor.execute(f"UPDATE {table} SET user_id = {admin_id} WHERE user_id IS NULL;")
        
    conn.commit()
    conn.close()
    print("Migration complete!")

if __name__ == "__main__":
    migrate()

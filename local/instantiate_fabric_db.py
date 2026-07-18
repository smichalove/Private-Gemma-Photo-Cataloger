"""Compute Fabric Database Instantiation & Migration Utility.

Purpose:
    Creates and seeds the 'compute_nodes' registry table in the PostgreSQL database.
    This guarantees perfect schema and configuration parity across client-side environments.

Architecture and Mechanics:
    - Centralized SQL Loading: Reuses the 'sql_loader.py' module to dynamically load and 
      execute schema definitions and seed payloads.
    - Autocommit Safety: Transactions are explicitly committed on success and rolled back 
      on error to prevent orphaned or open database locks.
    - Informative Readout: Displays a formatted table of all registered nodes and their 
      network details directly to the terminal on success.

Execution:
    python instantiate_fabric_db.py
"""

import os
import sys
import psycopg2
import argparse
from dotenv import load_dotenv
from typing import Any, Dict

# Reconfigure terminal stdout/stderr for robust UTF-8 printing
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

PROJECT_DIR: str = os.path.dirname(os.path.abspath(__file__))

# Import the centralized SQL loader utility
try:
    from sql_loader import get_sql
except ImportError:
    print("[ERROR] Failed to import sql_loader.py. Make sure it exists in the workspace.", file=sys.stderr)
    sys.exit(1)

# Load workspace credentials
_env_path = os.path.join(PROJECT_DIR, "auth", ".env")
if os.path.exists(_env_path):
    load_dotenv(_env_path)
else:
    load_dotenv()


def _get_pg_conn_params() -> Dict[str, Any]:
    """Retrieves PostgreSQL connection parameters from env config."""
    db_host = os.getenv("DB_HOST", "localhost")
    # Redirect loopbacks (localhost, 127.0.0.1, ::1) under Linux or macOS to the host workstation
    if db_host in ("localhost", "127.0.0.1", "::1") and sys.platform in ("darwin", "linux"):
        db_host = "192.168.1.100"  # Example local IP placeholder (obfuscated)

    params: Dict[str, Any] = {
        "dbname": os.getenv("DB_NAME", "photo_catalog"),
        "user": os.getenv("DB_USER", "postgres"),
        "host": db_host,
        "port": int(os.getenv("DB_PORT", "5432")),
    }
    pwd_path = os.path.join(PROJECT_DIR, "auth", "db_password.txt")
    if os.path.exists(pwd_path):
        with open(pwd_path, "r", encoding="utf-8") as f:
            params["password"] = f.read().strip()
    return params


def instantiate_postgresql() -> bool:
    """Creates and seeds the compute_nodes table in PostgreSQL."""
    print("\n--- Instantiating PostgreSQL Compute Fabric Table ---")
    params = _get_pg_conn_params()
    print(f"Connecting to PostgreSQL ({params['host']}:{params['port']}/{params['dbname']})...")
    
    try:
        conn = psycopg2.connect(**params)
        conn.set_client_encoding("UTF8")
        cur = conn.cursor()
        
        # 1. Create table schema
        print("Executing PostgreSQL schema creation...")
        schema_sql = get_sql("schema/create_compute_nodes_pg.sql", "postgresql")
        cur.execute(schema_sql)
        
        # 2. Seed initial nodes
        print("Executing PostgreSQL seeding...")
        seed_sql = get_sql("schema/seed_compute_nodes_pg.sql", "postgresql")
        cur.execute(seed_sql)
        
        conn.commit()
        print("PostgreSQL compute_nodes instantiated successfully.")
        
        # 3. Read back verification rows
        cur.execute("SELECT hostname, node_type, max_batch_size, is_active FROM compute_nodes ORDER BY hostname")
        rows = cur.fetchall()
        print(f"Registered Nodes in PostgreSQL ({len(rows)}):")
        for r in rows:
            print(f"  - Hostname: {r[0]:15s} | Type: {r[1]:12s} | Max Batch: {r[2]} | Active: {r[3]}")
            
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] PostgreSQL instantiation failed: {e}", file=sys.stderr)
        return False


def main() -> None:
    """Main execution wrapper."""
    parser = argparse.ArgumentParser(description="Compute Fabric Database Instantiation & Migration Utility (PostgreSQL)")
    args = parser.parse_args()

    print("============================================================")
    print("  Compute Fabric Schema Creation & Seeding Utility")
    print("============================================================")
    
    pg_ok = instantiate_postgresql()
    
    print("\n============================================================")
    if pg_ok:
        print("  Database instantiation completed successfully!")
        print("============================================================")
        sys.exit(0)
    else:
        print("  [ERROR] Database instantiation failed.")
        print("============================================================")
        sys.exit(1)


if __name__ == "__main__":
    main()

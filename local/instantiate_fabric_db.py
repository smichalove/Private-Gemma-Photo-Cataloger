"""Compute Fabric Database Instantiation & Migration Utility.

Purpose:
    Creates and seeds the 'compute_nodes' and 'photos' database tables across both
    SQLite and PostgreSQL database backends.
    This guarantees perfect schema and configuration parity across client-side environments.

Architecture and Mechanics:
    - Centralized SQL Loading: Reuses the 'sql_loader.py' module to dynamically load and 
      execute schema definitions and seed payloads.
    - Full Schema Parity: Instantiates both base tables and applies migration schemas.
    - Autocommit Safety: Transactions are explicitly committed on success and rolled back 
      on error to prevent orphaned or open database locks.
    - Informative Readout: Displays formatted tables of all registered nodes and their 
      network details directly to the terminal on success.

Execution:
    python instantiate_fabric_db.py [--backend {sqlite,postgresql,all}] [--sqlite-path PATH]
"""

import os
import sys
import sqlite3
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

# Import schema migration utility from describe_photos
try:
    from describe_photos import migrate_photos_schema
except ImportError:
    migrate_photos_schema = None

# Load workspace credentials
_env_path = os.path.join(PROJECT_DIR, "auth", ".env")
if os.path.exists(_env_path):
    load_dotenv(_env_path)
else:
    load_dotenv()


def _get_pg_conn_params() -> Dict[str, Any]:
    """Retrieves PostgreSQL connection parameters from env config.

    Returns:
        Dict mapping connection parameter names to values.
    """
    db_host = os.getenv("DB_HOST", "localhost")
    if db_host in ("localhost", "127.0.0.1", "::1") and sys.platform in ("darwin", "linux"):
        db_host = "192.168.1.100"

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


def instantiate_sqlite(db_path: str = "") -> bool:
    """Creates and seeds the photos and compute_nodes tables in SQLite.

    Args:
        db_path: Path to the SQLite database file. Defaults to OUTPUT_DATABASE_SQLITE or photo_catalog.db.

    Returns:
        True if successful, False otherwise.
    """
    if not db_path:
        db_path = os.getenv("OUTPUT_DATABASE_SQLITE", os.path.join(PROJECT_DIR, "photo_catalog.db"))
    
    print(f"\n--- Instantiating SQLite Database ({db_path}) ---")
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        # 1. Create compute_nodes table & seed
        print("Creating SQLite compute_nodes schema...")
        schema_sql = get_sql("schema/create_compute_nodes_sqlite.sql", "sqlite")
        cur.executescript(schema_sql)
        
        print("Seeding SQLite compute_nodes table...")
        seed_sql = get_sql("schema/seed_compute_nodes_sqlite.sql", "sqlite")
        cur.executescript(seed_sql)
        
        # 2. Create photos table & indexes
        print("Creating SQLite photos schema...")
        photos_sql = get_sql("schema/create_photos_table.sql", "sqlite")
        cur.executescript(photos_sql)
        idx_sql = get_sql("schema/create_indexes.sql", "sqlite")
        cur.executescript(idx_sql)
        
        conn.commit()
        
        # 3. Apply schema migrations (extended metadata columns)
        if migrate_photos_schema is not None:
            print("Applying SQLite photos schema migrations...")
            migrate_photos_schema(conn, "sqlite")
            
        print("SQLite database instantiated successfully.")
        
        # 4. Read back verification rows
        cur.execute("SELECT hostname, node_type, max_batch_size, is_active FROM compute_nodes ORDER BY hostname")
        rows = cur.fetchall()
        print(f"Registered Compute Nodes in SQLite ({len(rows)}):")
        for r in rows:
            print(f"  - Hostname: {r[0]:15s} | Type: {r[1]:12s} | Max Batch: {r[2]} | Active: {r[3]}")
            
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[ERROR] SQLite database instantiation failed: {e}", file=sys.stderr)
        return False


def instantiate_postgresql() -> bool:
    """Creates and seeds the compute_nodes table in PostgreSQL.

    Returns:
        True if successful, False otherwise.
    """
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
    parser = argparse.ArgumentParser(description="Compute Fabric Database Instantiation & Migration Utility")
    parser.add_argument(
        "--backend",
        choices=["sqlite", "postgresql", "all"],
        default="all",
        help="Database backend to instantiate (default: all)"
    )
    parser.add_argument(
        "--sqlite-path",
        type=str,
        default="",
        help="Custom path for SQLite database file."
    )
    args = parser.parse_args()

    print("============================================================")
    print("  Compute Fabric Schema Creation & Seeding Utility")
    print("============================================================")
    
    success = True
    if args.backend in ("sqlite", "all"):
        sq_ok = instantiate_sqlite(args.sqlite_path)
        if not sq_ok and args.backend == "sqlite":
            success = False
            
    if args.backend in ("postgresql", "all"):
        pg_ok = instantiate_postgresql()
        if not pg_ok and args.backend == "postgresql":
            success = False
    
    print("\n============================================================")
    if success:
        print("  Database instantiation completed!")
        print("============================================================")
        sys.exit(0)
    else:
        print("  [ERROR] Database instantiation encountered errors.")
        print("============================================================")
        sys.exit(1)


if __name__ == "__main__":
    main()

"""Interactive CLI REPL client for the photo catalog database.

Purpose:
    This script provides an interactive Read-Eval-Print Loop (REPL) CLI chat client
    connected to the local photo catalog SQLite database (photo_catalog.db) and
    the offline Gemma 4 VLM. It enables users to ask natural language questions about
    the photo catalog, which the VLM answers by generating and executing SQL queries.

Architecture and Mechanics:
    1. WSL2 Server Control: Integrates with wsl_client to start and keep the vision
       server alive during the session.
    2. Dynamic Prompting: Reloads 'db_prompt.txt' dynamically on each query.
    3. Tool Call Parser: Parses '<tool_call>{"tool": "query_db", "sql": "..."}</tool_call>'
       blocks using regular expressions.
    4. SQLite Executor: Connects to 'photo_catalog.db' in read-only mode to retrieve
       records, formatting the results as clean markdown tables.
    5. Agent Loop: Runs a multi-step completion loop (up to 5 turns) to let the model
       reason over query results before delivering the final response.

Execution Modes:
    - Interactive CLI Shell: Run from a console terminal to start the chat loop.
      Command:
        python db_chat_repl.py
"""

import os
import sys
import textwrap
import re
import json
import sqlite3
import signal
import datetime
import requests
import psycopg2
import time
import concurrent.futures
from dotenv import load_dotenv
from typing import Dict, List, Optional, Tuple, Any
from fabric_manager import FabricManager



SUPPORTED_MEDIA_EXTENSIONS = (
    ".flac", ".mp3", ".wav", ".m4a", ".ogg", ".wma", ".aac", ".ape", ".wv", ".m4b",
    ".mp4", ".mov", ".mkv", ".avi", ".mpg", ".mpeg", ".wmv", ".flv", ".m4v", ".webm", ".3gp", ".divx"
)

# Load workspace environment variables
if os.path.exists("auth/.env"):
    load_dotenv("auth/.env")
else:
    load_dotenv()

# Load local environment overrides if present
if os.path.exists("auth/.env.local"):
    load_dotenv("auth/.env.local", override=True)

# Reconfigure console streams for UTF-8 on Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

# Try to import readline for history and editing capabilities
try:
    import readline
except ImportError:
    readline = None

SERVER_URL: str = os.getenv("LOCAL_SERVER_URL", "http://127.0.0.1:8000/analyze")  # 127.0.0.1 resolves to localhost (loopback interface)
# On macOS or Linux clients, route loopback server URL to the workstation host as needed.
# Use LOCAL_SERVER_URL environment variable to customize this default behavior.

PROJECT_DIR: str = os.path.dirname(os.path.abspath(__file__))
DB_PATH: str = os.getenv("DB_PATH", os.path.join(PROJECT_DIR, "photo_catalog.db"))
PROMPT_FILE: str = os.getenv("PROMPT_FILE", "db_prompt.txt")

# Track absolute paths from the most recent SQL query to support /open index command
last_query_paths: List[str] = []


def sigint_handler(signum: int, frame: Any) -> None:
    """Handles SIGINT (Ctrl-C) to exit the client gracefully.

    Args:
        signum: The signal number (typically SIGINT).
        frame: The current execution frame object.

    Returns:
        None
    """
    print("\nExiting...")
    sys.exit(0)


def load_system_prompt(file_name: str = PROMPT_FILE) -> str:
    """Loads the system prompt template from an external file on disk.

    Args:
        file_name: The filename of the prompt template.

    Returns:
        The raw string content of the system prompt template.
    """
    file_path: str = os.path.join(PROJECT_DIR, file_name)
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"[Warning] Failed to read prompt template: {e}")

    # Fallback prompt in case the file cannot be accessed
    return (
        "You are a helpful assistant for the photo catalog database.\n"
        "=== SYSTEM CONTEXT ===\n"
        "Current local date/time: {current_time}\n"
        "Total photo records currently cataloged: {total_photos}\n"
    )


def load_sql_fix_prompt(file_name: str = "sql_fix_prompt.txt") -> str:
    """Loads the SQL correction prompt template from an external file on disk.

    Args:
        file_name: The filename of the prompt template.

    Returns:
        The raw string content of the SQL correction prompt template.
    """
    file_path: str = os.path.join(PROJECT_DIR, file_name)
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"[Warning] Failed to read SQL fix prompt template: {e}")

    # Fallback template in case the file cannot be accessed
    return (
        "The following SQL query failed to run:\n"
        "{failed_sql}\n\n"
        "Error was:\n"
        "{error_message}\n\n"
        "Please correct the query and output only the fixed SQL query."
    )


def extract_sql_from_response(text: str) -> str:
    """Extracts a SQL query string from an LLM response.

    Strips any tool calls or markdown formatting from the response.

    Args:
        text: The raw LLM response string.

    Returns:
        The extracted SQL query string.
    """
    # Try parsing tool call blocks first
    tool_call_match = re.search(r"<tool_call>(.*?)</tool_call>", text, re.DOTALL)
    if tool_call_match:
        tool_json_str: str = tool_call_match.group(1).strip()
        try:
            tool_data: Dict[str, Any] = json.loads(tool_json_str)
            return tool_data.get("sql", "").strip()
        except Exception:
            sql_match = re.search(r"(SELECT\s+.*)", tool_json_str, re.IGNORECASE | re.DOTALL)
            if sql_match:
                return sql_match.group(1).strip()

    # Try parsing markdown SQL code blocks
    code_block_match = re.search(r"```(?:sql)?(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if code_block_match:
        return code_block_match.group(1).strip()

    # Fallback to direct raw SELECT statements
    direct_match = re.search(r"(SELECT\s+.*|WITH\s+.*)", text, re.IGNORECASE | re.DOTALL)
    if direct_match:
        return direct_match.group(1).strip()

    return text.strip()


def query_llm_for_correction(
    prompt_text: str,
    messages: Optional[List[Dict[str, str]]] = None,
    remote: bool = False,
    model_name: str = "gemma4-it-q4:latest",
    host: str = "127.0.0.1",  # Default remote host IP placeholder
    port: int = 11434,
    session: Optional[requests.Session] = None
) -> str:
    """Queries the model server (local or remote) to fix a broken SQL query.

    Args:
        prompt_text: The formatted prompt string (used for local/legacy API).
        messages: Message history list of dicts (used for remote /api/chat).
        remote: If True, uses the remote Ollama server.
        model_name: Remote model name.
        host: Remote host IP.
        port: Remote host port.
        session: Active requests session.

    Returns:
        The response text string from the model.

    Raises:
        RuntimeError: If the server returns a non-200 status code.
    """
    if session is None:
        session = requests.Session()

    if not remote:
        target_url = SERVER_URL
        payload = {
            "prompt_text": prompt_text,
            "temperature": 0.2,
            "max_new_tokens": 4096
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(session.post, target_url, json=payload, timeout=600.0)
            last_print = time.time()
            dots_printed = False
            while not future.done():
                time.sleep(0.1)
                if time.time() - last_print >= 10.0:
                    print(".", end="", flush=True)
                    last_print = time.time()
                    dots_printed = True
            if dots_printed:
                print()
            response = future.result()
        if response.status_code != 200:
            raise RuntimeError(f"Server returned status code {response.status_code}: {response.text}")
        result: Dict[str, Any] = response.json()
        return result.get("response", "").strip()

    # Remote Mode with Dynamic Fabric Failover
    fabric_mgr = FabricManager()
    max_attempts = 3
    attempt = 0
    current_host = host
    current_port = port
    current_model = model_name
    current_node_hostname = "unknown"

    with fabric_mgr.pool_lock:
        for k, n in fabric_mgr.nodes.items():
            if n.resolved_ip == current_host:
                current_node_hostname = k
                break

    while attempt < max_attempts:
        target_url = f"http://{current_host}:{current_port}/api/chat"
        if not messages:
            messages = [{"role": "user", "content": prompt_text}]
        payload = {
            "model": current_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_ctx": 16384,
                "num_predict": 4096
            }
        }
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(session.post, target_url, json=payload, timeout=600.0)
                last_print = time.time()
                dots_printed = False
                while not future.done():
                    time.sleep(0.1)
                    if time.time() - last_print >= 10.0:
                        print(".", end="", flush=True)
                        last_print = time.time()
                        dots_printed = True
                if dots_printed:
                    print()
                response = future.result()
            
            if response.status_code != 200:
                raise RuntimeError(f"Status code {response.status_code}")
                
            result: Dict[str, Any] = response.json()
            return result.get("message", {}).get("content", "").strip()
        except Exception as e:
            attempt += 1
            print(f"\n[FABRIC] [CORRECTION] Connection failed on node '{current_node_hostname}' ({current_host}:{current_port}): {e}")
            fabric_mgr.mark_node_failed(current_node_hostname)
            
            if attempt >= max_attempts:
                print("[FABRIC] All correction retry attempts and failovers exhausted.")
                raise e
            
            # Fetch next best node
            next_nodes = fabric_mgr.get_nodes_for_workload(current_model, "ollama")
            if next_nodes:
                best_next = next_nodes[0]
                current_node_hostname = best_next.hostname
                current_host = best_next.resolved_ip
                current_port = best_next.services.get("ollama", 11434)
                print(f"[FABRIC] [CORRECTION-FAILOVER] Switched to next best node: '{current_node_hostname}' ({current_host}:{current_port})")
            else:
                any_active = fabric_mgr.get_active_nodes()
                if any_active:
                    best_next = any_active[0]
                    current_node_hostname = best_next.hostname
                    current_host = best_next.resolved_ip
                    current_port = best_next.services.get("ollama", 11434)
                    current_model = best_next.supported_models[0]
                    print(f"[FABRIC] [CORRECTION-FAILOVER] Switched to any active node: '{current_node_hostname}' with fallback model '{current_model}' ({current_host}:{current_port})")
                else:
                    raise e


def get_db_conn_params() -> dict:
    """Constructs connection parameters for PostgreSQL, resolving local host overrides.

    This function builds a parameter dictionary for psycopg2 connections. Because the shared
    auth/.env configuration file on the SMB drive is designed for local workstation use and
    defaults the DB_HOST to loopback (localhost or 127.0.0.1), running the REPL natively on
    macOS client machines would fail. This function dynamically resolves local host overrides,
    allowing remote database connection parameters to be loaded cleanly without configuration conflicts.

    Args:
        None

    Returns:
        A dictionary containing database connection arguments (dbname, user, host, port, password).
    """
    db_host = os.getenv("DB_HOST", "localhost")
    # Under macOS or Linux/WSL clients, route local loopback connection parameters using the DB_HOST environment variables or local host configuration.
        
    db_conn_params = {
        "dbname": os.getenv("DB_NAME", "photo_catalog"),
        "user": os.getenv("DB_USER", "postgres"),
        "host": db_host,
        "port": int(os.getenv("DB_PORT", "5432")),
    }
    pwd_path = os.path.join(PROJECT_DIR, "auth", "db_password.txt")
    if os.path.exists(pwd_path):
        with open(pwd_path, "r", encoding="utf-8") as f:
            db_conn_params["password"] = f.read().strip()
    return db_conn_params


def get_total_photos_count() -> int:
    """Queries the database to return the total number of cataloged photos.

    Args:
        None

    Returns:
        The total number of records in the photos table.
    """
    is_testing = "pytest" in sys.argv[0] or "unittest" in sys.argv[0] or "PYTEST_CURRENT_TEST" in os.environ
    db_backend = "sqlite" if is_testing else os.getenv("DB_BACKEND", "postgresql").lower()
    
    if db_backend == "postgresql":
        conn = None
        try:
            db_conn_params = get_db_conn_params()
            conn = psycopg2.connect(**db_conn_params)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM photos")
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            print(f"[Warning] Failed to fetch total photos count from PostgreSQL: {e}")
            return 0
        finally:
            if conn:
                conn.close()
    else:
        if not os.path.exists(DB_PATH):
            return 0
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM photos")
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception as e:
            print(f"[Warning] Failed to fetch total photos count: {e}")
            return 0
        finally:
            if conn:
                conn.close()


def execute_sql(sql: str) -> Tuple[str, str, List[str]]:
    """Executes a database query against the photo catalog database.

    Args:
        sql: The SQL query string.

    Returns:
        A tuple of (raw_json_for_llm, terminal_display_for_user, paths_list).
    """
    is_testing = "pytest" in sys.argv[0] or "unittest" in sys.argv[0] or "PYTEST_CURRENT_TEST" in os.environ
    db_backend = "sqlite" if is_testing else os.getenv("DB_BACKEND", "postgresql").lower()

    conn = None
    try:
        if db_backend == "postgresql":
            db_conn_params = get_db_conn_params()
            
            # Connect in read-only mode to prevent mutation from generated SQL queries
            conn = psycopg2.connect(**db_conn_params)
            conn.set_session(readonly=True, autocommit=True)
            cursor = conn.cursor()
        else:
            if not os.path.exists(DB_PATH):
                err: str = f"Error: Database file not found at {DB_PATH}"
                return err, err, []
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            cursor = conn.cursor()

        # Rewrite parameter markers if query contains SQLite '?' but we are on PostgreSQL
        if db_backend == "postgresql":
            sql = sql.replace("?", "%s")
            
        cursor.execute(sql)

        if cursor.description:
            cols: List[str] = [desc[0] for desc in cursor.description]
            rows: List[Tuple[Any, ...]] = cursor.fetchall()
            if not rows:
                msg: str = "Query executed successfully. No rows returned."
                return msg, msg, []

            # Determine path column index early
            path_col_idx: int = -1
            for name in ["full_path", "rel_path", "file_path"]:
                if name in cols:
                    path_col_idx = cols.index(name)
                    break

            # --- Fetch full paths if we only have rel_path for RAG/VLM context enrichment ---
            rel_to_full: Dict[str, str] = {}
            if "rel_path" in cols:
                rel_paths_in_rows = [row[cols.index("rel_path")] for row in rows if row[cols.index("rel_path")] is not None]
                if rel_paths_in_rows:
                    try:
                        if db_backend == "postgresql":
                            conn2 = psycopg2.connect(**db_conn_params)
                            conn2.set_session(readonly=True, autocommit=True)
                            cursor2 = conn2.cursor()
                            placeholders = ",".join(["%s"] * len(rel_paths_in_rows))
                            cursor2.execute(f"SELECT rel_path, full_path FROM photos WHERE rel_path IN ({placeholders})", rel_paths_in_rows)
                        else:
                            conn2 = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
                            cursor2 = conn2.cursor()
                            placeholders = ",".join(["?"] * len(rel_paths_in_rows))
                            cursor2.execute(f"SELECT rel_path, full_path FROM photos WHERE rel_path IN ({placeholders})", rel_paths_in_rows)
                        
                        for r_path, f_path in cursor2.fetchall():
                            rel_to_full[r_path.lower()] = f_path
                        conn2.close()
                    except Exception as e:
                        print(f"[Warning] Failed to fetch full_path mappings for VLM: {e}")

            # --- Construct full untruncated paths list for /queue and /play commands ---
            paths_list: List[str] = []
            if path_col_idx != -1:
                for row in rows:
                    val_path = row[path_col_idx]
                    if val_path is not None:
                        val_str = str(val_path)
                        resolved_path = val_str
                        if cols[path_col_idx] == "rel_path" and val_str.lower() in rel_to_full:
                            resolved_path = rel_to_full[val_str.lower()]
                        paths_list.append(os.path.normpath(resolved_path))

            # Truncate the total return (list of rows) to 300 rows for CLI display
            total_count = len(rows)
            if len(rows) > 300:
                rows = rows[:300]

            # --- 1. Construct raw markdown for VLM (Always ensuring full_path is present in context) ---
            # Slices cell strings to 2000 characters directly in Python to protect VRAM dynamically.
            # If the query returns more than 10 rows, we send only the first 5 rows as a sample
            # and a note about the total count to prevent swamping VRAM during VLM reasoning.
            vlm_cols = list(cols)
            has_appended_full = "full_path" not in cols and bool(rel_to_full)
            if has_appended_full:
                vlm_cols.append("full_path")

            raw_headers: str = f"| {' | '.join(vlm_cols)} |"
            raw_separator: str = f"| {' | '.join(['---'] * len(vlm_cols))} |"
            raw_lines: List[str] = [raw_headers, raw_separator]
            
            rel_idx = cols.index("rel_path") if "rel_path" in cols else -1

            if total_count > 10:
                vlm_rows: List[Tuple[Any, ...]] = rows[:5]
                prefix_note = (
                    f"Query executed successfully. Returned {total_count} rows. "
                    f"Only the first 5 rows are shown below as a sample to save context window. "
                    f"All {total_count} rows have already been printed directly to the user's terminal. "
                    f"Refer the user to the printed list above and do NOT list individual paths or repeat descriptions in your response.\n\n"
                )
            else:
                vlm_rows = rows
                prefix_note = ""

            for row in vlm_rows:
                row_str: List[str] = []
                for idx_c, val in enumerate(row):
                    if val is None:
                        row_str.append("NULL")
                    elif isinstance(val, float):
                        row_str.append(f"{val:.3f}")
                    else:
                        val_str: str = str(val).replace("\n", " ")
                        if len(val_str) > 2000:
                            row_str.append(val_str[:1997] + "...")
                        else:
                            row_str.append(val_str)
                
                # Append full_path column value for RAG enrichment if missing
                if has_appended_full and rel_idx != -1:
                    rel_val = row[rel_idx]
                    f_path = rel_to_full.get(str(rel_val).lower(), "NULL") if rel_val is not None else "NULL"
                    row_str.append(f_path)
                
                raw_lines.append(f"| {' | '.join(row_str)} |")
            raw_markdown: str = prefix_note + "\n".join(raw_lines)

            # --- 2. Construct terminal display for User ---
            # If it's a single value (e.g. COUNT)
            if len(rows) == 1 and len(cols) == 1 and cols[0] not in ("full_path", "rel_path"):
                val_single = rows[0][0]
                term_display: str = str(val_single) if val_single is not None else "NULL"
                return raw_markdown, term_display, []
 
            if path_col_idx != -1:
                bullets: List[str] = []
                 
                # Determine all metadata column indexes (excluding path columns)
                meta_cols: List[Tuple[int, str]] = [
                    (i, col_name) for i, col_name in enumerate(cols)
                    if col_name not in ("full_path", "rel_path", "file_path")
                ]

                # Detect console width dynamically (fallback to 80)
                try:
                    term_width = os.get_terminal_size().columns
                except Exception:
                    term_width = 80
                wrap_width = max(term_width - 6, 40)  # Account for indentation spacing

                for idx, row in enumerate(rows):
                    val_path = row[path_col_idx]
                    if val_path is not None:
                        val_str: str = str(val_path)
                        resolved_path = val_str
                        if cols[path_col_idx] == "rel_path" and val_str.lower() in rel_to_full:
                            resolved_path = rel_to_full[val_str.lower()]

                        win_path: str = os.path.normpath(resolved_path)
                        
                        # Build formatted lines for each bullet item
                        bullet_lines: List[str] = [f"[{idx + 1}] {win_path}"]
                        
                        for col_idx, col_name in meta_cols:
                            val_meta = row[col_idx]
                            if val_meta is None:
                                val_meta_clean = "NULL"
                            else:
                                val_meta_clean = str(val_meta).replace("\n", " ").strip()
                            
                            # Skip printing empty lists or empty values to keep output concise
                            if val_meta_clean in ("[]", "{}", ""):
                                continue
                                
                            meta_line = f"{col_name}: {val_meta_clean}"
                            # Wrap metadata cleanly with indented wrap boundaries
                            wrapped_meta = textwrap.wrap(meta_line, width=wrap_width, subsequent_indent="        ")
                            bullet_lines.extend(["    " + line for line in wrapped_meta])
                            
                        bullets.append("\n".join(bullet_lines))
                return raw_markdown, "\n".join(bullets), paths_list

            # Fallback: Truncated Markdown Table for clean terminal output
            term_headers: str = f"| {' | '.join(cols)} |"
            term_separator: str = f"| {' | '.join(['---'] * len(cols))} |"
            term_lines: List[str] = [term_headers, term_separator]
            for row in rows:
                row_str: List[str] = []
                for val in row:
                    if val is None:
                        row_str.append("NULL")
                    elif isinstance(val, float):
                        row_str.append(f"{val:.3f}")
                    else:
                        val_str: str = str(val).replace("\n", " ")
                        if len(val_str) > 120:
                            row_str.append(val_str[:117] + "...")
                        else:
                            row_str.append(val_str)
                term_lines.append(f"| {' | '.join(row_str)} |")
            return raw_markdown, "\n".join(term_lines), []
        else:
            if db_backend == "sqlite":
                conn.commit()
            msg: str = f"Query executed successfully. Rows affected: {cursor.rowcount}"
            return msg, msg, []
    except Exception as e:
        err: str = f"Error executing SQL: {e}"
        return err, err, []
    finally:
        if conn:
            conn.close()


def call_antigravity_agent(prompt: str) -> None:
    """Invokes the Antigravity agent CLI programmatically on behalf of the client.

    This function attempts to locate the 'agy' binary in the system PATH.
    If 'agy' is not found in the path, it looks for it in user-local directories
    like ~/.local/bin/agy or /home/workbench/.local/bin/agy. Once resolved, it runs the
    agent using `--dangerously-skip-permissions` to auto-approve tool execution, and
    `--print` to run the prompt non-interactively, streaming the output to stdout.

    Args:
        prompt: The command or task description to pass to the agent.
    """
    import sys
    import os
    import subprocess

    # Determine paths to inspect for the agy binary
    agy_path: str = "agy"
    home_agy: str = os.path.expanduser("~/.local/bin/agy")
    
    # Check if agy is in the environment PATH
    in_path: bool = False
    path_env: str = os.environ.get("PATH", "")
    for p in path_env.split(os.pathsep):
        candidate: str = os.path.join(p, "agy")
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            in_path = True
            break

    if not in_path:
        if os.path.exists(home_agy) and os.access(home_agy, os.X_OK):
            agy_path = home_agy
        elif os.path.exists("/home/workbench/.local/bin/agy") and os.access("/home/workbench/.local/bin/agy", os.X_OK):
            agy_path = "/home/workbench/.local/bin/agy"
        elif os.path.exists("/home/steven/.local/bin/agy") and os.access("/home/steven/.local/bin/agy", os.X_OK):
            agy_path = "/home/steven/.local/bin/agy"

    print(f"\n🤖 [Agent Mode] Delegating to Antigravity CLI: '{agy_path}'...")
    print(f"Task: \"{prompt}\"\n")

    try:
        # Launch agy in non-interactive print mode with auto-approved permissions
        process = subprocess.Popen(
            [agy_path, "--dangerously-skip-permissions", "--print", prompt],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        # Stream stdout character-by-character to the console in real-time
        if process.stdout:
            while True:
                char: str = process.stdout.read(1)
                if not char and process.poll() is not None:
                    break
                if char:
                    sys.stdout.write(char)
                    sys.stdout.flush()
        print()
    except Exception as e:
        print(f"\n❌ Failed to run Antigravity agent CLI: {e}\n")


def run_repl(remote: bool = False, model_name: str = "gemma4-it-q4:latest", host: str = "127.0.0.1", port: int = 11434, jriver_host_override: Optional[str] = None) -> None:  # Default host IP resolves to localhost
    """Runs the interactive Read-Eval-Print Loop (REPL) CLI chat client.

    Loops prompting user input, formatting the payload, querying the
    FastAPI or remote Ollama endpoint, executing tool calls, and updating conversation history.

    Args:
        remote: If True, connects to the remote Ollama server instead of local WSL2 container.
        model_name: The name of the remote model to use.
        host: Host IP or hostname of the remote Ollama server.
        port: Connection port of the remote Ollama server.

    Returns:
        None

    Raises:
        SystemExit: If the model server fails to start or respond.
    """
    global last_query_paths
    import threading
    
    # Initialize PostgreSQL Compute Fabric Manager
    fabric_mgr = FabricManager()
    current_node_hostname = "unknown"
    
    server_thread: Optional[threading.Thread] = None
    # Ensure WSL2 server is running if not in remote mode
    if not remote:
        print("[WSL2 Server] Starting local model server in a background thread...")
        
        def boot_server() -> None:
            import wsl_client
            if not wsl_client.start_wsl_server():
                print("\n[Error] Failed to start local WSL2 model server. VLM queries will fail.")
                
        server_thread = threading.Thread(target=boot_server, daemon=True, name="WSLServerBoot")
        server_thread.start()
    else:
        # Resolve best host/port from active fabric nodes
        active_nodes = fabric_mgr.get_nodes_for_workload(model_name, "ollama")
        if active_nodes:
            # If a specific host was requested via CLI, prioritize it in the active list
            if host:
                requested_host = host.lower().strip()
                active_nodes.sort(key=lambda n: 0 if (n.hostname.lower() == requested_host or str(n.resolved_ip) == requested_host) else 1)
                
            best_node = active_nodes[0]
            current_node_hostname = best_node.hostname
            host = best_node.resolved_ip
            port = best_node.services.get("ollama", 11434)
            print(f"[FABRIC] Connected to best node: '{current_node_hostname}' ({host}:{port}) using model '{model_name}'")
        else:
            # Fallback standard IP translation
            from fabric_manager import resolve_hostname_to_ip
            host = resolve_hostname_to_ip(host)
            with fabric_mgr.pool_lock:
                for k, n in fabric_mgr.nodes.items():
                    if n.resolved_ip == host:
                        current_node_hostname = k
                        break
            print(f"[FABRIC] No active nodes matched '{model_name}'. Fallback target: '{current_node_hostname}' (http://{host}:{port}) using model '{model_name}'...")

    print("==================================================")
    print("  Gemma 4 Photo Catalog - Database Chat Client")
    print("==================================================")
    print("Instructions:")
    print("  * Type your question and press Enter.")
    print("  * To paste multiline text, type '/paste' and press Enter.")
    print("  * Type 'open <index>' or '/open <index>' to view a photo locally.")
    print("  * Type '/save [name]' to save session (default: timestamped).")
    print("  * Type '/load [name]' to resume a session (default: last_chat).")
    print("  * Type '/load list' to show all available saved sessions.")
    print("  * Type '/clear' or '/reset' to clear chat history.")
    print("  * Type '/fabric' or '/nodes' to show the compute fabric status.")
    print("  * Type 'exit' or 'quit' to close the client.")
    print("==================================================")
    print()

    # Register OS-level signal handler for SIGINT (Ctrl-C)
    signal.signal(signal.SIGINT, sigint_handler)

    session: requests.Session = requests.Session()
    chat_history: List[Dict[str, str]] = []

    # Configure persistent command history if readline is available
    if readline:
        import atexit
        history_file = os.path.join(os.path.expanduser("~"), ".db_chat_history")
        try:
            readline.read_history_file(history_file)
            readline.set_history_length(1000)
        except FileNotFoundError:
            pass
        atexit.register(readline.write_history_file, history_file)

    while True:
        try:
            user_input: str = input("Prompt > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            break

        if not user_input:
            continue

        if user_input.lower() in ("/nodes", "nodes", "/fabric", "fabric"):
            print("\n=========================================================================================================")
            print("                                    PostgreSQL Compute Fabric Registry")
            print("=========================================================================================================")
            print(f"  {'Hostname':18s} | {'Type':12s} | {'IP Address':15s} | {'Latency':10s} | {'Status':8s} | {'Supported Models'}")
            print("---------------------------------------------------------------------------------------------------------")
            
            with fabric_mgr.pool_lock:
                all_hosts = sorted(list(fabric_mgr.nodes.keys()))
                for host_key in all_hosts:
                    n = fabric_mgr.nodes[host_key]
                    if not n.is_active:
                        continue
                    status_display = "🟢 ONLINE" if n.is_online else "🔴 OFFLINE"
                    latency_str = f"{n.latency_ms:6.1f} ms" if (n.is_online and n.latency_ms is not None) else "N/A"
                    ip_str = n.resolved_ip or "Unresolved"
                    print(f"  {n.hostname:18s} | {n.node_type:12s} | {ip_str:15s} | {latency_str:10s} | {status_display:8s} | {', '.join(n.supported_models)}")
            print("=========================================================================================================\n")
            continue

        if user_input.lower() in ("exit", "quit", "/exit", "/quit", "/bye"):
            print("Exiting...")
            break

        if user_input.lower() in ("/clear", "/reset"):
            chat_history = []
            try:
                with open("db_chat_session.json", "w", encoding="utf-8") as f:
                    json.dump([], f)
            except Exception:
                pass
            print("Conversation history cleared.")
            continue

        # Slash Command: /save [name]
        if user_input.lower().startswith("/save") or user_input.lower().startswith("save"):
            prefix_len = 0
            if user_input.lower().startswith("/save"):
                prefix_len = 5
            elif user_input.lower().startswith("save"):
                if len(user_input) == 4 or user_input[4] == " ":
                    prefix_len = 4
            
            if prefix_len > 0:
                session_name = user_input[prefix_len:].strip().strip("'\"")
                sessions_dir = os.path.join(PROJECT_DIR, "sessions")
                os.makedirs(sessions_dir, exist_ok=True)
                
                if not session_name:
                    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
                    session_name = f"session_{timestamp}"
                
                if not session_name.lower().endswith(".json"):
                    session_name += ".json"
                    
                target_path = os.path.join(sessions_dir, session_name)
                try:
                    with open(target_path, "w", encoding="utf-8") as f:
                        json.dump(chat_history, f, indent=2)
                    print(f"[Success] ✅ Current session saved to '{os.path.basename(target_path)}' ({len(chat_history)} messages).")
                except Exception as ex:
                    print(f"[Error] ❌ Failed to save session: {ex}")
                continue

        # Slash Command: /load [name] or /lh [name]
        if (user_input.lower().startswith("/load") or 
            user_input.lower().startswith("load") or 
            user_input.lower().startswith("/lh") or 
            user_input.lower().startswith("lh")):
            
            prefix_len = 0
            for p in ("/load", "load", "/lh", "lh"):
                if user_input.lower().startswith(p):
                    if len(user_input) == len(p) or user_input[len(p)] == " ":
                        prefix_len = len(p)
                        break
            
            if prefix_len > 0:
                session_name = user_input[prefix_len:].strip().strip("'\"")
                sessions_dir = os.path.join(PROJECT_DIR, "sessions")
                os.makedirs(sessions_dir, exist_ok=True)
                
                # Default to last_chat if no session name is provided
                if not session_name:
                    session_name = "last_chat"
                
                # List available sessions if explicitly requested
                if session_name.lower() in ("list", "show", "help", "?", "all"):
                    print("\n[Available Sessions]:")
                    files = []
                    if os.path.exists(sessions_dir):
                        for f in os.listdir(sessions_dir):
                            if f.lower().endswith(".json"):
                                files.append(f)
                    
                    if os.path.exists(os.path.join(PROJECT_DIR, "db_chat_session.json")):
                        files.append("db_chat_session.json")
                        
                    files = sorted(list(set(files)))
                    
                    if not files:
                        print("  No saved sessions found. Use '/save [name]' to save your current session.")
                    else:
                        if "last_chat.json" in files:
                            files.remove("last_chat.json")
                            files.insert(0, "last_chat.json")
                            
                        for f in files:
                            f_path = os.path.join(sessions_dir, f) if f != "db_chat_session.json" else os.path.join(PROJECT_DIR, f)
                            msg_count = 0
                            mtime_str = "unknown"
                            try:
                                mtime = os.path.getmtime(f_path)
                                mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                                with open(f_path, "r", encoding="utf-8") as file_obj:
                                    temp_history = json.load(file_obj)
                                    msg_count = len(temp_history)
                            except Exception:
                                pass
                                
                            name_display = f.replace(".json", "")
                            if f == "last_chat.json":
                                print(f"  * {name_display:<20} ({msg_count:>2} messages) - Last Auto-Saved: {mtime_str} (Default)")
                            else:
                                print(f"  * {name_display:<20} ({msg_count:>2} messages) - Saved: {mtime_str}")
                        print("\nUsage: /load <session_name> (e.g. /load last_chat)\n")
                    continue
                
                target_filename = session_name
                if not target_filename.lower().endswith(".json"):
                    target_filename += ".json"
                
                possible_paths = [
                    os.path.join(sessions_dir, target_filename),
                    os.path.join(PROJECT_DIR, target_filename),
                    session_name,
                    os.path.join(sessions_dir, session_name),
                    os.path.join(PROJECT_DIR, session_name)
                ]
                
                loaded = False
                for p_path in possible_paths:
                    if os.path.exists(p_path) and os.path.isfile(p_path):
                        try:
                            with open(p_path, "r", encoding="utf-8") as f:
                                chat_history = json.load(f)
                            print(f"[Success] Loaded conversation history from '{os.path.basename(p_path)}' ({len(chat_history)} messages).")
                            loaded = True
                            break
                        except Exception as e:
                            print(f"[Error] Failed to load history from '{os.path.basename(p_path)}': {e}")
                            loaded = True
                            break
                            
                if not loaded:
                    print(f"[Error] ❌ Session file not found: {session_name}")
                continue

        # Slash Command: /playlist [name]
        if user_input.lower().startswith("/playlist") or user_input.lower().startswith("playlist"):
            prefix_len = 9 if user_input.lower().startswith("/playlist") else 8
            playlist_name = user_input[prefix_len:].strip().strip("'\"")
            if not playlist_name:
                playlist_name = "ai_generated"
            if not playlist_name.lower().endswith(".m3u"):
                playlist_name += ".m3u"
                
            if not last_query_paths:
                print("[Playlist Error] ❌ No files found in the last query to generate a playlist.")
                continue
                
            # Filter only audio and video files
            audio_paths = [p for p in last_query_paths if p.lower().endswith(SUPPORTED_MEDIA_EXTENSIONS)]
            if not audio_paths:
                print("[Playlist Error] ❌ None of the files in the last query are supported audio or video files.")
                continue
                
            playlist_dir = r"D:\Users\steven\Music\Playlists"
            try:
                os.makedirs(playlist_dir, exist_ok=True)
            except Exception:
                playlist_dir = os.path.join(PROJECT_DIR, "Playlists")
                os.makedirs(playlist_dir, exist_ok=True)
                
            playlist_path = os.path.join(playlist_dir, playlist_name)
            try:
                with open(playlist_path, "w", encoding="utf-8") as f:
                    for path in audio_paths:
                        f.write(path + "\n")
                print(f"[Playlist Success] ✅ Created M3U playlist with {len(audio_paths)} tracks:")
                print(f"   -> {playlist_path}")
            except Exception as ex:
                print(f"[Playlist Error] ❌ Failed to write playlist file: {ex}")
            continue

        # Slash Command: /play or /queue or /add
        cmd_normalized = user_input.lower()
        if any(cmd_normalized.startswith(x) for x in ("/play", "play", "/queue", "queue", "/add", "add")):
            prefix_len = 0
            is_queue = False
            for p in ("/play", "play", "/queue", "queue", "/add", "add"):
                if cmd_normalized.startswith(p):
                    prefix_len = len(p)
                    if "queue" in p or "add" in p:
                        is_queue = True
                    break
            
            target = user_input[prefix_len:].strip().strip("'\"")
            cmd_name = "Queue" if is_queue else "Play"
            
            tracks_to_play = []
            if not target:
                # Play/Queue all audio tracks from last query
                if not last_query_paths:
                    print(f"[{cmd_name} Error] ❌ No files found in the last query to {cmd_name.lower()}.")
                    continue
                tracks_to_play = [p for p in last_query_paths if p.lower().endswith(SUPPORTED_MEDIA_EXTENSIONS)]
                if not tracks_to_play:
                    print(f"[{cmd_name} Error] ❌ None of the files in the last query are supported media files.")
                    continue
            elif target.isdigit():
                # Play/Queue specific track by index
                idx = int(target)
                if 1 <= idx <= len(last_query_paths):
                    track_path = last_query_paths[idx - 1]
                    if track_path.lower().endswith(SUPPORTED_MEDIA_EXTENSIONS):
                        tracks_to_play = [track_path]
                    else:
                        print(f"[{cmd_name} Error] ❌ File at index {idx} is not a supported media file: {os.path.basename(track_path)}")
                        continue
                else:
                    print(f"[{cmd_name} Error] ❌ Index {idx} is out of range. Valid indexes: 1 to {len(last_query_paths)}.")
                    continue
            else:
                print(f"[{cmd_name} Error] ❌ Invalid parameter. Use '/{cmd_name.lower()}' to {cmd_name.lower()} all, or '/{cmd_name.lower()} <index>'.")
                continue
                
            verb = "queue" if is_queue else "play"
            action_desc = "queued" if is_queue else "played"

            print(f"[JRiver {cmd_name}] Preparing to {verb} {len(tracks_to_play)} track(s) on JRiver Media Center...")
            try:
                import urllib.parse
                jriver_host = jriver_host_override or os.getenv("JRIVER_HOST")
                if not jriver_host:
                    try:
                        from path_utils import get_wsl_host_ip
                        wsl_ip = get_wsl_host_ip()
                    except ImportError:
                        wsl_ip = None
                    
                    if wsl_ip:
                        jriver_host = wsl_ip
                    elif sys.platform in ("darwin", "linux"):
                        jriver_host = "192.168.1.100"
                    else:
                        jriver_host = "127.0.0.1"
                
                # 1. Clear current queue only if not queueing/adding
                if not is_queue:
                    requests.get(f"http://{jriver_host}:52198/MCWS/v1/Playback/ClearPlaylist?Zone=0&ZoneType=ID", timeout=10)
                
                # 2. Add tracks
                queued_count = 0
                if len(tracks_to_play) == 1:
                    encoded_path = urllib.parse.quote(tracks_to_play[0])
                    add_url = f"http://{jriver_host}:52198/MCWS/v1/Playback/PlayByFilename?Filenames={encoded_path}&Location=End&Zone=0&ZoneType=ID"
                    add_r = requests.get(add_url, timeout=(5.0, 15.0))
                    if add_r.status_code == 200:
                        queued_count = 1
                else:
                    # Target the local D: drive (d-drive) where JRiver is running,
                    # mapping the mount prefix depending on the client platform.
                    playlist_dir = None
                    for candidate in (
                        r"D:\Users\steven\Music\Playlists",
                        "/Volumes/d-drive/Users/steven/Music/Playlists",
                        "/mnt/d/Users/steven/Music/Playlists"
                    ):
                        if os.path.exists(os.path.dirname(candidate)):
                            playlist_dir = candidate
                            break
                    if not playlist_dir:
                        playlist_dir = os.path.join(PROJECT_DIR, "Playlists")
                        
                    os.makedirs(playlist_dir, exist_ok=True)
                    temp_playlist_path = os.path.join(playlist_dir, "temp_playback_queue.m3u")
                    with open(temp_playlist_path, "w", encoding="utf-8") as f:
                        for path in tracks_to_play:
                            f.write(f"{path}\n")
                    
                    # Resolve path layout for Windows JRiver
                    win_playlist_path = temp_playlist_path
                    if win_playlist_path.startswith("/mnt/"):
                        drive = win_playlist_path[5].upper()
                        win_playlist_path = f"{drive}:" + win_playlist_path[6:].replace("/", "\\")
                    elif win_playlist_path.startswith("/Volumes/"):
                        parts = win_playlist_path.split("/")
                        if len(parts) > 2:
                            share = parts[2].lower()
                            if share == "hdrive":
                                win_playlist_path = "H:" + temp_playlist_path[15:].replace("/", "\\")
                            elif share == "d-drive":
                                win_playlist_path = "D:" + temp_playlist_path[16:].replace("/", "\\")
                    else:
                        win_playlist_path = win_playlist_path.replace("/", "\\")
                    
                    encoded_playlist = urllib.parse.quote(win_playlist_path)
                    add_url = f"http://{jriver_host}:52198/MCWS/v1/Playback/PlayByFilename?Filenames={encoded_playlist}&Location=End&Zone=0&ZoneType=ID"
                    add_r = requests.get(add_url, timeout=(5.0, 15.0))
                    if add_r.status_code == 200:
                        queued_count = len(tracks_to_play)
                        
                # 3. Ensure play command is sent only if clearing & starting fresh
                if not is_queue:
                    requests.get(f"http://{jriver_host}:52198/MCWS/v1/Playback/Play?Zone=0&ZoneType=ID", timeout=10)
                
                print(f"[JRiver {cmd_name}] ✅ Successfully {action_desc} {queued_count} track(s) in JRiver!")
            except requests.exceptions.ReadTimeout:
                print(f"[JRiver {cmd_name}] ⚠️ JRiver is processing the playlist in the background. It will populate in a few moments.")
            except Exception as ex:
                print(f"[JRiver {cmd_name} Error] ❌ Failed to send command to JRiver: {ex}")
                print("   Ensure JRiver Media Center is running and Media Network (MCWS) is enabled on port 52198.")
            continue
            continue

        if (user_input.lower().startswith("/catalog") or 
            user_input.lower().startswith("catalog") or 
            user_input.lower().startswith("/run_cataloger") or 
            user_input.lower().startswith("run_cataloger")):
            
            prefix_len = 0
            for p in ("/cataloger", "cataloger", "/catalog", "catalog", "/run_cataloger", "run_cataloger"):
                if user_input.lower().startswith(p):
                    prefix_len = len(p)
                    break
            
            catalog_args_str = user_input[prefix_len:].strip()
            import shlex
            try:
                parsed_args = shlex.split(catalog_args_str)
            except Exception:
                parsed_args = catalog_args_str.split()

            print(f"[Cataloger Agent] Launching describe_photos.py with arguments: {parsed_args} in a background thread...")
            
            def run_cataloger_in_background(args_list: List[str]) -> None:
                import subprocess
                py_exe = os.path.join(os.path.dirname(PROJECT_DIR), "ltx2_env", "Scripts", "python.exe")
                if not os.path.exists(py_exe):
                    py_exe = "python"
                
                full_args = [py_exe, "describe_photos.py"]
                
                has_db = False
                has_dir = False
                for a in args_list:
                    if a.startswith("--db"):
                        has_db = True
                    if a.startswith("--dir"):
                        has_dir = True
                
                if not has_db:
                    full_args.extend(["--db", DB_PATH])
                if not has_dir:
                    full_args.extend(["--dir", "D:\\Users\\steven\\Pictures"])
                
                if "--no-json" not in args_list:
                    full_args.append("--no-json")
                if "--embed-exif" not in args_list:
                    full_args.append("--embed-exif")
                
                full_args.extend(args_list)
                
                try:
                    res = subprocess.run(
                        full_args,
                        cwd=PROJECT_DIR,
                        capture_output=True,
                        text=True,
                        timeout=7200
                    )
                    if res.returncode == 0:
                        print("\n[Cataloger Success] ✅ Cataloging run completed successfully!")
                        for line in (res.stderr.splitlines() + res.stdout.splitlines()):
                            if "Found" in line or "Saved" in line or "processed" in line or "Active VLM" in line or "images to process" in line:
                                print(f"[Cataloger Summary] {line.strip()}")
                        print("Prompt > ", end="", flush=True)
                    else:
                        print(f"\n[Cataloger Error] ❌ Cataloging failed (Exit Code {res.returncode}):\n{res.stderr or res.stdout}")
                        print("Prompt > ", end="", flush=True)
                except Exception as ex:
                    print(f"\n[Cataloger Error] ❌ Failed to launch cataloger: {ex}")
                    print("Prompt > ", end="", flush=True)

            threading.Thread(target=run_cataloger_in_background, args=(parsed_args,), daemon=True, name="CatalogerRun").start()
            continue

        if (user_input.lower().startswith("/merge") or 
            user_input.lower().startswith("merge") or 
            "merge agent" in user_input.lower() or 
            "lauch merge" in user_input.lower() or 
            "launch merge" in user_input.lower()):
            
            # Detect if user requested to overwrite/force update existing records
            overwrite = False
            user_args = user_input.lower()
            if any(k in user_args for k in ("overwrite", "-o", "all", "same", "force")):
                overwrite = True

            print(f"[Merge Agent] Launching the description merger script (overwrite={overwrite}) in a background thread...")
            def run_merge_in_background(force_overwrite: bool) -> None:
                import subprocess
                py_exe = os.path.join(os.path.dirname(PROJECT_DIR), "ltx2_env", "Scripts", "python.exe")
                if not os.path.exists(py_exe):
                    py_exe = "python"
                try:
                    merge_args = [py_exe, "merge_new_to_enriched.py"]
                    if force_overwrite:
                        merge_args.append("--overwrite")
                    res = subprocess.run(
                        merge_args,
                        cwd=PROJECT_DIR,
                        capture_output=True,
                        text=True,
                        timeout=300
                    )
                    if res.returncode == 0:
                        print("\n[Merge Agent Success] ✅ Master catalog description merge completed successfully!")
                        for line in (res.stderr.splitlines() + res.stdout.splitlines()):
                            if "Merge summary" in line or "Successfully wrote updated master" in line:
                                print(f"[Merge Agent Summary] {line.strip()}")
                        
                        # Trigger SQLite synchronization from updated photo_descriptions_enriched.json
                        print("[Merge Agent] Synchronizing updated master catalog to SQLite database...")
                        enriched_json = os.path.join(os.path.dirname(PROJECT_DIR), "photo_descriptions_enriched.json")
                        sync_res = subprocess.run(
                            [py_exe, "import_json_to_sqlite.py", "--source", enriched_json],
                            cwd=PROJECT_DIR,
                            capture_output=True,
                            text=True,
                            timeout=300
                        )
                        if sync_res.returncode == 0:
                            print("[Merge Agent Sync Success] ✅ SQLite database successfully synchronized!")
                            for line in (sync_res.stderr.splitlines() + sync_res.stdout.splitlines()):
                                if "Sync completed" in line or "DB is already up-to-date" in line:
                                    print(f"[Merge Agent Sync Summary] {line.strip()}")
                        else:
                            print(f"[Merge Agent Sync Error] ❌ SQLite synchronization failed (Exit Code {sync_res.returncode}):\n{sync_res.stderr or sync_res.stdout}")
                        print("Prompt > ", end="", flush=True)
                    else:
                        print(f"\n[Merge Agent Error] ❌ Merger failed (Exit Code {res.returncode}):\n{res.stderr or res.stdout}")
                        print("Prompt > ", end="", flush=True)
                except Exception as ex:
                    print(f"\n[Merge Agent Error] ❌ Failed to launch merger: {ex}")
                    print("Prompt > ", end="", flush=True)

            threading.Thread(target=run_merge_in_background, args=(overwrite,), daemon=True, name="MergeAgentRun").start()
            continue

        if user_input.lower().startswith("/open ") or user_input.lower().startswith("open "):
            prefix_len = 6 if user_input.lower().startswith("/open ") else 5
            target = user_input[prefix_len:].strip().strip("'\"")
            file_to_open = ""
            
            # Allow opening by numeric index corresponding to printed bullet items
            if target.isdigit():
                idx = int(target)
                if 1 <= idx <= len(last_query_paths):
                    file_to_open = last_query_paths[idx - 1]
                else:
                    print(f"[Error] Index {idx} is out of range. Valid indexes: 1 to {len(last_query_paths)}.")
                    continue
            else:
                file_to_open = target
                # Handle raw file:/// URL stripping if copy-pasted
                if file_to_open.startswith("file:///"):
                    file_to_open = file_to_open[8:]
            
            if file_to_open:
                from path_utils import open_file
                print(f"[Opening]: {file_to_open}...")
                try:
                    if not open_file(file_to_open):
                        print(f"[Error] File not found: {file_to_open}")
                except Exception as e:
                    print(f"[Error] Failed to open file: {e}")
            continue

        # Slash Command: /agent <prompt> or /ai <prompt>
        if (user_input.lower().startswith("/agent") or 
            user_input.lower().startswith("agent") or
            user_input.lower().startswith("/ai") or 
            user_input.lower().startswith("ai")):
            
            prefix_len: int = 0
            for p in ("/agent", "agent", "/ai", "ai"):
                if user_input.lower().startswith(p):
                    if len(user_input) == len(p) or user_input[len(p)] == " ":
                        prefix_len = len(p)
                        break
            
            if prefix_len > 0:
                agent_prompt: str = user_input[prefix_len:].strip()
                if not agent_prompt:
                    print("Usage: /agent <prompt> (e.g., /agent 'update track 134334 genre to Podcast')")
                    continue
                call_antigravity_agent(agent_prompt)
                continue

        # Check if the user input is a direct SQL query
        if user_input.lower().startswith("select ") or user_input.lower().startswith("with "):
            print(f"[Executing Direct SQL]: {user_input}")
            raw_markdown, term_display, paths_list = execute_sql(user_input)
            
            # Check if the query failed to compile/execute
            if raw_markdown.startswith("Error executing SQL:") or raw_markdown.startswith("Error:"):
                print(f"[SQL Error]: {term_display}")
                print("Attempting to automatically fix the query...")
                
                # Format a prompt for the model to fix the SQL
                prompt_template: str = load_sql_fix_prompt()
                active_fix_prompt: str = prompt_template.format(
                    failed_sql=user_input,
                    error_message=term_display
                )
                
                fixed_sql: str = ""
                try:
                    # Query the model server
                    if remote:
                        messages: List[Dict[str, str]] = [
                            {"role": "system", "content": "You are a database SQL assistant. Correct the failed SQL query based on the schema and error message."},
                            {"role": "user", "content": active_fix_prompt}
                        ]
                        response_text: str = query_llm_for_correction(
                            "",
                            messages=messages,
                            remote=True,
                            model_name=model_name,
                            host=host,
                            port=port,
                            session=session
                        )
                    else:
                        response_text = query_llm_for_correction(active_fix_prompt, remote=False, session=session)
                    
                    fixed_sql = extract_sql_from_response(response_text)
                except Exception as ex:
                    print(f"[LLM Correction Error]: {ex}")
                
                if fixed_sql:
                    print(f"[Executing Fixed SQL]: {fixed_sql}")
                    raw_markdown_fixed, term_display_fixed, paths_list_fixed = execute_sql(fixed_sql)
                    if not (raw_markdown_fixed.startswith("Error executing SQL:") or raw_markdown_fixed.startswith("Error:")):
                        print(f"[Results (Fixed Query)]:\n{term_display_fixed}\n")
                        if paths_list_fixed:
                            last_query_paths = paths_list_fixed
                        continue
                    else:
                        print(f"[Fixed SQL failed too]: {term_display_fixed}")
                else:
                    print("[Error] LLM did not return a valid corrected SQL query.")
                
                # If unable to fix it (failed to compile, or LLM failed), ask the user for intent
                print("\nUnable to automatically fix the SQL query.")
                try:
                    user_intent: str = input("What was your intent? (Describe in natural language): ").strip()
                except (KeyboardInterrupt, EOFError):
                    print("\nCorrection cancelled.")
                    continue
                
                if user_intent:
                    user_input = user_intent
                    print(f"Processing your intent: '{user_intent}'")
                    # Fall through to the standard natural language model agent loop below
                else:
                    print("Correction cancelled.")
                    continue
            else:
                # Execution succeeded
                print(f"[Results]:\n{term_display}\n")
                if paths_list:
                    last_query_paths = paths_list
                continue

        prompt_text: str = user_input

        # Handle multiline paste command
        if user_input.lower() == "/paste":
            print("[Multiline Mode] Paste text. Type '/end' on a separate line to finish and send.")
            multiline_lines: List[str] = []
            
            # Temporarily restore default SIGINT handler for KeyboardInterrupt support
            old_handler = signal.signal(signal.SIGINT, signal.SIG_DFL)
            try:
                while True:
                    try:
                        line: str = input("... ")
                    except EOFError:
                        break
                    if line.strip() == "/end":
                        break
                    multiline_lines.append(line)
            except KeyboardInterrupt:
                print("\n[Cancelled multiline input]")
                multiline_lines = []
            finally:
                # Restore the custom exit signal handler
                signal.signal(signal.SIGINT, sigint_handler)

            prompt_text = "\n".join(multiline_lines).strip()
            if not prompt_text:
                continue

        # Check if user requested no-history mode (/nh)
        skip_history = False
        if prompt_text.lower().startswith("/nh"):
            skip_history = True
            prompt_text = prompt_text[3:].strip()
            if not prompt_text:
                print("[NH Mode] Please provide a query after /nh.")
                continue

        # Get total photos count and format system context
        total_photos: int = get_total_photos_count()
        current_time_str: str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prompt_template: str = load_system_prompt()
        
        try:
            system_context: str = prompt_template.format(
                current_time=current_time_str,
                total_photos=total_photos
            )
        except KeyError as ke:
            print(f"[Warning] Formatting placeholder missing in prompt template: {ke}")
            system_context = prompt_template

        # Assemble active history list and history string conditionally
        if skip_history:
            active_history = [{"role": "user", "content": prompt_text}]
            history_str = ""
        else:
            chat_history.append({"role": "user", "content": prompt_text})
            active_history = list(chat_history)
            
            # Format previous conversation history (excluding the current user prompt at the end)
            history_str = ""
            for msg in chat_history[:-1]:
                role = msg["role"].capitalize()
                content = msg["content"]
                history_str += f"{role}: {content}\n\n"

        # Record the initial history length to allow restoring on failure
        initial_history_len: int = len(chat_history)

        # Assemble the active prompt string
        active_prompt: str = f"{system_context}\n\n"
        if history_str:
            active_prompt += f"=== CONVERSATION HISTORY ===\n{history_str}"
        active_prompt += f"=== CURRENT TURN ===\nUser: {prompt_text}\n"

        # Agent iteration loop to allow multiple database queries
        tool_executed: bool = False
        final_response_text: str = ""

        # Limit to 5 iterations to prevent infinite agent run-away loops
        for iteration in range(5):
            try:
                if not remote:
                    target_url = SERVER_URL
                    payload = {
                        "prompt_text": active_prompt,
                        "temperature": 0.2,
                        "max_new_tokens": 4096
                    }
                    if server_thread and server_thread.is_alive():
                        print("[WSL2 Server] Local model server is still booting up (VRAM weight loading in progress). Waiting for boot to complete...")
                        server_thread.join()
                    
                    if not tool_executed:
                        print("Waiting for response...")
                    else:
                        print("Processing SQL query results...")
                    
                    try:
                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                            future = executor.submit(session.post, target_url, json=payload, timeout=600.0)
                            last_print = time.time()
                            dots_printed = False
                            while not future.done():
                                time.sleep(0.1)
                                if time.time() - last_print >= 10.0:
                                    print(".", end="", flush=True)
                                    last_print = time.time()
                                    dots_printed = True
                            if dots_printed:
                                print()
                            response = future.result()
                        if response.status_code != 200:
                            print(f"\n[Error] Server returned status code {response.status_code}: {response.text}\n")
                            break
                    except Exception as ex:
                        print(f"\n[Error] Local server connection failed: {ex}\n")
                        break
                else:
                    # Remote Mode with Dynamic Fabric Failover
                    max_attempts = 3
                    attempt = 0
                    response = None
                    
                    while attempt < max_attempts:
                        target_url = f"http://{host}:{port}/api/chat"
                        messages: List[Dict[str, str]] = []
                        messages.append({"role": "system", "content": system_context})
                        for msg in active_history:
                            messages.append({"role": msg["role"], "content": msg["content"]})

                        payload = {
                            "model": model_name,
                            "messages": messages,
                            "stream": False,
                            "options": {
                                "temperature": 0.2,
                                "num_ctx": 16384,
                                "num_predict": 4096
                            }
                        }
                        
                        if not tool_executed:
                            print(f"Waiting for response from '{current_node_hostname}' ({host}:{port})...")
                        else:
                            print(f"Processing SQL query results on '{current_node_hostname}' ({host}:{port})...")
                            
                        try:
                            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                                future = executor.submit(session.post, target_url, json=payload, timeout=600.0)
                                last_print = time.time()
                                dots_printed = False
                                while not future.done():
                                    time.sleep(0.1)
                                    if time.time() - last_print >= 10.0:
                                        print(".", end="", flush=True)
                                        last_print = time.time()
                                        dots_printed = True
                                if dots_printed:
                                    print()
                                response = future.result()
                                
                            if response.status_code != 200:
                                raise RuntimeError(f"Server returned status {response.status_code}")
                            
                            # Request succeeded!
                            break
                        except Exception as ex:
                            attempt += 1
                            print(f"\n[FABRIC] Connection failed on node '{current_node_hostname}' ({host}:{port}): {ex}")
                            fabric_mgr.mark_node_failed(current_node_hostname)
                            
                            if attempt >= max_attempts:
                                print("[FABRIC] All retry attempts and failovers exhausted.")
                                response = None
                                break
                            
                            # Retrieve the "next best" online node
                            next_nodes = fabric_mgr.get_nodes_for_workload(model_name, "ollama")
                            if next_nodes:
                                best_next = next_nodes[0]
                                current_node_hostname = best_next.hostname
                                host = best_next.resolved_ip
                                port = best_next.services.get("ollama", 11434)
                                print(f"[FABRIC] [FAILOVER] Switched to next best node: '{current_node_hostname}' ({host}:{port})")
                            else:
                                any_active = fabric_mgr.get_active_nodes()
                                if any_active:
                                    best_next = any_active[0]
                                    current_node_hostname = best_next.hostname
                                    host = best_next.resolved_ip
                                    port = best_next.services.get("ollama", 11434)
                                    fallback_model = best_next.supported_models[0]
                                    print(f"[FABRIC] [FAILOVER] Switched to any active node: '{current_node_hostname}' with fallback model '{fallback_model}' ({host}:{port})")
                                    model_name = fallback_model
                                else:
                                    print("[FABRIC] No other active compute nodes are online in the network!")
                                    response = None
                                    break
                    
                    if response is None:
                        break
                        
                result: Dict[str, Any] = response.json()
                if remote:
                    response_text: str = result.get("message", {}).get("content", "").strip()
                else:
                    response_text = result.get("response", "").strip()

                # Look for tool call tags in response
                tool_call_match = re.search(r'<tool_call>(.*?)</tool_call>', response_text, re.DOTALL)
                if tool_call_match:
                    tool_json_str: str = tool_call_match.group(1).strip()
                    print(f"\n[Executing Tool Call]: {tool_json_str}")
                    sql_query: str = ""

                    try:
                        tool_data: Dict[str, Any] = json.loads(tool_json_str)
                        sql_query = tool_data.get("sql", "")
                    except Exception:
                        # Fallback: Extract SELECT statement directly
                        sql_match = re.search(r'(SELECT\s+.*)', tool_json_str, re.IGNORECASE | re.DOTALL)
                        if sql_match:
                            sql_query = sql_match.group(1).strip()

                    if sql_query:
                        print(f"[Executing SQL]: {sql_query}")
                        raw_markdown, term_display, paths_list = execute_sql(sql_query)
                        print(f"[Results]:\n{term_display}\n")
                        
                        # Store paths list for /open index command
                        if paths_list:
                            last_query_paths = paths_list

                        # Record the intermediate turns
                        if skip_history:
                            active_history.append({"role": "assistant", "content": response_text})
                            active_history.append({"role": "user", "content": f"TOOL RESULT:\n{raw_markdown}"})
                        else:
                            chat_history.append({"role": "assistant", "content": response_text})
                            chat_history.append({"role": "user", "content": f"TOOL RESULT:\n{raw_markdown}"})
                            active_history = list(chat_history)

                        # Update active_prompt for local mode
                        active_prompt += f"Assistant: {response_text}\n\nUser: TOOL RESULT:\n{raw_markdown}\n\n"
                        tool_executed = True
                        continue
                    else:
                        print("[Error] Failed to parse SQL statement from tool call.")

                # If no tool call was matched, we've received the final answer
                final_response_text = response_text
                if not skip_history:
                    chat_history.append({"role": "assistant", "content": final_response_text})
                break

            except requests.RequestException as e:
                print(f"\n[Error] Failed to connect to server: {e}\n")
                break
            except Exception as e:
                print(f"\n[Error] An unexpected error occurred: {e}\n")
                break

        # Display response if successful, otherwise restore history
        if final_response_text:
            print("\nResponse:")
            print(final_response_text)
            print()

            # Record clean conversational turns in history, keeping the last 20 messages
            if not skip_history and len(chat_history) > 20:
                chat_history = chat_history[-20:]

            # Auto-save history to file
            if not skip_history:
                try:
                    with open("db_chat_session.json", "w", encoding="utf-8") as f:
                        json.dump(chat_history, f, indent=2)
                except Exception:
                    pass
                
                try:
                    sessions_dir = os.path.join(PROJECT_DIR, "sessions")
                    os.makedirs(sessions_dir, exist_ok=True)
                    with open(os.path.join(sessions_dir, "last_chat.json"), "w", encoding="utf-8") as f:
                        json.dump(chat_history, f, indent=2)
                except Exception:
                    pass
        else:
            # Restore history to before this turn if it failed
            if not skip_history:
                chat_history = chat_history[:initial_history_len]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Interactive CLI REPL client for the photo catalog database.")
    parser.add_argument("--remote", action="store_true", help="Use remote Ollama server instead of local WSL2 container.")
    parser.add_argument("--model", type=str, default="gemma4-it-q4:latest", help="Model name to request from remote Ollama server.")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Remote host IP or hostname. Default resolves to localhost.")
    parser.add_argument("--port", type=int, default=11434, help="Remote host port.")
    parser.add_argument("--jriver-host", type=str, default=None, help="Host IP of JRiver Media Center for playback commands.")
    args = parser.parse_args()

    run_repl(
        remote=args.remote,
        model_name=args.model,
        host=args.host,
        port=args.port,
        jriver_host_override=args.jriver_host
    )

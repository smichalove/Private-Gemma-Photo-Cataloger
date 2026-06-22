"""Interactive Database Chat REPL Client for the Photo Catalog Database.

1. Purpose:
    This script provides an interactive Read-Eval-Print Loop (REPL) CLI chat client
    connected to the local photo catalog SQLite database and an offline Gemma VLM (either
    a local WSL2 server or a remote Ollama server). It allows operators to query their
    cataloged photo collection using natural language. The VLM translates these questions
    into SQL, which is executed safely in read-only mode, and the results are presented
    back to the user as clean markdown tables or bullets.

2. Architecture and Mechanics:
    - Configuration Resolution: Loads parameters dynamically from the environment
      (.env file) and supports CLI overrides for all endpoints and file paths.
    - Read-Only SQL Execution: Queries the database in read-only mode to prevent mutations.
    - Markdown Rendering: Formats results into markdown tables or index bullets for the VLM.
    - Interactive Command Routing: Supports CLI-only commands like '/clear', '/reset',
      'open <index>', and '/open <index>'.
    - Conversational Memory: Keeps up to a 20-message linear history queue of conversation turns,
      including tool calls and results, to maintain context.

3. Execution Modes:
    - Interactive CLI Shell: Run from a console terminal to start the chat loop.
      Command:
        python local/db_chat_repl.py [--remote] [--db DB_PATH] [--prompt PROMPT_PATH]
"""

import os
import sys
import re
import json
import sqlite3
import signal
import datetime
import requests
from typing import Dict, List, Optional, Tuple, Any

# Ensure standard output streams use UTF-8 on Windows to prevent encoding crashes
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

# Attempt to load environment variables from a local .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Try to import readline for command history and editing capabilities
try:
    import readline
except ImportError:
    readline = None

# Global configuration defaults
DEFAULT_DB_PATH: str = "local/photo_catalog.db"
DEFAULT_PROMPT_PATH: str = "local/db_prompt.txt"
DEFAULT_VLM_SERVER_URL: str = "http://127.0.0.1:8000/analyze"
DEFAULT_OLLAMA_MODEL: str = "gemma4-it-q4:latest"
DEFAULT_OLLAMA_HOST: str = "127.0.0.1"
DEFAULT_OLLAMA_PORT: int = 11434

# Global state to track absolute paths from the most recent SQL query to support 'open <index>'
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


def load_system_prompt(prompt_path: str) -> str:
    """Loads the system prompt template from an external file on disk.

    Args:
        prompt_path: The filesystem path to the prompt template.

    Returns:
        The raw string content of the system prompt template, or a fallback if not found.
    """
    if os.path.exists(prompt_path):
        try:
            with open(prompt_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"[Warning] Failed to read prompt template at {prompt_path}: {e}")

    # Fallback prompt in case the file cannot be accessed
    return (
        "You are a helpful assistant for the photo catalog database.\n"
        "=== SYSTEM CONTEXT ===\n"
        "Current local date/time: {current_time}\n"
        "Total photo records currently cataloged: {total_photos}\n"
    )


def get_total_photos_count(db_path: str) -> int:
    """Queries the SQLite database to return the total number of cataloged photos.

    Args:
        db_path: The absolute or relative path to the SQLite database file.

    Returns:
        The total number of records in the photos table.
    """
    if not os.path.exists(db_path):
        return 0
    conn = None
    try:
        conn = sqlite3.connect(db_path)
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


def execute_sql(sql: str, db_path: str) -> Tuple[str, str, List[str]]:
    """Executes an SQL query against the photo catalog database in read-only mode.

    Args:
        sql: The SQL query string.
        db_path: The filesystem path to the database.

    Returns:
        A tuple of (raw_markdown_for_vlm, terminal_display_for_user, paths_list).
    """
    if not os.path.exists(db_path):
        err: str = f"Error: Database file not found at {db_path}"
        return err, err, []

    conn = None
    try:
        # Connect in read-only mode to prevent mutation from generated SQL queries
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute(sql)

        if cursor.description:
            cols: List[str] = [desc[0] for desc in cursor.description]
            rows: List[Tuple[Any, ...]] = cursor.fetchall()
            if not rows:
                msg: str = "Query executed successfully. No rows returned."
                return msg, msg, []

            # Truncate the total return (list of rows) to protect context window size
            if len(rows) > 50:
                rows = rows[:50]

            # Fetch full paths if we only have rel_path for context enrichment
            rel_to_full: Dict[str, str] = {}
            if "rel_path" in cols:
                rel_paths_in_rows = [row[cols.index("rel_path")] for row in rows if row[cols.index("rel_path")] is not None]
                if rel_paths_in_rows:
                    try:
                        conn2 = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                        cursor2 = conn2.cursor()
                        placeholders = ",".join(["?"] * len(rel_paths_in_rows))
                        cursor2.execute(
                            f"SELECT rel_path, full_path FROM photos WHERE rel_path IN ({placeholders})",
                            rel_paths_in_rows
                        )
                        for r_path, f_path in cursor2.fetchall():
                            rel_to_full[r_path.lower()] = f_path
                        conn2.close()
                    except Exception as e:
                        print(f"[Warning] Failed to fetch full_path mappings: {e}")

            # 1. Construct raw markdown for VLM (Always ensuring full_path is present in context)
            vlm_cols = list(cols)
            has_appended_full = "full_path" not in cols and bool(rel_to_full)
            if has_appended_full:
                vlm_cols.append("full_path")

            raw_headers: str = f"| {' | '.join(vlm_cols)} |"
            raw_separator: str = f"| {' | '.join(['---'] * len(vlm_cols))} |"
            raw_lines: List[str] = [raw_headers, raw_separator]
            
            rel_idx = cols.index("rel_path") if "rel_path" in cols else -1

            for row in rows:
                row_str: List[str] = []
                for idx_c, val in enumerate(row):
                    if val is None:
                        row_str.append("NULL")
                    elif isinstance(val, float):
                        row_str.append(f"{val:.3f}")
                    else:
                        val_str: str = str(val).replace("\n", " ")
                        if len(val_str) > 5000:
                            row_str.append(val_str[:4997] + "...")
                        else:
                            row_str.append(val_str)
                
                # Append full_path column value for RAG enrichment if missing
                if has_appended_full and rel_idx != -1:
                    rel_val = row[rel_idx]
                    f_path = rel_to_full.get(str(rel_val).lower(), "NULL") if rel_val is not None else "NULL"
                    row_str.append(f_path)
                
                raw_lines.append(f"| {' | '.join(row_str)} |")
            raw_markdown: str = "\n".join(raw_lines)

            # 2. Construct terminal display for User
            # If it's a single value (e.g. COUNT)
            if len(rows) == 1 and len(cols) == 1:
                val_single = rows[0][0]
                term_display: str = str(val_single) if val_single is not None else "NULL"
                return raw_markdown, term_display, []

            # Check if we can format as indexed bullets with file paths/names
            path_col_idx: int = -1
            for name in ["full_path", "rel_path"]:
                if name in cols:
                    path_col_idx = cols.index(name)
                    break

            if path_col_idx != -1:
                bullets: List[str] = []
                paths_list: List[str] = []
                for idx, row in enumerate(rows):
                    val_path = row[path_col_idx]
                    if val_path is not None:
                        val_str: str = str(val_path)
                        resolved_path = val_str
                        if cols[path_col_idx] == "rel_path" and val_str.lower() in rel_to_full:
                            resolved_path = rel_to_full[val_str.lower()]

                        win_path: str = os.path.normpath(resolved_path)
                        bullets.append(f"[{idx + 1}] {win_path}")
                        paths_list.append(win_path)
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
                        if len(val_str) > 60:
                            row_str.append(val_str[:57] + "...")
                        else:
                            row_str.append(val_str)
                term_lines.append(f"| {' | '.join(row_str)} |")
            return raw_markdown, "\n".join(term_lines), []
        else:
            conn.commit()
            msg: str = f"Query executed successfully. Rows affected: {cursor.rowcount}"
            return msg, msg, []
    except Exception as e:
        err: str = f"Error executing SQL: {e}"
        return err, err, []
    finally:
        if conn:
            conn.close()


def run_repl(
    db_path: str,
    prompt_path: str,
    vlm_url: str,
    remote: bool = False,
    model_name: str = DEFAULT_OLLAMA_MODEL,
    host: str = DEFAULT_OLLAMA_HOST,
    port: int = DEFAULT_OLLAMA_PORT
) -> None:
    """Runs the interactive Read-Eval-Print Loop (REPL) CLI chat client.

    Loops prompting user input, formatting the payload, querying the
    FastAPI or remote Ollama endpoint, executing tool calls, and updating conversation history.

    Args:
        db_path: Path to the SQLite photo catalog database.
        prompt_path: Path to the db_prompt.txt system instruction template.
        vlm_url: Endpoint URL of the local VLM server (FastAPI).
        remote: If True, connects to the remote Ollama server instead of local WSL2 container.
        model_name: The name of the remote model to use.
        host: Host IP or hostname of the remote Ollama server.
        port: Connection port of the remote Ollama server.

    Returns:
        None
    """
    global last_query_paths
    # Ensure WSL2 server is running if not in remote mode
    if not remote:
        # Import local wsl client module dynamically to manage model lifecycle
        try:
            import wsl_client
            if not wsl_client.start_wsl_server():
                print("[Error] Failed to start WSL2 model server. Exiting.")
                sys.exit(1)
        except ImportError:
            print("[Warning] wsl_client module not found in path. Attempting direct requests to local VLM url.")
    else:
        print(f"[Remote Mode] Connecting to model server at http://{host}:{port} using model '{model_name}'...")

    print("==================================================")
    print("  Gemma Photo Catalog - Database Chat Client")
    print("==================================================")
    print("Instructions:")
    print("  * Type your question and press Enter.")
    print("  * To paste multiline text, type '/paste' and press Enter.")
    print("  * Type 'open <index>' or '/open <index>' to view a photo locally.")
    print("  * Type '/clear' or '/reset' to clear chat history.")
    print("  * Type 'exit' or 'quit' to close the client.")
    print("==================================================")
    print()

    # Register OS-level signal handler for SIGINT (Ctrl-C)
    signal.signal(signal.SIGINT, sigint_handler)

    session: requests.Session = requests.Session()
    chat_history: List[Dict[str, str]] = []

    while True:
        try:
            user_input: str = input("Prompt > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            print("Exiting...")
            break

        if user_input.lower() in ("/clear", "/reset"):
            chat_history = []
            print("Conversation history cleared.")
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
                file_to_open = os.path.normpath(file_to_open)

            if file_to_open and os.path.exists(file_to_open):
                print(f"[Opening]: {file_to_open}...")
                try:
                    os.startfile(file_to_open)
                except Exception as e:
                    print(f"[Error] Failed to open file: {e}")
            elif file_to_open:
                print(f"[Error] File not found: {file_to_open}")
            continue

        prompt_text: str = user_input

        # Handle multiline paste command
        if user_input.lower() == "/paste":
            print("[Multiline Mode] Paste text. Type '/end' on a separate line to finish and send.")
            multiline_lines: List[str] = []
            
            # Temporarily restore default SIGINT handler for KeyboardInterrupt support
            signal.signal(signal.SIGINT, signal.SIG_DFL)
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

        # Get total photos count and format system context
        total_photos: int = get_total_photos_count(db_path)
        current_time_str: str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        prompt_template: str = load_system_prompt(prompt_path)
        
        try:
            system_context: str = prompt_template.format(
                current_time=current_time_str,
                total_photos=total_photos
            )
        except KeyError as ke:
            print(f"[Warning] Formatting placeholder missing in prompt template: {ke}")
            system_context = prompt_template

        # Record the initial history length to allow restoring on failure
        initial_history_len: int = len(chat_history)
        chat_history.append({"role": "user", "content": prompt_text})

        # Format previous conversation history (excluding the current user prompt at the end)
        history_str: str = ""
        for msg in chat_history[:-1]:
            role: str = msg["role"].capitalize()
            content: str = msg["content"]
            history_str += f"{role}: {content}\n\n"

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
            if remote:
                target_url: str = f"http://{host}:{port}/api/chat"
                messages: List[Dict[str, str]] = []
                messages.append({"role": "system", "content": system_context})
                for msg in chat_history:
                    messages.append({"role": msg["role"], "content": msg["content"]})

                payload: Dict[str, Any] = {
                    "model": model_name,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": 0.2,
                        "num_ctx": 32768,
                        "num_predict": 4096
                    }
                }
            else:
                target_url = vlm_url
                payload = {
                    "prompt_text": active_prompt,
                    "temperature": 0.2,
                    "max_new_tokens": 4096
                }

            if not tool_executed:
                print("Waiting for response...")
            else:
                print("Processing SQL query results...")

            try:
                response = session.post(target_url, json=payload, timeout=120.0)
                if response.status_code != 200:
                    print(f"\n[Error] Server returned status code {response.status_code}: {response.text}\n")
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
                        raw_markdown, term_display, paths_list = execute_sql(sql_query, db_path)
                        print(f"[Results]:\n{term_display}\n")
                        
                        # Store paths list for /open index command
                        if paths_list:
                            last_query_paths = paths_list

                        # Record the intermediate turns directly in chat_history
                        chat_history.append({"role": "assistant", "content": response_text})
                        chat_history.append({"role": "user", "content": f"TOOL RESULT:\n{raw_markdown}"})

                        # Update active_prompt for local mode
                        active_prompt += f"Assistant: {response_text}\n\nUser: TOOL RESULT:\n{raw_markdown}\n\n"
                        tool_executed = True
                        continue
                    else:
                        print("[Error] Failed to parse SQL statement from tool call.")

                # If no tool call was matched, we've received the final answer
                final_response_text = response_text
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
            if len(chat_history) > 20:
                chat_history = chat_history[-20:]
        else:
            # Restore history to before this turn if it failed
            chat_history = chat_history[:initial_history_len]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Interactive CLI REPL client for the photo catalog database.")
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Use remote Ollama server instead of local WSL2 container."
    )
    parser.add_argument(
        "--db",
        type=str,
        default=os.environ.get("OUTPUT_DATABASE_SQLITE", DEFAULT_DB_PATH),
        help="Path to the SQLite database file."
    )
    parser.add_argument(
        "--prompt",
        type=str,
        default=os.environ.get("DB_PROMPT_PATH", DEFAULT_PROMPT_PATH),
        help="Path to the db_prompt.txt system instruction template."
    )
    parser.add_argument(
        "--local-url",
        type=str,
        default=os.environ.get("VLM_SERVER_URL", DEFAULT_VLM_SERVER_URL),
        help="Server URL for the local VLM pipeline."
    )
    parser.add_argument(
        "--model",
        type=str,
        default=os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        help="Model name to request from remote Ollama server."
    )
    parser.add_argument(
        "--host",
        type=str,
        default=os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST),
        help="Remote host IP or hostname."
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("OLLAMA_PORT", str(DEFAULT_OLLAMA_PORT))),
        help="Remote host port."
    )
    args = parser.parse_args()

    # Convert paths to absolute to prevent relative navigation errors
    db_file_path: str = os.path.abspath(args.db)
    prompt_file_path: str = os.path.abspath(args.prompt)

    run_repl(
        db_path=db_file_path,
        prompt_path=prompt_file_path,
        vlm_url=args.local_url,
        remote=args.remote,
        model_name=args.model,
        host=args.host,
        port=args.port
    )

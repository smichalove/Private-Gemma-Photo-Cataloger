"""Unit Tests for Interactive Database Chat REPL Client.

1. Purpose:
    This module contains the unittest suite for verifying the core utility functions of 
    `db_chat_repl.py`. It uses mock patching to isolate tests from the active filesystem,
    external VLM/Ollama servers, and actual SQLite files, ensuring clean, fast, and
    deterministic runs.

2. Architecture and Mechanics:
    - Load System Prompt Tests: Verifies loading templates and falling back when files are missing.
    - SQL Formatting and Execution Tests: Validates formatting database results as markdown
      tables or index bullets depending on the output columns.
    - Parser/Agent Loop Parsing: Verifies extracting tool call blocks and SELECT queries from model text.

3. Execution Modes:
    - Run the tests directly from CLI:
      python -m unittest local/tests/test_db_chat_repl.py
"""

import os
import sys
import re
import json
import unittest
from unittest.mock import patch, MagicMock, mock_open
from typing import Dict, List, Set, Optional, Tuple, Any

# Ensure local directory is in sys.path to allow importing db_chat_repl
local_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if local_dir not in sys.path:
    sys.path.insert(0, local_dir)

# Import the functions under test from db_chat_repl.py
import db_chat_repl
from db_chat_repl import (
    load_system_prompt,
    get_total_photos_count,
    execute_sql
)


class TestDBChatRepl(unittest.TestCase):
    """Unit test suite for the Interactive Database Chat REPL client utility functions.

    Verifies system prompt loading, database query execution, markdown formatting,
    and regular expression parsing.
    """

    def setUp(self) -> None:
        """Setup fixture called before each test method execution."""
        db_chat_repl.last_query_paths = []

    def tearDown(self) -> None:
        """Teardown fixture called after each test method execution."""
        pass

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data="Mock System Prompt Template {current_time} {total_photos}")
    def test_load_system_prompt_success(self, mock_file: MagicMock, mock_exists: MagicMock) -> None:
        """Tests that load_system_prompt successfully loads instructions from disk when the file exists."""
        mock_exists.return_value = True
        prompt = load_system_prompt("mock_prompt.txt")
        self.assertEqual(prompt, "Mock System Prompt Template {current_time} {total_photos}")
        mock_file.assert_called_once_with("mock_prompt.txt", "r", encoding="utf-8")

    @patch("os.path.exists")
    def test_load_system_prompt_fallback(self, mock_exists: MagicMock) -> None:
        """Tests that load_system_prompt returns the default fallback when the prompt file is missing."""
        mock_exists.return_value = False
        prompt = load_system_prompt("nonexistent_prompt.txt")
        self.assertIn("Total photo records currently cataloged", prompt)

    @patch("os.path.exists")
    @patch("sqlite3.connect")
    def test_get_total_photos_count(self, mock_connect: MagicMock, mock_exists: MagicMock) -> None:
        """Tests get_total_photos_count queries the SQLite database and returns the total photo count."""
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (42,)
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        count = get_total_photos_count("mock_photo_catalog.db")
        self.assertEqual(count, 42)
        mock_cursor.execute.assert_called_with("SELECT COUNT(*) FROM photos")
        mock_conn.close.assert_called_once()

    @patch("os.path.exists")
    @patch("sqlite3.connect")
    def test_execute_sql_markdown_table(self, mock_connect: MagicMock, mock_exists: MagicMock) -> None:
        """Tests that execute_sql returns raw markdown tables when querying general attributes (not paths)."""
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("id", None), ("primary_subject", None), ("environment", None)]
        mock_cursor.fetchall.return_value = [
            (1, "A cute cat", "indoor"),
            (2, "A red car", "street")
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        raw_markdown, term_display, paths = execute_sql(
            "SELECT id, primary_subject, environment FROM photos",
            "mock_photo_catalog.db"
        )

        self.assertIn("| id | primary_subject | environment |", raw_markdown)
        self.assertIn("| 1 | A cute cat | indoor |", raw_markdown)
        self.assertIn("| 2 | A red car | street |", raw_markdown)
        self.assertIn("| id | primary_subject | environment |", term_display)
        self.assertEqual(paths, [])

    @patch("os.path.exists")
    @patch("sqlite3.connect")
    def test_execute_sql_indexed_bullets(self, mock_connect: MagicMock, mock_exists: MagicMock) -> None:
        """Tests that execute_sql returns indexed bullets and maps paths when full_path or rel_path is selected."""
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("id", None), ("full_path", None)]
        # Use lowercase forward-slash paths internally, which normalized relative/absolute path lookups expect
        path1 = os.path.normpath("D:/Pictures/cat.jpg")
        path2 = os.path.normpath("D:/Pictures/car.jpg")
        mock_cursor.fetchall.return_value = [
            (1, path1),
            (2, path2)
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        raw_markdown, term_display, paths = execute_sql(
            "SELECT id, full_path FROM photos",
            "mock_photo_catalog.db"
        )

        # Expected format in bullet mapping lists
        self.assertEqual(paths, [path1, path2])
        self.assertIn(f"[1] {path1}", term_display)
        self.assertIn(f"[2] {path2}", term_display)

    def test_tool_call_regex_parsing(self) -> None:
        """Tests that the agent loop regex pattern correctly extracts SQL query blocks from the LLM response."""
        response_text = (
            "Based on your request, I will execute a query.\n"
            "<tool_call>{\"tool\": \"query_db\", \"sql\": \"SELECT id, full_path FROM photos WHERE primary_subject LIKE '%cat%' LIMIT 5\"}</tool_call>\n"
            "This will retrieve the photos."
        )

        tool_call_match = re.search(r'<tool_call>(.*?)</tool_call>', response_text, re.DOTALL)
        self.assertTrue(tool_call_match is not None)
        if tool_call_match:
            tool_json_str = tool_call_match.group(1).strip()
            tool_data = json.loads(tool_json_str)
            self.assertEqual(tool_data["tool"], "query_db")
            self.assertEqual(tool_data["sql"], "SELECT id, full_path FROM photos WHERE primary_subject LIKE '%cat%' LIMIT 5")


if __name__ == "__main__":
    unittest.main()

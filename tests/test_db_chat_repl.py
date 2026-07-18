"""Unit Tests for Interactive Database Chat REPL Client on the Dev Branch.

Purpose:
    Contains unit tests for the functions in db_chat_repl.py, focusing on database counts,
    system prompt fallbacks, row truncation, and dynamic metadata column formatting.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock, mock_open
from typing import Dict, List, Tuple, Any

# Add the local directory to system path
local_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "local")
sys.path.insert(0, local_dir)

import db_chat_repl
from db_chat_repl import (
    load_system_prompt,
    get_total_photos_count,
    execute_sql
)


class TestDBChatRepl(unittest.TestCase):
    """Test suite for db_chat_repl.py functionality.

    Attributes:
        None
    """

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data="Mock prompt {current_time} {total_photos}")
    def test_load_system_prompt_success(self, mock_file: MagicMock, mock_exists: MagicMock) -> None:
        """Tests load_system_prompt retrieves the external prompt content successfully.

        Args:
            mock_file: Mocked builtins.open.
            mock_exists: Mocked os.path.exists.

        Returns:
            None
        """
        mock_exists.return_value = True
        prompt = load_system_prompt("mock_prompt.txt")
        self.assertEqual(prompt, "Mock prompt {current_time} {total_photos}")

    @patch("os.path.exists")
    def test_load_system_prompt_fallback(self, mock_exists: MagicMock) -> None:
        """Tests load_system_prompt uses fallback string when prompt file is missing.

        Args:
            mock_exists: Mocked os.path.exists.

        Returns:
            None
        """
        mock_exists.return_value = False
        prompt = load_system_prompt("nonexistent_prompt.txt")
        self.assertIn("Total photo records currently cataloged", prompt)

    @patch("os.path.exists")
    @patch("sqlite3.connect")
    def test_get_total_photos_count(self, mock_connect: MagicMock, mock_exists: MagicMock) -> None:
        """Tests get_total_photos_count retrieves correct count from photos table.

        Args:
            mock_connect: Mocked sqlite3 connection.
            mock_exists: Mocked os.path.exists.

        Returns:
            None
        """
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (100,)
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        count = get_total_photos_count()
        self.assertEqual(count, 100)

    @patch("os.path.exists")
    @patch("sqlite3.connect")
    def test_execute_sql_bullets_and_truncation(self, mock_connect: MagicMock, mock_exists: MagicMock) -> None:
        """Tests execute_sql formats bullet lists with truncated metadata, and truncates VLM cells.

        Args:
            mock_connect: Mocked sqlite3 connection.
            mock_exists: Mocked os.path.exists.

        Returns:
            None
        """
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("full_path", None), ("primary_subject", None)]
        
        long_desc = "A black cat " * 200  # 2400 chars
        mock_cursor.fetchall.return_value = [
            ("D:\\Pictures\\cat.jpg", long_desc)
        ] * 11
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        raw_markdown, term_display, paths = execute_sql("SELECT full_path, primary_subject FROM photos")

        # Verify that terminal display bullets append the description (formatted dynamically)
        self.assertIn("D:\\Pictures\\cat.jpg", term_display)
        self.assertIn("primary_subject: ", term_display)
        
        # Verify that the full description is present (not truncated in console)
        self.assertIn("A black cat", term_display)
        
        # Verify subsequent lines are indented
        self.assertIn("\n    ", term_display)

        # Verify raw markdown sent to VLM truncates cells to 2000 chars as well (including 3 for '...')
        self.assertIn("...", raw_markdown)
        self.assertEqual(paths, ["D:\\Pictures\\cat.jpg"] * 11)

    @patch("os.path.exists")
    @patch("sqlite3.connect")
    def test_execute_sql_300_rows_truncation(self, mock_connect: MagicMock, mock_exists: MagicMock) -> None:
        """Tests row truncation caps VLM markdown at 100 rows and terminal display at 300 rows.

        Args:
            mock_connect: Mocked sqlite3 connection.
            mock_exists: Mocked os.path.exists.

        Returns:
            None
        """
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("full_path", None), ("primary_subject", None)]
        
        # Mock 350 rows
        mock_cursor.fetchall.return_value = [
            (f"D:\\Pictures\\cat_{i}.jpg", "A black cat") for i in range(350)
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        raw_markdown, term_display, paths = execute_sql("SELECT full_path, primary_subject FROM photos")

        # VLM raw_markdown table should have prefix note (2 lines), header (1 line), divider (1 line), and exactly 5 rows.
        markdown_lines = raw_markdown.splitlines()
        self.assertEqual(len(markdown_lines), 9)
        self.assertIn("Returned 350 rows.", raw_markdown)
        self.assertIn("Only the first 5 rows are shown below as a sample", raw_markdown)

        # Terminal display should have exactly 350 bullet records in paths
        self.assertEqual(len(paths), 350)
        self.assertIn("[300] D:\\Pictures\\cat_299.jpg", term_display)

    @patch("os.path.exists")
    @patch("sqlite3.connect")
    def test_execute_sql_multi_column_formatting(self, mock_connect: MagicMock, mock_exists: MagicMock) -> None:
        """Tests execute_sql dynamically formats all selected metadata columns.

        Args:
            mock_connect: Mocked sqlite3 connection.
            mock_exists: Mocked os.path.exists.

        Returns:
            None
        """
        mock_exists.return_value = True
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [
            ("full_path", None),
            ("primary_subject", None),
            ("environment", None),
            ("suggested_tags", None)
        ]
        mock_cursor.fetchall.return_value = [
            ("D:\\Pictures\\cat.jpg", "A black cat", "living room", "[\"cat\", \"indoor\"]")
        ] * 11
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        raw_markdown, term_display, paths = execute_sql("SELECT * FROM photos")

        lines = term_display.splitlines()
        self.assertEqual(lines[0], "[1] D:\\Pictures\\cat.jpg")
        self.assertEqual(lines[1], "    primary_subject: A black cat")
        self.assertEqual(lines[2], "    environment: living room")
        self.assertEqual(lines[3], "    suggested_tags: [\"cat\", \"indoor\"]")

    @patch("builtins.input")
    @patch("threading.Thread")
    @patch("db_chat_repl.get_total_photos_count")
    @patch("db_chat_repl.load_system_prompt")
    def test_run_repl_catalog_command(self, mock_load_prompt: MagicMock, mock_count: MagicMock, mock_thread: MagicMock, mock_input: MagicMock) -> None:
        """Tests that run_repl correctly parses and launches the cataloger command from prompt.

        Args:
            mock_load_prompt: Mocked load_system_prompt.
            mock_count: Mocked get_total_photos_count.
            mock_thread: Mocked threading.Thread.
            mock_input: Mocked builtins.input.

        Returns:
            None
        """
        # Mock input sequence: run catalog command, then exit
        mock_input.side_effect = ["/catalog --max-photos 5", "exit"]
        mock_count.return_value = 100
        mock_load_prompt.return_value = "Mock system prompt"

        # Run REPL in remote mode so it does not boot local wsl server
        from db_chat_repl import run_repl
        run_repl(remote=True)

        # Verify that threading.Thread was called to launch the cataloger in background
        mock_thread.assert_called_once()
        kwargs = mock_thread.call_args[1]
        self.assertEqual(kwargs["name"], "CatalogerRun")
        self.assertEqual(kwargs["args"], (["--max-photos", "5"],))

    @patch("builtins.input")
    @patch("db_chat_repl.get_total_photos_count")
    @patch("db_chat_repl.load_system_prompt")
    @patch("sqlite3.connect")
    @patch("os.path.exists")
    def test_run_repl_direct_sql(self, mock_exists: MagicMock, mock_connect: MagicMock, mock_load_prompt: MagicMock, mock_count: MagicMock, mock_input: MagicMock) -> None:
        """Tests that run_repl executes direct SQL input queries without using VLM.

        Args:
            mock_exists: Mocked os.path.exists.
            mock_connect: Mocked sqlite3 connection.
            mock_load_prompt: Mocked load_system_prompt.
            mock_count: Mocked get_total_photos_count.
            mock_input: Mocked builtins.input.

        Returns:
            None
        """
        # Mock input sequence: direct SELECT query, then exit
        mock_input.side_effect = ["SELECT * FROM photos LIMIT 5", "exit"]
        mock_count.return_value = 100
        mock_load_prompt.return_value = "Mock system prompt"
        mock_exists.return_value = True

        # Mock database connection and cursor
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.description = [("full_path", None)]
        mock_cursor.fetchall.return_value = [("D:\\Pictures\\photo.jpg",)]
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        from db_chat_repl import run_repl
        run_repl(remote=True)

        # Verify that execute_sql was called with the direct SQL input query
        mock_cursor.execute.assert_called_with("SELECT * FROM photos LIMIT 5")

    def test_type_hints(self) -> None:
        """Verifies type hints exist for all key functions in db_chat_repl.py."""
        import typing
        from db_chat_repl import load_system_prompt, get_total_photos_count, execute_sql, run_repl
        
        for func in [load_system_prompt, get_total_photos_count, execute_sql, run_repl]:
            hints = typing.get_type_hints(func)
            self.assertTrue(len(hints) > 0, f"Function {func.__name__} has no type hints.")

    @patch.dict("os.environ", {"PLAYLIST_DIR": r"C:\Users\username\Music\Playlists"})
    @patch("builtins.input")
    @patch("builtins.open", new_callable=mock_open)
    @patch("os.makedirs")
    @patch("db_chat_repl.get_total_photos_count")
    @patch("db_chat_repl.load_system_prompt")
    def test_run_repl_playlist_command(self, mock_load_prompt: MagicMock, mock_count: MagicMock, mock_makedirs: MagicMock, mock_file: MagicMock, mock_input: MagicMock) -> None:
        """Tests that run_repl processes the /playlist command and writes an M3U file."""
        import db_chat_repl
        # Inject mock paths into last_query_paths
        db_chat_repl.last_query_paths = [
            r"C:\Users\username\Music\Track1.flac",
            r"C:\Users\username\Music\Track2.mp3",
            r"C:\Users\username\Pictures\Photo.jpg"  # Non-audio file
        ]

        # Input "/playlist test_list", then exit
        mock_input.side_effect = ["/playlist test_list", "exit"]
        mock_count.return_value = 100
        mock_load_prompt.return_value = "Mock system prompt"

        from db_chat_repl import run_repl
        run_repl(remote=True)

        # Assert directory creation was attempted
        mock_makedirs.assert_called_with(r"C:\Users\username\Music\Playlists", exist_ok=True)
        
        # Assert file writing was triggered for only the 2 audio files
        import sys
        expected_playlist_path = r"C:\Users\username\Music\Playlists/test_list.m3u" if sys.platform in ("darwin", "linux") else r"C:\Users\username\Music\Playlists\test_list.m3u"
        mock_file.assert_called_with(expected_playlist_path, "w", encoding="utf-8")
        
        # Verify tracks written
        handle = mock_file()
        calls = [c[0][0] for c in handle.write.call_args_list]
        self.assertIn("C:\\Users\\username\\Music\\Track1.flac\n", calls)
        self.assertIn("C:\\Users\\username\\Music\\Track2.mp3\n", calls)
        self.assertNotIn("C:\\Users\\username\\Pictures\\Photo.jpg\n", calls)


    @patch.dict("os.environ", {"PLAYLIST_DIR": r"C:\Users\username\Music\Playlists"})
    @patch("builtins.input")
    @patch("requests.get")
    @patch("db_chat_repl.get_total_photos_count")
    @patch("db_chat_repl.load_system_prompt")
    def test_run_repl_play_command(self, mock_load_prompt: MagicMock, mock_count: MagicMock, mock_get: MagicMock, mock_input: MagicMock) -> None:
        """Tests that run_repl processes the /play command and sends MCWS HTTP requests."""
        import db_chat_repl
        # Inject mock paths into last_query_paths
        db_chat_repl.last_query_paths = [
            r"C:\Users\username\Music\Track1.flac",
            r"C:\Users\username\Music\Track2.mp3"
        ]

        # Input "/play", then exit
        mock_input.side_effect = ["/play", "exit"]
        mock_count.return_value = 100
        mock_load_prompt.return_value = "Mock system prompt"

        # Mock requests.get response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        from db_chat_repl import run_repl
        run_repl(remote=True)

        # Verify ClearPlaylist was called first
        import sys
        import os
        jriver_host = os.getenv("JRIVER_HOST")
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
        mock_get.assert_any_call(f"http://{jriver_host}:52198/MCWS/v1/Playback/ClearPlaylist?Zone=0&ZoneType=ID", timeout=10)
        
        # Verify PlayByFilename was called for temp playlist
        import urllib.parse
        from db_chat_repl import PROJECT_DIR
        playlist_dir = os.getenv("PLAYLIST_DIR")
        if not playlist_dir:
            for candidate in (
                r"C:\Users\username\Music\Playlists",
                "/Volumes/d-drive/Users/username/Music/Playlists",
                "/mnt/d/Users/username/Music/Playlists"
            ):
                if os.path.exists(os.path.dirname(candidate)):
                    playlist_dir = candidate
                    break
        if not playlist_dir:
            playlist_dir = os.path.join(PROJECT_DIR, "Playlists")
            
        temp_playlist_path = os.path.join(playlist_dir, "temp_playback_queue.m3u")
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
        mock_get.assert_any_call(f"http://{jriver_host}:52198/MCWS/v1/Playback/PlayByFilename?Filenames={encoded_playlist}&Location=End&Zone=0&ZoneType=ID", timeout=(5.0, 15.0))
        
        # Verify Play was called at the end
        mock_get.assert_any_call(f"http://{jriver_host}:52198/MCWS/v1/Playback/Play?Zone=0&ZoneType=ID", timeout=10)

    @patch("builtins.input")
    @patch("requests.get")
    @patch("db_chat_repl.get_total_photos_count")
    @patch("db_chat_repl.load_system_prompt")
    def test_run_repl_queue_command(self, mock_load_prompt: MagicMock, mock_count: MagicMock, mock_get: MagicMock, mock_input: MagicMock) -> None:
        """Tests that run_repl processes the /queue / /add command and sends MCWS HTTP requests without clearing."""
        import db_chat_repl
        # Inject mock paths into last_query_paths
        db_chat_repl.last_query_paths = [
            r"C:\Users\username\Music\Track1.flac",
            r"C:\Users\username\Music\Track2.mp3"
        ]

        # Input "/queue 2", then exit
        mock_input.side_effect = ["/queue 2", "exit"]
        mock_count.return_value = 100
        mock_load_prompt.return_value = "Mock system prompt"

        # Mock requests.get response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        from db_chat_repl import run_repl
        run_repl(remote=True)

        # Assert ClearPlaylist was NOT called
        for call_arg in mock_get.call_args_list:
            url = call_arg[0][0]
            self.assertNotIn("ClearPlaylist", url)
            self.assertNotIn("Playback/Play?", url)  # Play should also not be called for queues

        # Verify PlayByFilename was called for track 2 only
        import sys
        import os
        import urllib.parse
        encoded2 = urllib.parse.quote(r"C:\Users\username\Music\Track2.mp3")
        jriver_host = os.getenv("JRIVER_HOST")
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
        mock_get.assert_any_call(f"http://{jriver_host}:52198/MCWS/v1/Playback/PlayByFilename?Filenames={encoded2}&Location=End&Zone=0&ZoneType=ID", timeout=(5.0, 15.0))

    @patch("builtins.input")
    @patch("db_chat_repl.get_total_photos_count")
    @patch("db_chat_repl.load_system_prompt")
    @patch("os.makedirs")
    @patch("os.path.isfile")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data='[{"role": "user", "content": "saved query"}]')
    def test_run_repl_save_and_load_commands(self, mock_file: MagicMock, mock_exists: MagicMock, mock_isfile: MagicMock, mock_makedirs: MagicMock, mock_load_prompt: MagicMock, mock_count: MagicMock, mock_input: MagicMock) -> None:
        """Tests that run_repl processes /save and /load commands successfully."""
        import db_chat_repl
        db_chat_repl.chat_history = [{"role": "user", "content": "hello database"}]
        
        # Test input sequence: /save custom_session, then /load custom_session, then exit
        mock_input.side_effect = ["/save custom_session", "/load custom_session", "exit"]
        mock_count.return_value = 100
        mock_load_prompt.return_value = "Mock system prompt"
        mock_exists.return_value = True
        mock_isfile.return_value = True

        from db_chat_repl import run_repl
        run_repl(remote=True)
        
        # Verify /save writes to custom_session.json in sessions folder
        mock_file.assert_any_call(os.path.join(db_chat_repl.PROJECT_DIR, "sessions", "custom_session.json"), "w", encoding="utf-8")
        
        # Verify /load reads from custom_session.json in sessions folder
        mock_file.assert_any_call(os.path.join(db_chat_repl.PROJECT_DIR, "sessions", "custom_session.json"), "r", encoding="utf-8")

    @patch("builtins.input")
    @patch("db_chat_repl.get_total_photos_count")
    @patch("db_chat_repl.load_system_prompt")
    @patch("os.makedirs")
    @patch("os.path.isfile")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open, read_data='[{"role": "user", "content": "saved query"}]')
    def test_run_repl_load_empty_defaults_to_last_chat(self, mock_file: MagicMock, mock_exists: MagicMock, mock_isfile: MagicMock, mock_makedirs: MagicMock, mock_load_prompt: MagicMock, mock_count: MagicMock, mock_input: MagicMock) -> None:
        """Tests that run_repl processes /load without a name by loading last_chat.json."""
        import db_chat_repl
        mock_input.side_effect = ["/load", "exit"]
        mock_count.return_value = 100
        mock_load_prompt.return_value = "Mock system prompt"
        mock_exists.return_value = True
        mock_isfile.return_value = True

        from db_chat_repl import run_repl
        run_repl(remote=True)
        
        # Verify /load reads from last_chat.json in sessions folder
        mock_file.assert_any_call(os.path.join(db_chat_repl.PROJECT_DIR, "sessions", "last_chat.json"), "r", encoding="utf-8")

    @patch("subprocess.Popen")
    @patch("os.access")
    @patch("os.path.exists")
    def test_call_antigravity_agent_success(self, mock_exists: MagicMock, mock_access: MagicMock, mock_popen: MagicMock) -> None:
        """Tests that call_antigravity_agent invokes subprocess.Popen with the expected arguments.

        Args:
            mock_exists: Mocked os.path.exists.
            mock_access: Mocked os.access.
            mock_popen: Mocked subprocess.Popen.

        Returns:
            None
        """
        mock_exists.return_value = True
        mock_access.return_value = True
        
        # Mock process stdout streaming
        mock_proc = MagicMock()
        mock_proc.stdout.read.side_effect = ["a", "b", ""]
        mock_proc.poll.return_value = 0
        mock_popen.return_value = mock_proc
        
        from db_chat_repl import call_antigravity_agent
        call_antigravity_agent("test task")
        
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        self.assertIn("--dangerously-skip-permissions", args)
        self.assertIn("--print", args)
        self.assertIn("test task", args)

    @patch("builtins.input")
    @patch("db_chat_repl.call_antigravity_agent")
    @patch("db_chat_repl.get_total_photos_count")
    @patch("db_chat_repl.load_system_prompt")
    def test_run_repl_agent_command(self, mock_load_prompt: MagicMock, mock_count: MagicMock, mock_call_agent: MagicMock, mock_input: MagicMock) -> None:
        """Tests that run_repl processes the /agent slash command and invokes call_antigravity_agent.

        Args:
            mock_load_prompt: Mocked load_system_prompt.
            mock_count: Mocked get_total_photos_count.
            mock_call_agent: Mocked call_antigravity_agent.
            mock_input: Mocked builtins.input.

        Returns:
            None
        """
        # Mock input sequence: run agent command, then exit
        mock_input.side_effect = ["/agent update database", "exit"]
        mock_count.return_value = 100
        mock_load_prompt.return_value = "Mock system prompt"

        from db_chat_repl import run_repl
        run_repl(remote=True)

        mock_call_agent.assert_called_once_with("update database")


if __name__ == "__main__":
    unittest.main()


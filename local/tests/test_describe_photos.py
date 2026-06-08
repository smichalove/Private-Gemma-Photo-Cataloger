"""Unit Tests for Local VLM Photo Cataloger - Test Suite.

SYSTEM ROLE & ARCHITECTURE:
This module contains the unittest suite for verifying the core utility functions of 
`describe_photos.py`. It uses mock patching to isolate tests from the active filesystem, 
external models, and shell executables (like ExifTool), ensuring clean, fast, and 
deterministic runs.

                   +-----------------------------+
                   |   test_describe_photos.py   |
                   +--------------+--------------+
                                  | (uses mocks)
                                  v
                   +--------------v--------------+
                   |      describe_photos.py     |
                   |   - JSON payload parsing    |
                   |   - Image directory walks   |
                   |   - Atomic database writes  |
                   |   - Metadata tag generation |
                   +-----------------------------+

TEST SUITE COVERAGE:
1. JSON Payload Extraction:
   - Stripping markdown fences (```json ... ```).
   - Adding missing keys dynamically to conform to the metadata schema.
   - Falling back gracefully to string logs when JSON is corrupt or unparsable.
2. Directory Scanning & Crawling:
   - Validating recursive path crawler checks extensions correctly.
   - Skipping relative paths that are already flagged as described/embedded in cache.
3. ExifTool Integration:
   - Mocking ExifTool subprocess execution to verify exact command arguments and temporary 
     argument file formats.
4. Database Integrity:
   - Ensuring database saves are atomic (writing to a temp file first, then replacing).

CRITICAL DEPENDENCIES:
- `unittest` (standard library).
- `unittest.mock` (standard library).
- `describe_photos.py` under test.

AGENT GUIDELINE FOR MODIFICATION:
- Avoid executing actual disk writes or running subprocesses directly in this test suite.
- Always mock out filesystem commands (`os.path.exists`, `os.walk`, `builtins.open`) and 
  process calls (`subprocess.run`).
- When introducing changes to the CLI argument signatures or output schemas in `describe_photos.py`, 
  ensure the mock classes in `setUp` or test assertions are updated accordingly.
"""

import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock, mock_open
from typing import Dict, List, Set, Optional, Tuple, Any

# Ensure local directory is in sys.path to allow importing describe_photos
local_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if local_dir not in sys.path:
    sys.path.insert(0, local_dir)

# Import the functions under test from describe_photos.py
from describe_photos import (
    extract_json_payload,
    get_image_files,
    load_and_encode_image,
    inline_embed_metadata,
    save_results
)


class TestGemmaPhotoCataloger(unittest.TestCase):
    """Unit test suite for the Gemma Photo Cataloger script utility functions.

    Verifies JSON extraction, image discovery, EXIF metadata embedding,
    and results serialization.
    """

    def setUp(self) -> None:
        """Setup fixture called before each test method execution."""
        pass

    def tearDown(self) -> None:
        """Teardown fixture called after each test method execution."""
        pass

    def test_extract_json_payload_valid(self) -> None:
        """Tests that extract_json_payload correctly parses a clean JSON string."""
        raw_json: str = (
            "{\n"
            "  \"primary_subject\": \"A beautiful mountain scene\",\n"
            "  \"environment\": \"outdoor\",\n"
            "  \"suggested_tags\": [\"nature\", \"landscape\"]\n"
            "}"
        )
        
        result = extract_json_payload(raw_json)
        
        self.assertEqual(result["primary_subject"], "A beautiful mountain scene")
        self.assertEqual(result["environment"], "outdoor")
        self.assertEqual(result["suggested_tags"], ["nature", "landscape"])

    def test_extract_json_payload_with_fences(self) -> None:
        """Tests that extract_json_payload strips markdown code blocks and parses correctly."""
        raw_json_fenced: str = (
            "```json\n"
            "{\n"
            "  \"primary_subject\": \"A historic building\",\n"
            "  \"environment\": \"urban\",\n"
            "  \"suggested_tags\": [\"architecture\"]\n"
            "}\n"
            "```"
        )
        
        result = extract_json_payload(raw_json_fenced)
        
        self.assertEqual(result["primary_subject"], "A historic building")
        self.assertEqual(result["suggested_tags"], ["architecture"])

    def test_extract_json_payload_missing_keys(self) -> None:
        """Tests that extract_json_payload adds missing keys with appropriate default values."""
        incomplete_json: str = (
            "{\n"
            "  \"primary_subject\": \"A red apple\"\n"
            "}"
        )
        
        result = extract_json_payload(incomplete_json)
        
        self.assertEqual(result["primary_subject"], "A red apple")
        self.assertEqual(result["environment"], "")
        self.assertEqual(result["suggested_tags"], [])

    def test_extract_json_payload_malformed(self) -> None:
        """Tests that extract_json_payload handles malformed JSON input gracefully by returning a fallback."""
        malformed_json: str = "This is not JSON at all, it's just raw description text."
        
        result = extract_json_payload(malformed_json)
        
        self.assertEqual(result["primary_subject"], malformed_json)
        self.assertEqual(result["environment"], "Unknown")
        self.assertEqual(result["suggested_tags"], ["error-parsing-json"])

    @patch("os.walk")
    @patch("os.path.exists")
    def test_get_image_files(self, mock_exists: MagicMock, mock_walk: MagicMock) -> None:
        """Tests get_image_files walks target directories and filters already-processed files."""
        mock_exists.return_value = True
        
        # Simulate walking a directory and finding some image files
        mock_walk.return_value = [
            (os.path.abspath("Pictures"), [], ["photo1.jpg", "photo2.png", "processed.jpg", "textfile.txt"])
        ]
        
        processed: Set[str] = {"photo2.png"}
        
        result = get_image_files(
            directories=[os.path.abspath("Pictures")],
            limit=10,
            processed_paths=processed
        )
        
        expected_paths = [
            os.path.join(os.path.abspath("Pictures"), "photo1.jpg"),
            os.path.join(os.path.abspath("Pictures"), "processed.jpg")
        ]
        self.assertEqual(len(result), 2)
        self.assertIn(expected_paths[0], result)
        self.assertIn(expected_paths[1], result)

    @patch("subprocess.run")
    def test_inline_embed_metadata(self, mock_run: MagicMock) -> None:
        """Tests that inline_embed_metadata executes exiftool with correct command arguments."""
        file_path: str = os.path.abspath("photo1.jpg")
        summary_text: str = "Subject: A beautiful sunrise\nEnvironment: Outdoor\nTags: sun, dawn"
        output_json: str = os.path.abspath("photo_descriptions.json")
        
        # Set EXIFTOOL_PATH globally inside the module under test to ensure test predictability
        import describe_photos
        describe_photos.EXIFTOOL_PATH = "exiftool"

        inline_embed_metadata(file_path, summary_text, output_json)
        
        mock_run.assert_called_once()
        called_args: List[str] = mock_run.call_args[0][0]
        
        self.assertEqual(called_args[0], "exiftool")
        self.assertIn("-overwrite_original", called_args)
        self.assertIn(f"-Caption-Abstract={summary_text}", called_args)
        self.assertIn(f"-Description={summary_text}", called_args)
        self.assertIn(f"-ImageDescription={summary_text}", called_args)
        self.assertIn("-@", called_args)
        self.assertTrue(called_args[-1].endswith(".txt"))

    @patch("builtins.open", new_callable=mock_open)
    @patch("os.replace")
    def test_save_results_atomic(self, mock_replace: MagicMock, mock_file: MagicMock) -> None:
        """Tests that save_results atomically saves description database entries to disk via a temp file."""
        test_results = [
            {
                "full_path": os.path.abspath("photo1.jpg"),
                "primary_subject": "Test subject",
                "environment": "Test environment",
                "suggested_tags": []
            }
        ]
        output_json: str = os.path.abspath("photo_descriptions.json")
        
        save_results(test_results, output_json)
        
        mock_file.assert_called_with(output_json + ".tmp", "w", encoding="utf-8")
        mock_replace.assert_called_once_with(
            output_json + ".tmp",
            output_json
        )


if __name__ == "__main__":
    unittest.main()

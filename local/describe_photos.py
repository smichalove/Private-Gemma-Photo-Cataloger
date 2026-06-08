"""Local Gemma 4 VLM Photo Cataloger - Local Orchestrator.

SYSTEM ROLE & ARCHITECTURE:
This module is the orchestrator for the local, offline photo description and metadata 
archiving pipeline. It coordinates the execution flow from raw image directory crawling 
to final EXIF header metadata embedding.

     +------------------+     (recursively scans)     +--------------------+
     | Image Directory  | <========================== | describe_photos.py |
     +------------------+                             +---------+----------+
              |                                                 |
              | (reads & encodes)                     (invokes) | (queries via HTTP)
              v                                                 v
     +------------------+                             +---------v----------+
     | PIL / Base64     |                             |   wsl_client.py    |
     +--------+---------+                             +---------+----------+
              | (payload)                                       | (docker start)
              v                                                 v
     +----------------------------------------------------------v----------+
     |                  wsl_server.py (WSL2 Docker FastAPI)                |
     +---------------------------------------------------------------------+

EXECUTION WORKFLOW:
1. Directory Scanning: Scans target directories recursively, filtering by image extensions.
2. Duplicate Filtering: Uses the results JSON database (and/or an active cache text file) 
   to skip previously processed images, preventing redundant model execution.
3. Server Lifecycle Management: Invokes `wsl_client.py` to ensure the dockerized 
   WSL2 VLM FastAPI server is running and healthy.
4. Parallel Prefetching & Batch Inference: Employs a CPU ThreadPoolExecutor to parallelize 
   image loading/base64 encoding. Groups files into batches and queries the server.
5. JSON Parsing: Extracts and normalizes the JSON block returned by the model 
   (keys: primary_subject, environment, suggested_tags).
6. Atomic Database Writes: Merges and writes results to the local JSON database file 
   using a thread-safe Lock and temp-file replacement.
7. Metadata Embedding (Optional): Triggers an asynchronous ThreadPoolExecutor to run 
   ExifTool, natively writing summary captions to image headers.

INPUTS:
- Image directories (via environment variables or --dir flags).
- prompt.txt: Real-time LLM instructions loaded dynamically.
- Existing database cache JSON file.

OUTPUTS:
- photo_descriptions.json: Output database containing structural description dictionary.
- EXIF/IPTC/XMP metadata directly modified inside the image files.
- gemma_cataloger.log: Log file containing pipeline run history.

CRITICAL DEPENDENCIES:
- `wsl_client.py`: For Docker/Server startup and API communication.
- ExifTool: Must be installed and reachable in the environment PATH.

AGENT GUIDELINE FOR MODIFICATION:
- Keep the `json_lock` thread lock intact when modifying the `save_results` function.
- Path representations must use lowercase, forward-slash normalization (e.g., via 
  `get_relative_path`) to ensure compatibility between Windows paths and Cloud/WSL2 environments.
- Maintain fallback exception handlers for ExifTool write failures (like clearing IRB resource errors).
"""

import os
import sys
import json
import time
import logging
import concurrent.futures
import threading
import subprocess
import argparse
import uuid
import base64
import io
from typing import List, Dict, Set, Optional, Tuple, Union

from PIL import Image, ImageFile
Image.MAX_IMAGE_PIXELS = None  # Allow massively high-res images to be processed
ImageFile.LOAD_TRUNCATED_IMAGES = True  # Allow reading truncated or slightly corrupted files
import pillow_heif
pillow_heif.register_heif_opener()

# Ensure standard output streams use UTF-8 on Windows to prevent encoding crashes
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass

# Load dotenv if available to manage configuration variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Setup logging to both console and a local log file
LOG_FILE: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemma_cataloger.log")

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

if root_logger.hasHandlers():
    root_logger.handlers.clear()

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)

try:
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setFormatter(log_formatter)
    root_logger.addHandler(file_handler)
except Exception as e:
    sys.stderr.write(f"Failed to create file logger: {e}\n")

logger = logging.getLogger(__name__)

# --- Configuration defaults ---
MODEL_ID: str = os.environ.get("GEMINI_MODEL_NAME", "google/gemma-4-12B-it")
OUTPUT_JSON: str = os.environ.get("OUTPUT_DATABASE_PATH", "photo_descriptions.json")
SUBMITTED_CACHE: str = os.environ.get("SUBMITTED_CACHE_PATH", "submitted_photos_cache.txt")
EXIFTOOL_PATH: str = os.environ.get("EXIFTOOL_PATH", "exiftool")

_raw_dirs: str = os.environ.get("PICTURE_DIRECTORIES", "")
PICTURE_DIRS: List[str] = [d.strip() for d in _raw_dirs.split(",") if d.strip()]

# Threading lock for saving the JSON database file safely
json_lock: threading.Lock = threading.Lock()


def get_relative_path(full_path: str, scan_dirs: List[str]) -> str:
    """Extracts a relative path portion against any matching scan directory root.

    Args:
        full_path: Absolute path to the file.
        scan_dirs: List of base directories to compute relative paths against.

    Returns:
        The normalized lowercase relative path with forward slashes.
    """
    normalized_path: str = full_path.replace("\\", "/").lower()
    for directory in scan_dirs:
        norm_dir: str = os.path.abspath(directory).replace("\\", "/").lower()
        if not norm_dir.endswith("/"):
            norm_dir += "/"
        if normalized_path.startswith(norm_dir):
            return normalized_path[len(norm_dir):]
    return os.path.basename(normalized_path)


def get_image_files(directories: List[str], limit: Optional[int] = 20, processed_paths: Optional[Set[str]] = None) -> List[str]:
    """Walks the directories recursively to find all unprocessed image files.

    Args:
        directories: List of directories to search.
        limit: Max number of images to return.
        processed_paths: Set of already processed file paths (lowercase relative or absolute).

    Returns:
        List of absolute paths to unprocessed image files.
    """
    if processed_paths is None:
        processed_paths = set()
    
    image_extensions: Set[str] = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp"}
    images: List[str] = []
    skipped_count: int = 0
    
    for directory in directories:
        if not os.path.exists(directory):
            logger.warning(f"Scan directory does not exist: {directory}")
            continue
        logger.info(f"Walking directory: {directory}")
        for root, dirs, files in os.walk(directory):
            if "venv" in root or ".git" in root or "$RECYCLE.BIN" in root or "System Volume Information" in root:
                continue
            for file in files:
                if file.startswith("."):
                    continue
                ext = os.path.splitext(file)[1].lower()
                if ext in image_extensions:
                    full_path = os.path.join(root, file)
                    rel_path = get_relative_path(full_path, directories)
                    
                    # Skip files we've already described
                    if rel_path in processed_paths or full_path.lower() in processed_paths:
                        skipped_count += 1
                        continue
                    
                    images.append(full_path)
    
    images.sort()
    
    if limit:
        images = images[:limit]
    
    logger.info(f"Skipped {skipped_count} already processed images.")
    return images


def load_and_encode_image(img_path: str) -> Optional[str]:
    """Loads an image from disk, converts to RGB, and Base64-encodes the representation.

    Args:
        img_path: Absolute path to the image file.

    Returns:
        The Base64-encoded string representation if successful, None otherwise.
    """
    try:
        if os.path.exists(img_path) and os.path.getsize(img_path) == 0:
            logger.warning(f"Skipping empty 0-byte image file: {img_path}")
            return None
        img = Image.open(img_path).convert("RGB")
        img.load()
        buffered = io.BytesIO()
        img.save(buffered, format="JPEG", quality=90)
        img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
        return img_str
    except Exception as e:
        logger.warning(f"Skipping unreadable image {img_path}: {e}")
        return None


def inline_embed_metadata(file_path: str, summary_text: str, output_json: str) -> None:
    """Runs ExifTool to natively embed the summary description into the file metadata.

    Args:
        file_path: The absolute path to the image file.
        summary_text: The descriptive string to embed.
        output_json: Path to the JSON database output file.

    Returns:
        None
    """
    db_dir: str = os.path.dirname(os.path.abspath(output_json))
    arg_file_path: str = os.path.join(db_dir, f"exif_args_{uuid.uuid4().hex}.txt")
    
    try:
        with open(arg_file_path, "w", encoding="utf-8") as arg_f:
            arg_f.write(file_path + "\n")
            sidecar_path = file_path + ".xmp"
            if os.path.exists(sidecar_path) or file_path.lower().endswith(".webp"):
                arg_f.write(sidecar_path + "\n")
            
        cmd: List[str] = [
            EXIFTOOL_PATH,
            '-m', 
            '-charset', 'iptc=UTF8',
            '-charset', 'UTF8',      
            '-charset', 'filename=utf8',
            '-overwrite_original',
            f'-Caption-Abstract={summary_text}',
            f'-Description={summary_text}',
            f'-ImageDescription={summary_text}',
            '-@', arg_file_path
        ]
        
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        logger.info(f"EXIF embedded: {file_path}")
    except subprocess.CalledProcessError as e:
        err_msg: str = e.stderr.decode('utf-8', errors='replace').strip() if e.stderr else str(e)
        if "Bad Photoshop IRB resource" in err_msg:
             try:
                 fallback_cmd: List[str] = cmd.copy()
                 fallback_cmd.insert(4, "-Photoshop:All=")
                 subprocess.run(fallback_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
             except Exception:
                 pass
        elif "Temporary file already exists" in err_msg:
             tmp_file: str = file_path + "_exiftool_tmp"
             if os.path.exists(tmp_file):
                 try:
                     os.remove(tmp_file)
                     subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                 except Exception:
                     pass
        else:
             logger.warning(f"ExifTool warning for {file_path}: {err_msg}")
    except Exception as e:
         logger.debug(f"Failed inline EXIF write for {file_path}: {e}")
    finally:
        if os.path.exists(arg_file_path):
            try:
                os.remove(arg_file_path)
            except Exception:
                pass


def save_results(results: List[Dict[str, Union[str, List[str]]]], output_json: str) -> None:
    """Saves the descriptions database atomically to the JSON output file.

    Args:
        results: The current list of image descriptions.
        output_json: The absolute path to write the JSON file.

    Returns:
        None
    """
    with json_lock:
        temp_filename = f"{output_json}.tmp"
        try:
            with open(temp_filename, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=4)
            
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    os.replace(temp_filename, output_json)
                    logger.info(f"Saved JSON description database to disk ({len(results)} entries)")
                    break
                except PermissionError as pe:
                    if attempt < max_retries - 1:
                        logger.warning(f"Database file locked, retrying replace in 1.0s (attempt {attempt+1}/{max_retries})...")
                        time.sleep(1.0)
                    else:
                        raise pe
        except Exception as e:
            logger.error(f"Failed to save results to {output_json}: {e}")


def extract_json_payload(raw_text: str) -> Dict[str, Union[str, List[str]]]:
    """Cleans up markdown code fences and parses the JSON response.

    Args:
        raw_text: The raw output string from the model.

    Returns:
        A dictionary containing the parsed metadata keys.
    """
    cleaned: str = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        parsed: Dict[str, Union[str, List[str]]] = json.loads(cleaned)
        
        normalized: Dict[str, Union[str, List[str]]] = {}
        for key, val in parsed.items():
            k_lower = key.lower()
            if "subject" in k_lower:
                normalized["primary_subject"] = val
            elif "env" in k_lower:
                normalized["environment"] = val
            elif "tag" in k_lower:
                normalized["suggested_tags"] = val
            else:
                normalized[key] = val

        required_keys = ["primary_subject", "environment", "suggested_tags"]
        for key in required_keys:
            if key not in normalized:
                normalized[key] = "" if key != "suggested_tags" else []
        return normalized
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON from output: {e}. Raw text: {raw_text}")
        return {
            "primary_subject": raw_text,
            "environment": "Unknown",
            "suggested_tags": ["error-parsing-json"]
        }


def add_or_update_result(results_list: List[Dict[str, Union[str, List[str]]]], path: str, metadata: Dict[str, Union[str, List[str]]]) -> None:
    """Inserts or updates a photo description entry in the results list.

    Args:
        results_list: The active list of photo descriptions.
        path: The absolute path of the image.
        metadata: The dictionary containing VLM tags and description.

    Returns:
        None
    """
    for item in results_list:
        if item.get("full_path", "").lower() == path.lower():
            for key, val in metadata.items():
                item[key] = val
            return
    
    new_entry: Dict[str, Union[str, List[str]]] = {"full_path": path}
    new_entry.update(metadata)
    results_list.append(new_entry)


def process_batches(
    image_paths: List[str], 
    results: List[Dict[str, Union[str, List[str]]]], 
    prompt_path: str,
    batch_size: int,
    max_workers: int,
    output_json: str,
    embed_exif: bool = False
) -> None:
    """Processes images in batches using parallel loading and WSL2 backend VLM server.

    Args:
        image_paths: List of file paths to process.
        results: The mutable list holding all descriptions.
        prompt_path: The file path to prompt.txt loaded dynamically.
        batch_size: Grouping dimension for inference.
        max_workers: Parallel thread count for disk loads.
        output_json: The absolute path to write the output JSON file.
        embed_exif: If True, write metadata back to EXIF tags.

    Returns:
        None
    """
    # Import local wsl_client module dynamically
    import wsl_client

    total: int = len(image_paths)
    exif_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            next_batch_paths = image_paths[0:batch_size]
            next_batch_futures = {executor.submit(load_and_encode_image, path): path for path in next_batch_paths}
            
            for batch_start in range(0, total, batch_size):
                current_futures = next_batch_futures
                current_paths = next_batch_paths
                
                next_start = batch_start + batch_size
                next_batch_paths = image_paths[next_start:next_start + batch_size]
                next_batch_futures = {executor.submit(load_and_encode_image, path): path for path in next_batch_paths}
                
                logger.info(f"Processing batch {batch_start // batch_size + 1} / {(total + batch_size - 1) // batch_size}")
                
                # Dynamically load/refresh prompt instructions at runtime right before model invocation
                active_prompt: str = ""
                try:
                    if os.path.exists(prompt_path):
                        with open(prompt_path, "r", encoding="utf-8") as f:
                            active_prompt = f.read().strip()
                except Exception as pe:
                    logger.warning(f"Failed to dynamically load prompt from {prompt_path}: {pe}")
                
                if not active_prompt:
                    active_prompt = "Analyze this image and catalog its content."

                batch_b64s: List[str] = []
                valid_paths: List[str] = []
                
                for future in concurrent.futures.as_completed(current_futures):
                    path = current_futures[future]
                    img_b64 = future.result()
                    if img_b64 is not None:
                        batch_b64s.append(img_b64)
                        valid_paths.append(path)
                    else:
                        add_or_update_result(results, path, {
                            "primary_subject": "Error: Failed to open or encode image file.",
                            "environment": "Unknown",
                            "suggested_tags": ["error-loading-file"]
                        })
                
                if not batch_b64s:
                    continue
                    
                try:
                    raw_responses = wsl_client.query_vlm_server_base64(batch_b64s, active_prompt)
                    
                    for path, raw_text in zip(valid_paths, raw_responses):
                        metadata = extract_json_payload(raw_text)
                        logger.info(f"Processed: {path}")
                        
                        add_or_update_result(results, path, metadata)
                        
                        if embed_exif:
                            summary_text: str = (
                                f"Subject: {metadata.get('primary_subject', '')}\n"
                                f"Environment: {metadata.get('environment', '')}\n"
                                f"Tags: {', '.join(metadata.get('suggested_tags', []))}"
                            )
                            exif_executor.submit(inline_embed_metadata, path, summary_text, output_json)

                    save_results(results, output_json)

                except Exception as e:
                    logger.error(f"Batch generation failed: {e}", exc_info=True)
                    for path in valid_paths:
                        add_or_update_result(results, path, {
                            "primary_subject": f"Error: WSL2 Inference failed - {e}",
                            "environment": "Unknown",
                            "suggested_tags": ["error-server-inference"]
                        })
    finally:
        if embed_exif:
            logger.info("Waiting for background EXIF metadata embedding to complete...")
            exif_executor.shutdown(wait=True)
            logger.info("All EXIF metadata embedding completed.")
        else:
            exif_executor.shutdown(wait=False)


def main() -> None:
    """Main orchestrator function for the local VLM pipeline.

    Loads command line arguments, reads prompt files, loads the database cache,
    starts the VLM WSL2 server, and begins processing.

    Args:
        None

    Returns:
        None
    """
    import wsl_client

    logger.info("Starting Local VLM Photo Cataloger")
    
    parser = argparse.ArgumentParser(description="Modular Photo Describer Tool.")
    parser.add_argument(
        "--max-photos", 
        type=int, 
        default=int(os.environ.get("MAX_PHOTOS", 100)),
        help="Maximum number of new images to describe."
    )
    parser.add_argument(
        "--batch-size", 
        type=int, 
        default=2,
        help="Batch size for model evaluation."
    )
    parser.add_argument(
        "--max-workers", 
        type=int, 
        default=8,
        help="Number of background CPU worker threads for image loading."
    )
    parser.add_argument(
        "--dir",
        type=str,
        action="append",
        dest="dir",
        help="Directory to scan for images. Can be specified multiple times."
    )
    parser.add_argument(
        "--output",
        type=str,
        default=OUTPUT_JSON,
        help="Path to the output photo descriptions JSON database."
    )
    parser.add_argument(
        "--submitted-cache",
        type=str,
        default=SUBMITTED_CACHE,
        help="Path to the submitted photos cache file to avoid reprocessing."
    )
    parser.add_argument(
        "--embed-exif",
        action="store_true",
        help="Natively embed descriptions into image EXIF tags using exiftool."
    )
    parser.add_argument(
        "--file",
        type=str,
        help="A single image file path to process. Bypasses skip-cache."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-evaluation of all images in scanned directories."
    )
    args = parser.parse_args()

    prompt_path: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt.txt")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            prompt_text: str = f.read().strip()
            logger.info("Loaded prompt instructions from prompt.txt successfully.")
    else:
        logger.error(f"Failed to find prompt configuration at: {prompt_path}")
        sys.exit(1)

    results: List[Dict[str, Union[str, List[str]]]] = []
    
    # Load existing descriptions JSON database
    if os.path.exists(args.output):
        try:
            with open(args.output, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    raw_results = json.loads(content)
                    for item in raw_results:
                        if isinstance(item, dict):
                            subject: str = str(item.get("primary_subject", "")).lower()
                            tags: List[str] = [str(t).lower() for t in item.get("suggested_tags", [])]
                            
                            # Skip and retry previously failed entries
                            if (
                                not subject or 
                                "error:" in subject or 
                                "safety violation" in subject or 
                                "sorry" in subject or 
                                "please provide" in subject or 
                                "error-parsing-json" in tags
                            ):
                                continue
                            results.append(item)
                    
            if 'raw_results' in locals():
                retry_count = len(raw_results) - len(results)
                logger.info(f"Loaded {len(results)} valid descriptions. Retrying {retry_count} failures.")
            else:
                logger.info("Loaded 0 existing descriptions.")
        except Exception as e:
            logger.warning(f"Failed to load existing JSON: {e}. Starting fresh.")
            
    target_dirs: List[str] = args.dir if args.dir else PICTURE_DIRS
    if not target_dirs and not args.file:
        logger.error("Error: No directories to scan were specified. Use --dir or configure env.")
        sys.exit(1)

    processed_paths: Set[str] = set()
    if not args.force:
        for item in results:
            full_path = item.get("full_path", "")
            if isinstance(full_path, str):
                processed_paths.add(full_path.lower())
                rel_path = get_relative_path(full_path, target_dirs)
                processed_paths.add(rel_path)
            
        if args.submitted_cache and os.path.exists(args.submitted_cache):
            with open(args.submitted_cache, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        processed_paths.add(line.strip().replace("\\", "/").lower())

    if args.file:
        normalized_file = os.path.abspath(args.file)
        if os.path.isfile(normalized_file):
            images = [normalized_file]
            results = [item for item in results if str(item.get("full_path", "")).lower() != normalized_file.lower()]
            logger.info(f"Forcing single file processing for: {normalized_file}")
        else:
            logger.error(f"Provided path is not a file: {normalized_file}")
            sys.exit(1)
    else:
        logger.info(f"Scanning directories for up to {args.max_photos} unprocessed images...")
        images = get_image_files(target_dirs, limit=args.max_photos, processed_paths=processed_paths)
        images.sort()
        logger.info(f"Found {len(images)} new images to process.")
    
    if not images:
        logger.info("No new images found. All done!")
        return

    # Start the VLM model server inside WSL2 Docker
    if not wsl_client.start_wsl_server():
        logger.error("Failed to start WSL2 model server. Exiting.")
        sys.exit(1)
        
    logger.info("Commencing batch generation...")
    start_time = time.time()
    process_batches(
        images, 
        results, 
        prompt_path, 
        args.batch_size, 
        args.max_workers, 
        args.output,
        embed_exif=args.embed_exif
    )
    end_time = time.time()
    logger.info(f"PROCESSED: {len(images)} images in {end_time - start_time:.2f} seconds")
    logger.info(f"Completed processing. Operations successfully saved to {args.output}")


if __name__ == "__main__":
    main()

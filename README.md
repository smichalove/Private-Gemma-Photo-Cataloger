# Local Gemma 4 Offline Photo Cataloger

A private, offline vision-language-driven photo cataloging pipeline. This application scans directory trees recursively, analyzes images in parallel, generates structured descriptive metadata, and optionally embeds descriptions natively back into the image file EXIF headers.

It runs entirely offline on local hardware using Google's encoder-free **Gemma 4 12B IT** multimodal model inside a WSL2 Docker container with BitsAndBytes 4-bit quantization.

> [!NOTE]
> **Looking for a Cloud-Based Pipeline?**
> If you want to use Google Cloud (Vertex AI Batch Jobs with Gemini) instead of running a local model server, please refer to the dedicated public repository:
> **[Gemini Photo Batch Workflow](https://github.com/smichalove/Gemini_Photo_Batch_Workflow)**

### ⚖️ Choosing Between Local VLM and Cloud Batch

Depending on your hardware capability, budget, and description requirements, you can choose between this local offline pipeline and the cloud-based workflow:

| Feature | 💻 Local VLM (This Repo) | ☁️ Cloud Batch ([Gemini_Photo_Batch_Workflow](https://github.com/smichalove/Gemini_Photo_Batch_Workflow)) |
| :--- | :--- | :--- |
| **Primary Model** | Gemma 4 12B IT (Quantized 4-bit) | Gemini 2.5 Flash / Pro |
| **Cost** | **100% Free** (No API or cloud charges) | Paid GCP Vertex AI API usage |
| **Hardware** | NVIDIA GPU with **16GB+ VRAM** (e.g., RTX 5000/5080 Blackwell, RTX 4080, etc.) | Standard CPU / low-end systems (no local GPU needed) |
| **Speed** | Slower (limited by local GPU batch processing) | Extremely fast (processed in parallel by Vertex AI) |
| **Description Detail**| Structured JSON summaries (tags & environment) | Comprehensive, detailed descriptions & narrative paragraphs |
| **Data Privacy** | **Absolute** (all processing remains offline on-disk) | Images processed remotely via secure Google Cloud servers |

---

## 🛠️ System Architecture

```
                       ┌────────────────────────┐
                       │   Directory Crawler    │
                       └───────────┬────────────┘
                                   │
                                   ▼
                       ┌────────────────────────┐
                       │  Deduplication Check   │
                       └───────────┬────────────┘
                                   │
                                   ▼
                       ┌────────────────────────┐
                       │   Base64 Image Batch   │
                       └───────────┬────────────┘
                                   │
                                   ▼
                       ┌────────────────────────┐
                       │    WSL2 Docker VLM     │
                       │    (gemma-4-12B-it)    │
                       └───────────┬────────────┘
                                   │
                                   ▼
                       ┌────────────────────────┐
                       │ Output JSON Database   │
                       └───────────┬────────────┘
                                   │
                                   ▼
                       ┌────────────────────────┐
                       │ ExifTool Writer        │
                       └────────────────────────┘
```

---

## 🚀 Installation & Setup

### 1. Prerequisites

You must install **ExifTool** locally on the host machine:
*   **Windows**: Download `exiftool.exe` from the official website and add it to your system PATH (or specify its path in `.env` under `EXIFTOOL_PATH`).
*   **macOS / Linux**: Install via package manager (e.g. `brew install exiftool` or `sudo apt-get install exiftool`).

### 2. Environment Setup

1.  Clone this repository and navigate into it.
2.  Create and activate a Python virtual environment (`venv`) to keep your host environment isolated and clean:
    ```bash
    # Windows (PowerShell)
    python -m venv venv
    .\venv\Scripts\Activate.ps1

    # macOS / Linux
    python -m venv venv
    source venv/bin/activate
    ```
3.  Install the **host dependencies** (lightweight scripts for image base64 encoding, network calls, and EXIF embedding):
    ```bash
    pip install -r local/requirements.txt
    ```
4.  Copy `.env.example` to `.env`:
    ```bash
    cp .env.example .env
    ```
5.  Configure `.env` with your photo directories and execution parameters. Here is a baseline example of the configuration:
    ```env
    # Comma-separated list of directories to scan (e.g., C:\Pictures,D:\Photos)
    PICTURE_DIRECTORIES=C:\path\to\your\pictures,D:\another\folder

    # Database and tracking paths
    OUTPUT_DATABASE_PATH=photo_descriptions.json
    SUBMITTED_CACHE_PATH=submitted_photos_cache.txt

    # Path to the ExifTool executable (defaults to "exiftool" if in system PATH)
    EXIFTOOL_PATH=exiftool

    # Optional Hugging Face Token (only needed for initial model download if weights aren't cached)
    HF_TOKEN=
    ```

> [!NOTE]
> **Host vs. Server Dependency Isolation**
> The host machine only handles lightweight coordination, file crawling, and metadata writing. Heavy dependencies (such as `torch`, `transformers`, and `bitsandbytes`) are isolated inside the WSL2 Docker container to keep the host environment clean and prevent PyTorch version conflicts.

### 3. GPU Hardware & Docker Container Setup (WSL2)

*   **Hardware Requirement**: An NVIDIA GPU with at least **16GB of VRAM** (e.g., Blackwell generation RTX 5000 / RTX 5080, Ada Lovelace RTX 4080, or Ampere/Turing equivalents).
*   Ensure **WSL2** and **Docker Desktop** (with the WSL2 backend enabled) are installed on your Windows host.
    - If you need to set up or configure WSL2, follow the official [Microsoft WSL2 Installation Guide](https://learn.microsoft.com/en-us/windows/wsl/install) to provision your subsystem.
*   **Docker User Permissions inside WSL2**: The WSL user account (configured via `WSL_USER` in `.env`) must be allowed to run Docker commands without prefixing `sudo`:
    - **Option A (Recommended)**: Add your WSL2 Linux user to the `docker` group:
      ```bash
      sudo usermod -aG docker your-wsl-username
      ```
      *(Reopen your terminal or run `newgrp docker` inside WSL for the changes to apply).*
    - **Option B (Direct Root)**: Set `WSL_USER=root` in your local `.env` file to execute all background controls as the root account directly.

> [!IMPORTANT]
> **Performance Benefits of WSL2 Containerization**
> Running the Gemma 4 VLM server inside a WSL2 Docker container provides major performance advantages:
> 1. **Zero-overhead GPU access**: Leveraging the NVIDIA Container Toolkit allows the container to run directly on the host GPU at native speeds.
> 2. **Accelerated model load times**: Quantized 4-bit weights load and compile significantly faster inside Linux's native filesystem and memory architectures compared to native Windows environments.

Launch the model server container mapping port 8000 and mounting your code directory:
```bash
docker run -d --name trt_llm_build \
  --gpus all \
  -p 8000:8000 \
  -v /absolute/path/to/project:/workspace \
  -v /absolute/path/to/models:/workspace/models \
  --restart unless-stopped \
  nvcr.io/nvidia/tensorrt-llm/release:1.2.0
```

Install the VLM server-specific dependencies inside the running container:
```bash
docker exec -it trt_llm_build pip install -r /workspace/local/requirements_server.txt
```

To start the model server and pre-load VLM weights in advance, run the provided start script:
```bash
# Run from PowerShell / Command Prompt:
.\start_server.bat
```

To stop the model server and release all GPU VRAM when you are finished cataloging, run the provided stop script:
```bash
# Run from PowerShell / Command Prompt:
.\stop_server.bat
```

> [!TIP]
> **Non-Blackwell Platform Customization**
> The server initialization script ([wsl_client.py](local/wsl_client.py)) defaults to parameters optimized for Blackwell GPUs (like `BNB_CUDA_VERSION=130` on line 124).
> If you are running on an older generation (e.g., Ada Lovelace RTX 40-series, Ampere RTX 30-series, or older CUDA setups), you can edit the `BNB_CUDA_VERSION` environment flag inside [wsl_client.py](local/wsl_client.py#L124) to match your GPU's CUDA runtime version (e.g., `121` or `118`).

---

## 💻 Usage

### 1. Run via Batch Script (Windows Recommended)
You can run the pipeline directly using the provided Windows batch file. This automatically activates your `venv` and runs the orchestrator:
```bash
# Run from PowerShell / Command Prompt:
.\run_local_pipeline.bat

# You can also pass any CLI arguments directly to the batch file:
.\run_local_pipeline.bat --max-photos 50 --batch-size 1
```

#### Run on a Single Image
To force VLM evaluation and EXIF embedding on a single specific image (bypassing caches), run the prompt script:
```bash
# Run from PowerShell / Command Prompt:
.\run_single.bat
```
This script will prompt you to type or drag-and-drop the absolute path to your target image file.

### 2. Run via Python Command
Or run the orchestrator script manually:
```bash
python local/describe_photos.py --embed-exif --batch-size 2
```

> [!IMPORTANT]
> **VRAM & Batch Size Tuning**
> - The pipeline is default-tuned for a **12GB VRAM Blackwell GPU** (using a default batch size of `2` to prevent Out-of-Memory (OOM) crashes).
> - **Batch size tuning is required per GPU profile**: If your local model server crashes with CUDA OOM errors during batch evaluation, reduce the batch size (e.g., to `--batch-size 1`).
> - If you have a high-end card with larger VRAM (like a 16GB RTX 5080/4080 or 24GB RTX 5090/4090), you can increase the batch size (e.g., `--batch-size 4` or `--batch-size 8`) to accelerate pipeline performance.

This script automatically:
1. Spins up the WSL2 container.
2. Launches the FastAPI server inside it (if not already running).
3. Crawls your configured image folders recursively.
4. Feeds Base64 image payloads in parallel CPU batches to the VLM.
5. Serializes output tags to the local `photo_descriptions.json` database.
6. Natively embeds descriptions back to image EXIF file headers using ExifTool.

### ⚙️ Command-Line Arguments
Override default settings using the following runtime options:

| Option | Type | Default Value | Description |
| :--- | :--- | :--- | :--- |
| `--dir` | `str` (repeating) | None / env `PICTURE_DIRECTORIES` | Target directory to scan. Can be specified multiple times. |
| `--max-photos` | `int` | `100` / env `MAX_PHOTOS` | Maximum number of new images to describe in this run. |
| `--batch-size` | `int` | `2` | Batch size for model evaluation. Tune down to `1` if you encounter CUDA OOM errors. |
| `--max-workers` | `int` | `8` | Number of background CPU worker threads for fast image loading. |
| `--output` | `str` | `"photo_descriptions.json"` | Path to save the cataloged descriptions database. |
| `--submitted-cache` | `str` | `"submitted_photos_cache.txt"` | Log file tracking already-evaluated photos. |
| `--embed-exif` | Flag | `False` | Triggers background ExifTool execution to write description headers. |
| `--file` | `str` | None | Processes a single target file directly, bypassing skip caches. |
| `--force` | Flag | `False` | Forces re-evaluation of all target files, ignoring existing database records. |

---

## 🏷️ Standalone Metadata Embedding

The metadata embedding script (`embed_metadata.py`) reads the serialized description JSON database and calls ExifTool in optimized multi-threaded batches.

To trigger embedding manually on already-described photos:
```bash
python embed_metadata.py
```
This script reads the `.env` settings, normalizes file paths across operating systems via relative indexing, and writes descriptions to:
*   `Caption-Abstract` (IPTC)
*   `Description` (XMP)
*   `ImageDescription` (EXIF)

---

## 🛠️ Lossless Video Frame Extractor

This repository includes a standalone utility inside the [utilities/](utilities/) folder that extracts individual video frames between specific timecodes as uncompressed `.bmp` files to preserve lossless clarity for VLM analysis.

To run it:
```bash
# Run from PowerShell / Command Prompt:
.\utilities\run_extract_frames.bat
```
This utility will:
1. Auto-detect video captures in common folders or prompt you for the target file path.
2. Ask for start and end timecodes (supports formats like `HH:MM:SS`, `MM:SS`, or raw seconds).
3. Export every frame in the range as a high-fidelity lossless `.bmp` image inside your target folder.

---

## 🧪 Testing

Run the mock-based unit tests to verify script logic:
```bash
python -m unittest local/tests/test_describe_photos.py
```

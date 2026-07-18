# Release Notes: Local Gemma Photo Cataloger v2.1.0 (Major Release)

This major release (**Version 2.1.0**) introduces the **Modular Compute Fabric** framework, enabling high-performance parallel image indexing across distributed local network nodes. Additionally, this release implements full support for the **PostgreSQL** database backend, transitions tests to the root project namespace, and officially deprecates the legacy SQLite backend.

---

## 🚀 Key Features and Highlights

### 1. LAN Compute Fabric Integration (`fabric_manager.py`)
*   **Dynamic Node Discovery**: Periodically polls the database node registry to discover and maintain a pool of online vision-language model (VLM) servers.
*   **Failover & Watchlist Monitoring**: Implements background monitor threads. If a node becomes unresponsive during batch evaluation, it is automatically demoted to a watchlist, allowing other active worker nodes to take over workloads.
*   **ARP & Ping Hot-Plug Warmup**: Resolves hostnames to IP addresses rapidly using a local static LAN cache, falling back to sending 1-second pings to warm OS ARP caches and reading `/proc/net/arp` (on Linux) or `arp -a` (on Windows/macOS) directly.
*   **Blackwell Generation Tuning**: The connection lifecycle supports Blackwell GPU parameter overrides (`BNB_CUDA_VERSION=130`) out-of-the-box.

### 2. Primary PostgreSQL Database Migration
*   **PostgreSQL Support**: The pipeline now connects natively to PostgreSQL database clusters to manage high-throughput metadata ingestion and the compute fabric registry.
*   **SQLite Deprecation**: The local SQLite backend (`photo_catalog.db`) is now officially deprecated. It remains in the codebase for legacy backward compatibility but is no longer recommended for production-grade indexing.
*   **Sanitized Seeding**: Standardized schema creation and seeding scripts (`sql/schema/`) are included for both SQLite and PostgreSQL.

### 3. Advanced Chat REPL Query Correction (`db_chat_repl.py`)
*   **Dynamic SQL Fixing**: The database chat REPL client dynamically intercepts Python-level database execution errors and prompts the local/remote VLM to correct SQL syntax (via `sql_fix_prompt.txt`), automatically re-executing corrected queries.
*   **Multi-Model Playback Routing**: Integrates standard JRiver Media Center MCWS API controls to trigger playlist queues and play audio/video files directly from search index results.

### 4. Codebase Sanitization & Security Mappings
*   **Hostname & Subnet Obfuscation**: All hardcoded workstation names (`i7office`, `ubunto-giga`, etc.) and home subnet addresses (`192.168.8.x`) have been removed from the public codebase and replaced with generic placeholder configurations (`192.168.1.100` and `workstation-host`).
*   **Path Mount Parameterization**: Hardcoded path prefix overrides are replaced with configuration variables and standard environmental defaults (`os.environ.get`).

### 5. Testing Namespace Alignment
*   **Root `tests/` Directory**: Test suites (`test_describe_photos.py` and `test_db_chat_repl.py`) have been moved from the legacy `local/tests` directory to the project root namespace (`tests/`) to isolate test executions.
*   **Sanitized Test Suites**: Sanitized IP and path string comparisons inside test assertions.

---

## 🛠️ Configuration Changes

To upgrade your configuration, copy the updated variables from `.env.example` to your `.env` file:
*   Configure `DB_BACKEND=postgresql`.
*   Set your PostgreSQL connection details: `DB_HOST`, `DB_PORT`, `DB_NAME`, and `DB_USER`.
*   Run the schema builder to configure your database tables:
    ```bash
    python local/instantiate_fabric_db.py
    ```

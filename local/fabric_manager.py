#!/usr/bin/env python3
"""
fabric_manager.py

Purpose:
    Centralized, thread-safe, and dynamic compute fabric registry for Google Gemma.
    Loads compute node configurations from the database (PostgreSQL/SQLite), performs
    concurrent turn-up, manages a background watchlist to support dynamic hot-plugging/recovery
    of down hosts, and implements ARP/ping-based hostname-to-IP resolution.

Usage:
    from fabric_manager import FabricManager
    manager = FabricManager()
    active_nodes = manager.get_nodes_for_workload(model_name="gemma4:12b", service_name="ollama")
"""

import os
import sys
import time
import socket
import subprocess
import re
import json
import platform
import threading
import logging
import psycopg2
import sqlite3
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Set, Tuple, Any
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

logger = logging.getLogger("gemma_cataloger.fabric")

# Ensure PROJECT_DIR is resolved
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(PROJECT_DIR, "auth", ".env")):
    load_dotenv(os.path.join(PROJECT_DIR, "auth", ".env"))
elif os.path.exists(os.path.join(os.path.dirname(PROJECT_DIR), ".env")):
    load_dotenv(os.path.join(os.path.dirname(PROJECT_DIR), ".env"))
else:
    load_dotenv()


@dataclass
class ComputeNode:
    hostname: str
    node_type: str                  # "workstation", "gpu_server", "jetson", "mac_client"
    supported_models: List[str]     # e.g., ["gemma4:12b", "gemma4:31b"]
    services: Dict[str, int]        # service_name -> port (e.g., {"ollama": 11434, "postgres": 5432})
    max_batch_size: int
    is_active: bool                 # Admin flag in DB
    description: str
    resolved_ip: Optional[str] = None
    latency_ms: Optional[float] = None
    is_online: bool = False         # Runtime health status

    def get_service_url(self, service_name: str) -> Optional[str]:
        """Constructs the absolute service URL for a given service name."""
        port = self.services.get(service_name)
        if not port:
            return None
        ip = self.resolved_ip or self.hostname
        return f"http://{ip}:{port}"


def resolve_hostname_to_ip(hostname: str) -> str:
    """Resolves a LAN hostname to an IPv4 address.

    0. Fast-path static LAN cache (bypasses slow DNS queries).
    1. Standard DNS/mDNS resolution (including .local fallback).
    2. Fallback to sending a fast 1-second ping to warm local OS ARP cache.
    3. Direct reading of kernel ARP tables (/proc/net/arp) on Linux or 'arp -a' shell output.
    """
    # 0. Fast-path static LAN cache (example placeholders)
    lan_cache = {
        "workstation-host": "192.168.1.100",
        "remote-gpu-server": "192.168.1.101",
        "remote-ollama-node": "192.168.1.102",
        "edge-jetson-node": "192.168.1.103",
        "localhost": "127.0.0.1",
        "127.0.0.1": "127.0.0.1"
    }
    
    key = hostname.lower().strip()
    if key in lan_cache:
        return lan_cache[key]

    # 1. Try standard DNS / mDNS resolution
    for name in [hostname, f"{hostname}.local"]:
        try:
            ip = socket.gethostbyname(name)
            if ip and ip != "127.0.0.1" and not ip.startswith("127."):
                return ip
        except socket.gaierror:
            continue

    # 2. Warm up local ARP cache by sending a single ping with a 1-second (1000ms) timeout
    system_os = platform.system().lower()
    if system_os == "windows":
        ping_cmd = ["ping", "-n", "1", "-w", "1000", hostname]
    else:
        ping_cmd = ["ping", "-c", "1", "-W", "1", hostname]

    try:
        subprocess.run(ping_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    # 3. Read system ARP tables to translate MAC/hostname to IP
    try:
        if system_os == "linux":
            # Direct kernel ARP cache read for maximum performance and file-lock safety
            if os.path.exists("/proc/net/arp"):
                with open("/proc/net/arp", "r") as f:
                    lines = f.readlines()[1:]  # Skip header
                    for line in lines:
                        parts = line.split()
                        if len(parts) >= 4 and (parts[0] == hostname or hostname in parts[3] or hostname.lower() in parts[0].lower()):
                            return parts[0]
        else:
            # Shell command fallback for Windows/macOS
            arp_out = subprocess.check_output(["arp", "-a"], text=True)
            for line in arp_out.splitlines():
                if hostname.lower() in line.lower():
                    # Extract first IPv4 pattern
                    match = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", line)
                    if match:
                        return match.group(1)
    except Exception:
        pass

    # Default fallback to original hostname to let routing try its best
    return hostname


def check_port_open(ip: str, port: int, timeout: float = 2.0) -> bool:
    """Checks if a TCP port is open on a given IP with a fast connection handshake timeout."""
    try:
        test_ip = "127.0.0.1" if ip in ("localhost", "127.0.0.1") else ip
        with socket.create_connection((test_ip, port), timeout=timeout) as s:
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def get_db_conn_params() -> dict:
    """Constructs database connection parameters for PostgreSQL."""
    db_host = os.getenv("DB_HOST", "localhost")
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


class FabricManager:
    """Thread-safe Compute Fabric Manager to discover, query, and monitor active model servers."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(FabricManager, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, watch_interval_seconds: int = 180):
        if self._initialized:
            return
        
        self.watch_interval = watch_interval_seconds
        self.nodes: Dict[str, ComputeNode] = {}
        self.active_pool: Set[str] = set()       # Hostnames of online, active nodes
        self.watchlist: Set[str] = set()         # Hostnames of currently offline/down nodes
        
        self.pool_lock = threading.Lock()
        self._initial_probing_done = threading.Event()
        self.backend = os.getenv("DB_BACKEND", "sqlite").lower()
        self._initialized = True
        
        # Concurrent Turn-up
        logger.info("[FABRIC] Initializing Compute Fabric registry...")
        self.reload_and_probe_fabric()

        # Start the background watcher thread
        self._stop_watcher = threading.Event()
        self._watcher_thread = threading.Thread(
            target=self._watcher_loop, 
            name="FabricWatcherDaemon", 
            daemon=True
        )
        self._watcher_thread.start()
        logger.info(f"[FABRIC] Daemon monitor started (Polling watchlist every {self.watch_interval}s).")


    def reload_and_probe_fabric(self, run_probing_async: bool = True) -> None:
        """Loads compute nodes from database and performs concurrent checks (asynchronously by default)."""
        loaded_nodes = []
        
        if self.backend == "postgresql":
            db_params = get_db_conn_params()
            conn = None
            try:
                conn = psycopg2.connect(**db_params)
                cur = conn.cursor()
                cur.execute("""
                    SELECT hostname, node_type, supported_models, services, max_batch_size, is_active, description 
                    FROM compute_nodes
                """)
                rows = cur.fetchall()
                for r in rows:
                    hostname, node_type, models_raw, services_raw, max_batch, is_active, desc = r
                    models = models_raw if isinstance(models_raw, list) else json.loads(models_raw) if isinstance(models_raw, str) else []
                    services_list = services_raw if isinstance(services_raw, list) else json.loads(services_raw) if isinstance(services_raw, str) else []
                    services_map = {srv["name"]: srv["port"] for srv in services_list if "name" in srv and "port" in srv}
                    
                    node = ComputeNode(
                        hostname=hostname,
                        node_type=node_type,
                        supported_models=models,
                        services=services_map,
                        max_batch_size=max_batch,
                        is_active=bool(is_active),
                        description=desc or ""
                    )
                    loaded_nodes.append(node)
                cur.close()
            except Exception as e:
                logger.warning(f"[FABRIC] Failed to load compute registry from PostgreSQL: {e}")
                return
            finally:
                if conn:
                    conn.close()
        else:
            # SQLite fallback
            sqlite_db = os.getenv("OUTPUT_DATABASE_SQLITE", os.path.join(PROJECT_DIR, "photo_catalog.db"))
            if not os.path.exists(sqlite_db):
                logger.info(f"[FABRIC] SQLite database not found at {sqlite_db}. Skipping node load.")
                return
            conn = None
            try:
                conn = sqlite3.connect(sqlite_db)
                cur = conn.cursor()
                cur.execute("""
                    SELECT hostname, node_type, supported_models, services, max_batch_size, is_active, description 
                    FROM compute_nodes
                """)
                rows = cur.fetchall()
                for r in rows:
                    hostname, node_type, models_raw, services_raw, max_batch, is_active, desc = r
                    models = json.loads(models_raw) if isinstance(models_raw, str) else []
                    services_list = json.loads(services_raw) if isinstance(services_raw, str) else []
                    services_map = {srv["name"]: srv["port"] for srv in services_list if "name" in srv and "port" in srv}
                    
                    node = ComputeNode(
                        hostname=hostname,
                        node_type=node_type,
                        supported_models=models,
                        services=services_map,
                        max_batch_size=max_batch,
                        is_active=bool(is_active),
                        description=desc or ""
                    )
                    loaded_nodes.append(node)
                cur.close()
            except Exception as e:
                logger.warning(f"[FABRIC] Failed to load compute registry from SQLite: {e}")
                return
            finally:
                if conn:
                    conn.close()

        with self.pool_lock:
            self.nodes.clear()
            self.active_pool.clear()
            self.watchlist.clear()
            
            for node in loaded_nodes:
                self.nodes[node.hostname] = node
                if node.is_active:
                    is_local_host = node.hostname.lower() in ("localhost", "127.0.0.1") or node.hostname.lower() == socket.gethostname().lower()
                    if is_local_host:
                        node.resolved_ip = "127.0.0.1"
                        ports_to_check = list(node.services.values())
                        services_up = False
                        for port in ports_to_check:
                            if check_port_open("127.0.0.1", port, timeout=0.1):
                                services_up = True
                                break
                        node.is_online = services_up
                        node.latency_ms = 0.1
                        if node.is_online:
                            self.active_pool.add(node.hostname)
                        else:
                            self.watchlist.add(node.hostname)
                    else:
                        node.is_online = False
                        self.watchlist.add(node.hostname)

        # Trigger full parallel health check in a background thread to prevent startup latency
        if run_probing_async:
            probing_thread = threading.Thread(
                target=self._initial_probing_worker,
                args=(loaded_nodes,),
                name="FabricInitialProber",
                daemon=True
            )
            probing_thread.start()
        else:
            self._run_full_probing(loaded_nodes)

    def _initial_probing_worker(self, loaded_nodes: List[ComputeNode]) -> None:
        """Worker thread to run full initial probing in the background."""
        time.sleep(0.1) # Yield CPU briefly
        self._run_full_probing(loaded_nodes)

    def wait_for_discovery(self, timeout: float = 2.0) -> None:
        """Wait for the initial background probing of registry nodes to complete (up to timeout)."""
        self._initial_probing_done.wait(timeout)

    def _run_full_probing(self, loaded_nodes: List[ComputeNode]) -> None:
        """Executes full concurrent network and service port probing on all loaded nodes."""
        if not loaded_nodes:
            self._initial_probing_done.set()
            return
        
        try:
            with ThreadPoolExecutor(max_workers=len(loaded_nodes) or 1) as executor:
                probed_nodes = list(executor.map(self._probe_node_health, loaded_nodes))

            with self.pool_lock:
                self.active_pool.clear()
                self.watchlist.clear()
                
                for node in probed_nodes:
                    self.nodes[node.hostname] = node
                    if node.is_active:
                        if node.is_online:
                            self.active_pool.add(node.hostname)
                        else:
                            self.watchlist.add(node.hostname)
                logger.info(f"[FABRIC] Completed asynchronous network probing of {len(probed_nodes)} registry nodes.")
        finally:
            self._initial_probing_done.set()


    def _probe_node_health(self, node: ComputeNode) -> ComputeNode:
        """Probes a single node's network and service ports. (Self-contained for parallel executors)."""
        if not node.is_active:
            node.is_online = False
            return node

        t0 = time.time()
        node.resolved_ip = resolve_hostname_to_ip(node.hostname)
        
        system_os = platform.system().lower()
        ping_target = node.resolved_ip or node.hostname
        ping_cmd = ["ping", "-c", "1", "-W", "1", ping_target] if system_os != "windows" else ["ping", "-n", "1", "-w", "1000", ping_target]
        is_reachable = False
        try:
            res = subprocess.run(ping_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            is_reachable = (res.returncode == 0)
        except Exception:
            pass

        node.latency_ms = round((time.time() - t0) * 1000, 2)

        ports_to_check = list(node.services.values())
        if not ports_to_check:
            node.is_online = is_reachable
        else:
            services_up = False
            for port in ports_to_check:
                if check_port_open(node.resolved_ip, port, timeout=1.5):
                    services_up = True
                    break
            node.is_online = services_up

        return node

    def get_active_nodes(self) -> List[ComputeNode]:
        """Returns a thread-safe snapshot of all currently online and active compute nodes."""
        with self.pool_lock:
            return [self.nodes[host] for host in self.active_pool if host in self.nodes]

    def get_nodes_for_workload(self, model_name: str, service_name: str) -> List[ComputeNode]:
        """Returns online nodes supporting a specific model name and listening service port.

        Automatically orders by capacity (max_batch_size) descending and latency ascending.
        """
        with self.pool_lock:
            matching = []
            for hostname in self.active_pool:
                node = self.nodes.get(hostname)
                if not node:
                    continue
                model_matches = any(model_name.lower() in m.lower() for m in node.supported_models)
                service_matches = (service_name.lower() in node.services)
                
                if model_matches and service_matches:
                    matching.append(node)
            
            matching.sort(key=lambda n: (-n.max_batch_size, n.latency_ms or 9999))
            return matching

    def mark_node_failed(self, hostname: str) -> None:
        """Thread-safe demotion of an active worker to the watchlist."""
        with self.pool_lock:
            if hostname in self.active_pool:
                self.active_pool.remove(hostname)
                self.watchlist.add(hostname)
                if hostname in self.nodes:
                    self.nodes[hostname].is_online = False
                logger.warning(f"[FABRIC] [DEMOTE] Node '{hostname}' became unresponsive. Moved to watchlist.")

    def _watcher_loop(self) -> None:
        """Daemon thread loop executing periodic health checks."""
        while not self._stop_watcher.is_set():
            self._stop_watcher.wait(self.watch_interval)
            if self._stop_watcher.is_set():
                break

            with self.pool_lock:
                current_watchlist = list(self.watchlist)
                current_active = list(self.active_pool)

            # 1. Probe the watchlist to find newly powered-on nodes (Hot-Plugs)
            if current_watchlist:
                nodes_to_probe = []
                with self.pool_lock:
                    for host in current_watchlist:
                        if host in self.nodes:
                            nodes_to_probe.append(self.nodes[host])

                with ThreadPoolExecutor(max_workers=len(nodes_to_probe) or 1) as executor:
                    probed_results = list(executor.map(self._probe_node_health, nodes_to_probe))

                with self.pool_lock:
                    for node in probed_results:
                        if node.is_online:
                            self.watchlist.remove(node.hostname)
                            self.active_pool.add(node.hostname)
                            logger.info(f"[FABRIC] [HOT-PLUG] Node '{node.hostname}' is now ONLINE. Hot-plugged back into active pool!")

            # 2. Run a fast connection health check on active nodes to prevent silent lockups
            if current_active:
                active_to_probe = []
                with self.pool_lock:
                    for host in current_active:
                        if host in self.nodes:
                            active_to_probe.append(self.nodes[host])

                with ThreadPoolExecutor(max_workers=len(active_to_probe) or 1) as executor:
                    active_results = list(executor.map(self._probe_node_health, active_to_probe))

                with self.pool_lock:
                    for node in active_results:
                        if not node.is_online and node.hostname in self.active_pool:
                            self.active_pool.remove(node.hostname)
                            self.watchlist.add(node.hostname)
                            logger.warning(f"[FABRIC] [WATCHER] Active node '{node.hostname}' went offline. Moved to watchlist.")

    def close(self) -> None:
        """Gracefully stops the background watcher thread."""
        self._stop_watcher.set()
        if self._watcher_thread.is_alive():
            self._watcher_thread.join(timeout=1.0)


if __name__ == "__main__":
    print("=====================================================================")
    print("  Compute Fabric Manager Integration & Status Check")
    print("=====================================================================")
    
    t_start = time.time()
    manager = FabricManager(watch_interval_seconds=10)
    duration = time.time() - t_start
    
    print(f"\nCompleted parallel fabric discovery in {duration:.2f} seconds.")
    print("---------------------------------------------------------------------")
    
    active_workers = manager.get_active_nodes()
    print(f"Online Fabric Nodes ({len(active_workers)}):")
    for n in active_workers:
        ip_str = str(n.resolved_ip or "")
        latency = n.latency_ms if n.latency_ms is not None else 0.0
        print(f"  🟢 {n.hostname:15s} | Type: {n.node_type:11s} | IP: {ip_str:15s} | Latency: {latency:6.1f}ms | Models: {n.supported_models}")
        
    down_workers = []
    with manager.pool_lock:
        for host in manager.watchlist:
            if host in manager.nodes:
                down_workers.append(manager.nodes[host])
                
    if down_workers:
        print(f"\nOffline Fabric Watchlist ({len(down_workers)}):")
        for n in down_workers:
            ip_str = str(n.resolved_ip or "")
            print(f"  🔴 {n.hostname:15s} | Type: {n.node_type:11s} | IP: {ip_str:15s} | Models: {n.supported_models}")
            
    print("=====================================================================")
    manager.close()

"""Utility module for cross-platform path resolution and normalization."""

import os
import sys
from typing import Optional

def is_wsl() -> bool:
    """Checks if the script is running inside WSL (Windows Subsystem for Linux).

    Args:
        None

    Returns:
        True if running in WSL, False otherwise.
    """
    if sys.platform != "linux":
        return False
    try:
        with open("/proc/version", "r") as f:
            return "microsoft" in f.read().lower()
    except Exception:
        return False

def get_wsl_host_ip() -> Optional[str]:
    """Retrieves the WSL2 host IP address from the default route or resolv.conf.

    Args:
        None

    Returns:
        The host gateway IP address as a string, or None if it cannot be resolved.
    """
    if not is_wsl():
        return None
    try:
        with open("/proc/net/route", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) > 2 and parts[1] == "00000000":
                    gw_hex = parts[2]
                    return f"{int(gw_hex[6:8], 16)}.{int(gw_hex[4:6], 16)}.{int(gw_hex[2:4], 16)}.{int(gw_hex[0:2], 16)}"
    except Exception:
        pass
    if os.path.exists("/etc/resolv.conf"):
        try:
            with open("/etc/resolv.conf", "r") as f:
                for line in f:
                    if line.strip().startswith("nameserver"):
                        parts = line.split()
                        if len(parts) > 1:
                            return parts[1].strip()
        except Exception:
            pass
    return None

def resolve_local_path(path: str) -> str:
    """Translates absolute file paths between Windows, WSL/Docker, and macOS formats.

    This function resolves path discrepancies across development environments. Because the
    primary database ingest pipeline runs on a Windows workstation and stores paths using
    Windows layout format (e.g. H:\\...), a macOS or WSL client accessing the PostgreSQL
    database remotely must dynamically translate these targets to their local filesystem mount
    points (e.g. /Volumes/HDrive/... or /mnt/h/...) so that files can be verified or launched.

    Args:
        path: The absolute database file path (typically starting with Windows H: drive prefix).

    Returns:
        The resolved absolute path string normalized for the current client environment.
    """
    if not path:
        return path
    p = path
    # If running on macOS
    if sys.platform == "darwin":
        if len(p) >= 2 and p[1] == ":":
            drive = p[0].lower()
            if drive == "d":
                p = "/Volumes/d-drive" + p[2:].replace("\\", "/")
            else:
                p = "/Volumes/HDrive" + p[2:].replace("\\", "/")
        elif p.lower().startswith("/workspace"):
            p = "/Volumes/HDrive" + p[10:]
    # If running on Linux (WSL or Docker)
    elif sys.platform.startswith("linux"):
        # Check if we are inside the docker workspace container
        in_docker = os.path.exists("/workspace")
        if len(p) >= 2 and p[1] == ":":
            drive = p[0].lower()
            if in_docker:
                p = "/workspace" + p[2:].replace("\\", "/")
            else:
                p = f"/mnt/{drive}" + p[2:].replace("\\", "/")
        elif p.lower().startswith("/volumes/d-drive"):
            if in_docker:
                p = "/workspace" + p[16:]
            else:
                p = "/mnt/d" + p[16:]
        elif p.lower().startswith("/volumes/hdrive"):
            if in_docker:
                p = "/workspace" + p[15:]
            else:
                p = "/mnt/h" + p[15:]
    # If running on Windows
    elif sys.platform == "win32":
        if p.lower().startswith("/volumes/d-drive"):
            p = "D:" + p[16:].replace("/", "\\")
        elif p.lower().startswith("/volumes/hdrive"):
            p = "H:" + p[15:].replace("/", "\\")
        elif p.lower().startswith("/workspace"):
            p = "H:" + p[10:].replace("/", "\\")

    # Self-healing fallback: if the resolved path does not exist, check other active mounts in /Volumes
    if sys.platform == "darwin" and not os.path.exists(p):
        rel = unify_path(path)
        try:
            for volume in os.listdir("/Volumes"):
                vol_path = os.path.join("/Volumes", volume, rel)
                if os.path.exists(vol_path):
                    p = vol_path
                    break
        except Exception:
            pass
    # Self-healing fallback: if running on Linux/WSL and the resolved path does not exist, check /mnt mounts
    elif sys.platform.startswith("linux") and not os.path.exists(p) and not os.path.exists("/workspace"):
        rel = unify_path(path)
        try:
            for mnt in os.listdir("/mnt"):
                mnt_path = os.path.join("/mnt", mnt, rel)
                if os.path.exists(mnt_path):
                    p = mnt_path
                    break
        except Exception:
            pass
    return p


def unify_path(path: str) -> str:
    """Sanitizes and isolates a relative path key to enable cross-platform media matching.

    This function converts absolute paths to relative paths by removing operating system
    specific mounts (like /Volumes/HDrive, /Volumes/d-drive, /mnt/h, /mnt/d, or H:) and project root directories.
    This normalization allows different servers and workflows (e.g., JRiver metadata syncing,
    catalog audits, or duplicate checks) to match file records cleanly using a platform-independent,
    lowercase relative path index.

    Args:
        path: The absolute path string (Windows, macOS, or Linux format).

    Returns:
        A normalized, lowercase relative path string.
    """
    p = path.replace("\\", "/").lower()
    while "//" in p:
        p = p.replace("//", "/")
    if len(p) >= 2 and p[1] == ':':
        p = p[2:]
    
    # Strip well-known mount and project root prefixes
    for prefix in ["/volumes/hdrive", "/volumes/d-drive", "/mnt/h", "/mnt/d", "/workspace", "/wan_project"]:
        if p.startswith(prefix):
            p = p[len(prefix):]
        if p.startswith("/wan_project"):
            p = p[12:]
            
    return p.lstrip("/")


def open_file(path: str) -> bool:
    """Resolves path formatting and opens a media file using the client OS default viewer.

    This function acts as the central launcher for media files on client machines.
    It contains platform-specific hardware safety guards and custom application routing rules:
    1. DAC Protection: On macOS, it blocks direct playback of 1-bit DSD audio streams (.dsf or .dff)
       which would send incompatible raw PDM signals to connected PCM-only DACs, causing loud static
       or driver lockups.
    2. JRiver Integration: On macOS, if a local JRiver Media Center service is running on port 52199,
       FLAC files are played through JRiver's Media Center Web Service (MCWS) API to preserve native
       bit-perfect playback, falling back to macOS Apple Music otherwise.

    Args:
        path: The target file path (Windows, macOS, or Linux format).

    Returns:
        True if the file was successfully matched and opened, False if the target does not exist.
    """
    resolved = resolve_local_path(path)
    if not resolved or not os.path.exists(resolved):
        return False
        
    if sys.platform == "darwin":
        # Block 1-bit DSD files due to DAC limitations
        if resolved.lower().endswith((".dsf", ".dff")):
            print("[Error] Refusing to open 1-bit DSD file (.dsf/.dff) on macOS client: DAC hardware limitation.")
            return True
        # Custom Photo routing to ACDSee Photo Studio on macOS
        if resolved.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")):
            acdsee_app = None
            try:
                for app in os.listdir("/Applications"):
                    if app.lower().startswith("acdsee"):
                        acdsee_app = app.replace(".app", "")
                        break
            except Exception:
                pass
            
            if acdsee_app:
                import subprocess
                try:
                    subprocess.run(["open", "-a", acdsee_app, resolved], check=True)
                    return True
                except Exception:
                    pass

        # Custom FLAC routing
        if resolved.lower().endswith(".flac"):
            import socket
            jriver_active = False
            jriver_host = os.getenv("JRIVER_HOST")
            if not jriver_host:
                wsl_ip = get_wsl_host_ip()
                if wsl_ip:
                    jriver_host = wsl_ip
                elif sys.platform in ("darwin", "linux"):
                    jriver_host = "192.168.1.100"  # Example default local IP
                else:
                    jriver_host = "127.0.0.1"
            try:
                with socket.create_connection((jriver_host, 52198), timeout=0.5):
                    jriver_active = True
            except Exception:
                pass

            if jriver_active:
                import urllib.request
                import urllib.parse
                escaped_path = urllib.parse.quote(path.replace('/', '\\'))
                url = f"http://{jriver_host}:52198/MCWS/v1/Playback/PlayByFilename?Filename={escaped_path}"
                try:
                    with urllib.request.urlopen(url, timeout=2.0) as response:
                        return True
                except Exception as e:
                    print(f"[Warning] Failed JRiver MCWS playback: {e}. Falling back to VLC/OS default...")

            # Fallback to VLC (since Apple Music does not support FLAC), falling back to OS default handler
            import subprocess
            try:
                subprocess.run(["open", "-a", "VLC", resolved], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                # Fallback to system default handler if VLC launcher fails
                subprocess.run(["open", resolved], check=True)
            return True

    if sys.platform == "win32":
        os.startfile(resolved)
    else:
        import subprocess
        cmd = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([cmd, resolved], check=True)
    return True


def compute_rel_path(full_path: str) -> str:
    """Computes a normalized relative path matching the cataloger's indexing rules.

    Args:
        full_path: The absolute path of the file.

    Returns:
        The normalized relative path string in lowercase.
    """
    if not full_path:
        return ""
    path_norm: str = full_path.replace("\\", "/").lower()

    # Check common project/pictures directory split markers
    if "vi ko\u0142odko/" in path_norm:
        return "vi ko\u0142odko/" + path_norm.split("vi ko\u0142odko/", 1)[1]
    elif "pictures/" in path_norm:
        return path_norm.split("pictures/", 1)[1]
    elif "patreon/" in path_norm:
        return path_norm.split("patreon/", 1)[1]
    elif "wan_project/" in path_norm:
        return path_norm.split("wan_project/", 1)[1]

    # Strip mount prefix for Windows drive (e.g. h:/), WSL (/mnt/h/ or /mnt/d/), and macOS (/volumes/hdrive/)
    for prefix in ["h:/", "d:/", "/mnt/h/", "/mnt/d/", "/volumes/hdrive/", "/volumes/d-drive/", "/workspace/"]:
        if path_norm.startswith(prefix):
            return path_norm[len(prefix):]

    # Fallback to the basename
    return os.path.basename(path_norm)

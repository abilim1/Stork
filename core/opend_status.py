"""Detect whether Moomoo OpenD is running."""

from __future__ import annotations

import socket
import subprocess
import sys

PROCESS_NAMES = ("opend.exe", "futuopend.exe", "moomooopend.exe")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 11111


def is_opend_process_running() -> bool:
    """True if OpenD.exe (or a known alias) is in the process list."""
    if sys.platform.startswith("win"):
        return _windows_tasklist()
    return _posix_pgrep()


def is_opend_port_open(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def opend_state(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> tuple[bool, str]:
    """
    Ready when the process is up and port 11111 accepts a connection.
    Returns (ready, short status text).
    """
    proc = is_opend_process_running()
    port_ok = is_opend_port_open(host, port)
    if proc and port_ok:
        return True, "OpenD.exe running  •  port 11111 open"
    if proc and not port_ok:
        return False, "OpenD.exe running  •  login may be incomplete"
    if port_ok and not proc:
        return True, "Port 11111 open  •  process name not seen"
    return False, "OpenD.exe not running"


def _windows_tasklist() -> bool:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for name in PROCESS_NAMES:
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                text=True,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        except (OSError, subprocess.CalledProcessError):
            continue
        if name.lower() in out.lower() and "No tasks" not in out:
            return True
    return False


def _posix_pgrep() -> bool:
    names = ("OpenD", "FutuOpenD", "moomooOpenD", "opend")
    try:
        out = subprocess.check_output(["ps", "-A", "-o", "comm="], text=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    procs = {line.strip() for line in out.splitlines()}
    return any(n in procs for n in names)

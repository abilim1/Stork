"""Run Stork or its migration through a private SSH PostgreSQL tunnel."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import re
import socket
import subprocess
import sys
import time


@contextmanager
def vps_connection(host, opend_port=None):
    from psycopg.conninfo import make_conninfo

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", host):
        raise ValueError("Use a configured SSH host alias")
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "UpdateHostKeys=no", "-o", "ConnectTimeout=10",
           "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    credentials = subprocess.run(ssh + [host, "cat .config/stork/postgres.json"],
                                 stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True, timeout=20, creationflags=flags)
    if credentials.returncode:
        raise RuntimeError("Cannot read Stork database credentials over SSH. Complete the VPS setup first.")
    try:
        config = json.loads(credentials.stdout)
        if set(config) != {"dbname", "user", "password"} or not all(isinstance(v, str) and v for v in config.values()):
            raise ValueError()
    except (ValueError, TypeError):
        raise RuntimeError("The private PostgreSQL credential file is invalid") from None
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    forwards = ["-L", f"127.0.0.1:{port}:127.0.0.1:5432"]
    if opend_port is not None:
        forwards += ["-L", f"127.0.0.1:{opend_port}:127.0.0.1:11111"]
    tunnel = subprocess.Popen(ssh + ["-N", "-o", "ExitOnForwardFailure=yes", *forwards, host],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=flags)
    try:
        deadline = time.monotonic() + 12
        while True:
            if tunnel.poll() is not None:
                raise RuntimeError("The private database tunnel could not start")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    pass
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("The private database tunnel timed out") from None
                time.sleep(0.2)
        yield make_conninfo(host="127.0.0.1", port=port, sslmode="disable", **config)
    finally:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tunnel.kill()
            tunnel.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-host", default="vps")
    parser.add_argument("--vps-opend", action="store_true", help="Connect the live feed to VPS OpenD through SSH")
    parser.add_argument("action", choices=("serve", "migrate"))
    args, remaining = parser.parse_known_args()
    opend_port = None
    if args.vps_opend:
        if args.action != "serve":
            parser.error("--vps-opend is only available for serve")
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            opend_port = reserve.getsockname()[1]
        remaining += ["--opend-host", "127.0.0.1", "--opend-port", str(opend_port), "--connect-live"]
    with vps_connection(args.ssh_host, opend_port=opend_port) as conninfo:
        previous = os.environ.get("STORK_DATABASE_URL")
        previous_argv = sys.argv
        os.environ["STORK_DATABASE_URL"] = conninfo
        sys.argv = [sys.argv[0]] + remaining
        try:
            if args.action == "serve":
                # Refuse to accidentally start a fresh empty database before migration.
                from .postgres import connect_database
                with connect_database(conninfo) as db:
                    if not db.execute("SELECT to_regclass('public.reports') AS name").fetchone()["name"]:
                        raise RuntimeError("Stork tables are missing. Complete the migration before starting the website.")
                from .server import main as serve
                serve()
            else:
                from .migrate_postgres import main as migrate
                migrate()
        finally:
            sys.argv = previous_argv
            if previous is None:
                os.environ.pop("STORK_DATABASE_URL", None)
            else:
                os.environ["STORK_DATABASE_URL"] = previous


if __name__ == "__main__":
    main()

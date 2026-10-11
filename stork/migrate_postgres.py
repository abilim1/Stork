"""Copy a stopped Stork SQLite database into an empty PostgreSQL database."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from .postgres import connect_database, create_schema

TABLES = {
    "bars": ("symbol,time,interval,open,high,low,close,volume,source", "symbol,time,interval"),
    "documents": ("key,value", "key"),
    "reports": ("id,created,status,body,error", "id"),
    "positions": ("id,created,quantity,entry,intent,reason,state,exit,history", "id"),
    "events": ("id,created,kind,title,body", "id"),
    "ticks": ("sequence,time,price,volume", "sequence"),
}


def fingerprint(rows):
    """Hash complete ordered records without loading the tick archive into RAM."""
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        values = list(row.values()) if isinstance(row, dict) else list(row)
        digest.update(json.dumps(values, ensure_ascii=True, allow_nan=False,
                                 separators=(",", ":")).encode("ascii") + b"\n")
        count += 1
    return {"count": count, "sha256": digest.hexdigest()}


def inspect_source(db):
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity check failed")
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    if tables != set(TABLES):
        raise ValueError("Unexpected source tables; migration stopped to avoid omitting data")
    for table, (columns, _) in TABLES.items():
        actual = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
        if actual != columns.split(","):
            raise ValueError(f"Unexpected columns in {table}")


def copy_and_verify(source, target):
    """The caller owns the PostgreSQL transaction and exclusive Stork lock."""
    from psycopg import sql

    existing = target.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
    if existing:
        raise ValueError("Destination is not empty; no existing tables will be overwritten")
    create_schema(target)
    manifest = {}
    for table, (columns, order) in TABLES.items():
        with target.cursor() as cursor:
            with cursor.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(
                sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns.split(",")))
            )) as copy:
                for row in source.execute(f"SELECT {columns} FROM {table}"):
                    copy.write_row(tuple(row))
        expected = fingerprint(source.execute(f"SELECT {columns} FROM {table} ORDER BY {order}"))
        # PostgreSQL locale sorting may differ from SQLite BINARY for tick IDs.
        pg_order = {"bars": 'symbol COLLATE "C",time,interval',
                    "documents": 'key COLLATE "C"', "ticks": 'sequence COLLATE "C"'}.get(table, order)
        with target.cursor(name=f"verify_{table}") as cursor:
            cursor.itersize = 10000
            cursor.execute(f"SELECT {columns} FROM {table} ORDER BY {pg_order}")
            actual = fingerprint(cursor)
        if actual != expected:
            raise ValueError(f"Content verification failed for {table}; transaction will roll back")
        manifest[table] = actual
    for table in ("reports", "positions", "events"):
        maximum = target.execute(f"SELECT MAX(id) AS maximum FROM {table}").fetchone()["maximum"]
        target.execute("SELECT setval(pg_get_serial_sequence(%s, 'id'), %s, %s)",
                       (table, max(1, maximum or 1), maximum is not None and maximum >= 1))
    return manifest


def migrate(source_path, backup_path, conninfo):
    source_path, backup_path = Path(source_path).resolve(), Path(backup_path).resolve()
    if not source_path.is_file() or source_path == backup_path:
        raise ValueError("Choose an existing source database and a separate new backup path")
    # Exclusive creation protects earlier backups, including failed-migration evidence.
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    with backup_path.open("xb"):
        pass
    with connect_database(conninfo) as target:
        # No schema or data is committed unless all tables pass content verification.
        with target.transaction():
            target.execute("SET LOCAL statement_timeout = '20min'")
            if target.execute("SELECT 1 FROM pg_tables WHERE schemaname='public' LIMIT 1").fetchone():
                raise ValueError("Destination is not empty; migration refused")
            lock = sqlite3.connect(source_path.as_uri() + "?mode=rw", uri=True, timeout=1)
            try:
                # Stork must be stopped first. This additionally prevents concurrent
                # writers while taking the backup and verifying the transfer.
                lock.execute("BEGIN IMMEDIATE")
                read = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
                backup = sqlite3.connect(backup_path)
                try:
                    read.backup(backup)
                    inspect_source(backup)
                    manifest = copy_and_verify(backup, target)
                finally:
                    backup.close()
                    read.close()
            finally:
                lock.rollback()
                lock.close()
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--source-stopped", action="store_true", help="Confirm the source Stork server is stopped")
    args = parser.parse_args()
    if not args.source_stopped:
        parser.error("Stop Stork first, then pass --source-stopped")
    conninfo = os.environ.get("STORK_DATABASE_URL")
    if not conninfo:
        parser.error("STORK_DATABASE_URL must identify the empty destination database")
    manifest = migrate(args.source, args.backup, conninfo)
    print(json.dumps({"status": "verified", "backup": str(args.backup), "tables": manifest}, indent=2))


if __name__ == "__main__":
    main()

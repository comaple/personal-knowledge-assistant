"""Offline maintenance: python maintain.py --help. Never overwrites an existing DB."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from runtime import DatabaseLock
from store import Store, now


def backup(database, output):
    database = Path(database).resolve()
    output = Path(output).resolve()
    if not database.is_file():
        raise ValueError("数据库不存在。")
    with DatabaseLock(database):
        if output.exists() and any(output.iterdir()):
            raise ValueError("备份目录必须为空。")
        output.mkdir(parents=True, exist_ok=True)
        target = output / "knowledge.db"
        try:
            src = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
                version = dst.execute("PRAGMA user_version").fetchone()[0]
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("数据库完整性错误。")
            finally:
                dst.close()
                src.close()
            (output / "backup.json").write_text(
                json.dumps(
                    {
                        "schema_version": version,
                        "created_at": now(),
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    },
                    indent=2,
                )
            )
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    return output


def restore(source, database):
    source = Path(source).resolve()
    database = Path(database).resolve()
    with DatabaseLock(database):
        if database.exists():
            raise ValueError("目标数据库已存在，拒绝覆盖。")
        meta = json.loads((source / "backup.json").read_text())
        original = source / "knowledge.db"
        if (
            meta["sha256"] != hashlib.sha256(original.read_bytes()).hexdigest()
            or meta["schema_version"] != 2
        ):
            raise ValueError("备份摘要或版本无效。")
        database.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=database.parent) as temp:
            target = Path(temp) / "restore.db"
            shutil.copyfile(original, target)
            db = sqlite3.connect(target)
            try:
                db.execute("PRAGMA foreign_keys=ON")
                if (
                    db.execute("PRAGMA user_version").fetchone()[0] != 2
                    or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                    or db.execute("PRAGMA foreign_key_check").fetchall()
                ):
                    raise ValueError("备份数据库不完整。")
                db.execute(
                    "UPDATE rag_state SET generation=NULL,built_revision=NULL,profile=NULL,updated_at=NULL"
                )
                db.commit()
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                db.close()
            # Exclusive creation prevents accidental overwrite even by an unrelated writer.
            os.link(target, database)
    return database


def clean_index(database):
    database = Path(database).resolve()
    if not database.is_file():
        raise ValueError("数据库不存在。")
    with DatabaseLock(database):
        Store(database, readonly=True)
        directory = database.parent / (database.stem + "-rag-index")
        if directory.exists():
            shutil.rmtree(directory)
        with sqlite3.connect(database) as db:
            db.execute(
                "UPDATE rag_state SET generation=NULL,built_revision=NULL,profile=NULL,updated_at=NULL"
            )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="operation", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--db", required=True)
    b.add_argument("--output", required=True)
    r = sub.add_parser("restore")
    r.add_argument("--backup", required=True)
    r.add_argument("--db", required=True)
    c = sub.add_parser("clean-index")
    c.add_argument("--db", required=True)
    a = p.parse_args()
    try:
        if a.operation == "backup":
            backup(a.db, a.output)
        elif a.operation == "restore":
            restore(a.backup, a.db)
        else:
            clean_index(a.db)
    except (ValueError, OSError, sqlite3.Error) as exc:
        p.exit(1, str(exc) + "\n")
    print("维护完成。")


if __name__ == "__main__":
    main()

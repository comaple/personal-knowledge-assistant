"""Schema-2 SQLite persistence; short transactions, immutable source snapshots."""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from errors import AppError, positive, text
from runtime import DatabaseLock
from schema import SCHEMA


def now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def utc(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Store:
    def __init__(self, path, readonly=False):
        self.path = Path(path).resolve()
        self.readonly = readonly
        if readonly:
            with self.connect() as db:
                if db.execute("PRAGMA user_version").fetchone()[0] != 2:
                    raise RuntimeError("请先通过应用升级数据库。")
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        probe = sqlite3.connect(self.path)
        try:
            version = probe.execute("PRAGMA user_version").fetchone()[0]
        finally:
            probe.close()
        if version != 2:
            with DatabaseLock(self.path):
                self._initialize()

    def _initialize(self):
        db = sqlite3.connect(self.path)
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 2:
                return
            if version != 0:
                raise RuntimeError("数据库版本较新或不受支持，已保留原数据。")
            tables = {
                r[0]
                for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not tables:
                db.execute("PRAGMA foreign_keys=ON")
                db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA + "\nCOMMIT;")
                return
            expected = {
                "documents",
                "chunks",
                "sessions",
                "messages",
                "harness_runs",
                "chunk_search",
            }
            if not expected <= tables:
                raise RuntimeError("数据库结构未知，无法自动升级。")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            backup = Path(str(self.path) + ".pre-v2-" + uuid.uuid4().hex + ".db")
            with sqlite3.connect(backup) as copy:
                db.backup(copy)
            temp = Path(str(self.path) + ".migration-" + uuid.uuid4().hex)
            try:
                new = sqlite3.connect(temp)
                try:
                    new.executescript(SCHEMA)
                    db.row_factory = sqlite3.Row
                    for r in db.execute("SELECT * FROM documents"):
                        new.execute(
                            "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)",
                            (
                                r["id"],
                                r["name"],
                                "imported",
                                r["content"],
                                r["digest"],
                                1,
                                utc(r["created_at"]),
                                utc(r["created_at"]),
                            ),
                        )
                    from knowledge import tokenize

                    for r in db.execute("SELECT * FROM chunks"):
                        new.execute("INSERT INTO chunks VALUES(?,?,?,?)", tuple(r))
                        new.execute(
                            "INSERT INTO chunk_search(rowid,tokens) VALUES(?,?)",
                            (r["id"], " ".join(tokenize(r["content"]))),
                        )
                    for r in db.execute("SELECT * FROM sessions"):
                        new.execute(
                            "INSERT INTO sessions VALUES(?,?,?)",
                            (r["id"], r["title"], utc(r["created_at"])),
                        )
                    high = 0
                    for r in db.execute("SELECT * FROM messages"):
                        new.execute(
                            "INSERT INTO messages(id,session_id,role,content,sources_json,status,created_at) VALUES(?,?,?,?,?,?,?)",
                            (
                                r["id"],
                                r["session_id"],
                                r["role"],
                                r["content"],
                                r["sources_json"],
                                r["status"],
                                utc(r["created_at"]),
                            ),
                        )
                        for s in json.loads(r["sources_json"]):
                            if type(s.get("chunk_id")) is int:
                                high = max(high, s["chunk_id"])
                    for r in db.execute("SELECT * FROM harness_runs"):
                        new.execute(
                            "INSERT INTO harness_runs(id,session_id,harness_session_id,finish_reason,events_json,created_at) VALUES(?,?,?,?,?,?)",
                            (
                                r["id"],
                                r["session_id"],
                                r["harness_session_id"],
                                r["finish_reason"],
                                r["events_json"],
                                utc(r["created_at"]),
                            ),
                        )
                    live = new.execute(
                        "SELECT COALESCE(MAX(id),0) FROM chunks"
                    ).fetchone()[0]
                    new.execute(
                        "UPDATE sqlite_sequence SET seq=? WHERE name='chunks'",
                        (max(high, live),),
                    )
                    if not new.execute(
                        "SELECT 1 FROM sqlite_sequence WHERE name='chunks'"
                    ).fetchone():
                        new.execute(
                            "INSERT INTO sqlite_sequence(name,seq) VALUES('chunks',?)",
                            (max(high, live),),
                        )
                    if "rag_state" in tables:
                        revision = db.execute(
                            "SELECT revision FROM rag_state WHERE id=1"
                        ).fetchone()
                        if revision:
                            new.execute(
                                "UPDATE rag_state SET revision=?,catalog_revision=?",
                                (revision[0], revision[0]),
                            )
                    new.commit()
                    if (
                        new.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                        or new.execute("PRAGMA foreign_key_check").fetchall()
                    ):
                        raise RuntimeError("升级完整性检查失败。")
                finally:
                    new.close()
                db.close()
                db = None
                for suffix in ("-wal", "-shm"):
                    Path(str(self.path) + suffix).unlink(missing_ok=True)
                os.replace(temp, self.path)
            finally:
                temp.unlink(missing_ok=True)
        finally:
            if db is not None:
                db.close()

    @contextmanager
    def connect(self, write=False):
        if write and self.readonly:
            raise RuntimeError("只读连接禁止写入。")
        db = (
            sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
            if self.readonly
            else sqlite3.connect(self.path, timeout=10)
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        if not self.readonly:
            db.execute("PRAGMA journal_mode=WAL")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            if write:
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _bump(db, content=True):
        db.execute(
            "UPDATE rag_state SET catalog_revision=catalog_revision+1"
            + (",revision=revision+1" if content else "")
            + " WHERE id=1"
        )

    @staticmethod
    def _version(db, doc_id, expected):
        row = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if row is None:
            raise AppError("NOT_FOUND", "资料不存在。", 404)
        if expected is not None and row["version"] != positive(expected):
            raise AppError(
                "VERSION_CONFLICT",
                "资料已修改，请刷新后重试。",
                409,
                {"current_version": row["version"]},
            )
        return row

    @staticmethod
    def _capacity(db, count, replacing=None):
        current = db.execute(
            "SELECT COUNT(*) FROM chunks"
            + (" WHERE document_id!=?" if replacing is not None else ""),
            (replacing,) if replacing is not None else (),
        ).fetchone()[0]
        if current + count > 500:
            raise AppError(
                "CAPACITY_EXCEEDED", "知识库最多 500 个片段，请先删减资料。", 422
            )

    @staticmethod
    def _pieces(db, doc_id, pieces, tokenize):
        for ordinal, piece in enumerate(pieces, 1):
            cid = db.execute(
                "INSERT INTO chunks(document_id,ordinal,content) VALUES(?,?,?)",
                (doc_id, ordinal, piece),
            ).lastrowid
            db.execute(
                "INSERT INTO chunk_search(rowid,tokens) VALUES(?,?)",
                (cid, " ".join(tokenize(piece))),
            )

    def add_document(self, name, content, digest, pieces, tokenize, kind="imported"):
        if kind not in ("imported", "note"):
            raise AppError("INVALID_INPUT", "资料类型无效。")
        with self.connect(write=True) as db:
            old = db.execute(
                "SELECT id FROM documents WHERE digest=?", (digest,)
            ).fetchone()
            if old:
                return old["id"], False
            self._capacity(db, len(pieces))
            stamp = now()
            did = db.execute(
                "INSERT INTO documents(name,kind,content,digest,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (name, kind, content, digest, stamp, stamp),
            ).lastrowid
            self._pieces(db, did, pieces, tokenize)
            self._bump(db)
            return did, True

    def edit_document(self, did, expected, changes):
        from knowledge import (
            digest_text,
            normalize_content,
            normalize_name,
            split_text,
            tokenize,
        )

        new_name = normalize_name(changes["name"]) if "name" in changes else None
        content = (
            normalize_content(changes["content"]) if "content" in changes else None
        )
        with self.connect(write=True) as db:
            old = self._version(db, did, expected)
            name = old["name"] if new_name is None else new_name
            content = old["content"] if content is None else content
            content_changed = content != old["content"]
            changed = content_changed or name != old["name"]
            if changed:
                digest = digest_text(content)
                if db.execute(
                    "SELECT 1 FROM documents WHERE digest=? AND id!=?", (digest, did)
                ).fetchone():
                    raise AppError("DUPLICATE_CONTENT", "其他资料已有相同正文。", 409)
                if content_changed:
                    pieces = split_text(content)
                    self._capacity(db, len(pieces), did)
                    db.execute(
                        "DELETE FROM chunk_search WHERE rowid IN(SELECT id FROM chunks WHERE document_id=?)",
                        (did,),
                    )
                    db.execute("DELETE FROM chunks WHERE document_id=?", (did,))
                    self._pieces(db, did, pieces, tokenize)
                db.execute(
                    "UPDATE documents SET name=?,content=?,digest=?,version=version+1,updated_at=? WHERE id=?",
                    (name, content, digest, now(), did),
                )
                self._bump(db, content_changed)
            count = db.execute(
                "SELECT COUNT(*) FROM chunks WHERE document_id=?", (did,)
            ).fetchone()[0]
            return {
                "document_id": did,
                "changed": changed,
                "chunk_count": count,
                "version": old["version"] + int(changed),
            }

    def replace_tags(self, did, expected, values):
        if not isinstance(values, list) or len(values) > 20:
            raise AppError("INVALID_INPUT", "最多设置 20 个标签。")
        values = sorted(set(text(v, 40, "标签") for v in values))
        with self.connect(write=True) as db:
            old = self._version(db, did, expected)
            previous = [
                r[0]
                for r in db.execute(
                    "SELECT t.name FROM tags t JOIN document_tags dt ON t.id=dt.tag_id WHERE dt.document_id=? ORDER BY t.name",
                    (did,),
                )
            ]
            changed = previous != values
            if changed:
                db.execute("DELETE FROM document_tags WHERE document_id=?", (did,))
                for name in values:
                    db.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
                    db.execute(
                        "INSERT INTO document_tags SELECT ?,id FROM tags WHERE name=?",
                        (did, name),
                    )
                db.execute(
                    "UPDATE documents SET version=version+1,updated_at=? WHERE id=?",
                    (now(), did),
                )
                self._bump(db, False)
            return {
                "document_id": did,
                "tags": values,
                "version": old["version"] + int(changed),
                "changed": changed,
            }

    def delete_document(self, did, expected=None):
        with self.connect(write=True) as db:
            self._version(db, did, expected)
            db.execute(
                "DELETE FROM chunk_search WHERE rowid IN(SELECT id FROM chunks WHERE document_id=?)",
                (did,),
            )
            db.execute("DELETE FROM documents WHERE id=?", (did,))
            self._bump(db)

    @staticmethod
    def _documents(db, tag=None, content=False):
        query = "SELECT d.*,(SELECT COUNT(*) FROM chunks WHERE document_id=d.id) AS chunk_count FROM documents d"
        args = ()
        if tag is not None:
            query += " WHERE EXISTS(SELECT 1 FROM document_tags dt JOIN tags t ON t.id=dt.tag_id WHERE dt.document_id=d.id AND t.name=?)"
            args = (tag,)
        result = []
        for row in db.execute(query + " ORDER BY d.updated_at DESC,d.id DESC", args):
            item = dict(row)
            if not content:
                item.pop("content")
            item["tags"] = [
                r[0]
                for r in db.execute(
                    "SELECT t.name FROM tags t JOIN document_tags dt ON dt.tag_id=t.id WHERE dt.document_id=? ORDER BY t.name",
                    (item["id"],),
                )
            ]
            result.append(item)
        return result

    def documents(self):
        with self.connect() as db:
            return self._documents(db)

    def document(self, did):
        with self.connect() as db:
            self._version(db, did, None)
            return next(d for d in self._documents(db, content=True) if d["id"] == did)

    @staticmethod
    def paginate(items, page=1, page_size=20):
        start = (page - 1) * page_size
        return {
            "items": items[start : start + page_size],
            "total": len(items),
            "page": page,
            "page_size": page_size,
        }

    def document_page(self, page=1, page_size=20, tag=None):
        with self.connect() as db:
            return self.paginate(self._documents(db, tag), page, page_size)

    def tags(self):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT t.name,COUNT(*) AS document_count FROM tags t JOIN document_tags dt ON dt.tag_id=t.id GROUP BY t.id ORDER BY t.name"
                )
            ]

    def export_snapshot(self):
        with self.connect() as db:
            state = dict(db.execute("SELECT * FROM rag_state WHERE id=1").fetchone())
            return state, self._documents(db, content=True)

    def search(self, expression, limit=4):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    """SELECT c.id AS chunk_id,c.document_id,c.ordinal,c.content,d.name,d.version AS document_version,s.revision,bm25(chunk_search) AS rank
        FROM chunk_search JOIN chunks c ON c.id=chunk_search.rowid JOIN documents d ON d.id=c.document_id CROSS JOIN rag_state s WHERE chunk_search MATCH ? ORDER BY rank,c.id LIMIT ?""",
                    (expression, limit),
                )
            ]

    def chunks_by_ids(self, ids):
        if not ids:
            return []
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT c.id AS chunk_id,c.document_id,c.ordinal,c.content,d.name,d.version AS document_version,s.revision FROM chunks c JOIN documents d ON d.id=c.document_id CROSS JOIN rag_state s WHERE c.id IN("
                    + ",".join("?" for _ in ids)
                    + ")",
                    ids,
                )
            ]

    def rag_state(self):
        with self.connect() as db:
            return dict(db.execute("SELECT * FROM rag_state WHERE id=1").fetchone())

    def assert_revision(self, revision):
        if self.rag_state()["revision"] != revision:
            raise AppError("CORPUS_CHANGED", "资料已变化，请重新提问。", 409)

    def corpus_snapshot(self):
        with self.connect() as db:
            revision = db.execute(
                "SELECT revision FROM rag_state WHERE id=1"
            ).fetchone()[0]
            chunks = [
                dict(r)
                for r in db.execute(
                    "SELECT c.id AS chunk_id,c.document_id,c.ordinal,c.content,d.name,d.version AS document_version,s.revision FROM chunks c JOIN documents d ON d.id=c.document_id CROSS JOIN rag_state s ORDER BY c.id"
                )
            ]
            return revision, chunks

    def publish_index(self, generation, revision, profile):
        with self.connect(write=True) as db:
            return (
                db.execute(
                    "UPDATE rag_state SET generation=?,built_revision=?,profile=?,updated_at=? WHERE id=1 AND revision=?",
                    (generation, revision, profile, now(), revision),
                ).rowcount
                == 1
            )

    def sessions(self):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM sessions ORDER BY created_at DESC,id DESC"
                )
            ]

    def new_session(self):
        sid = str(uuid.uuid4())
        with self.connect(write=True) as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (sid, "新会话", now()))
        return sid

    def has_session(self, sid):
        with self.connect() as db:
            return (
                db.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone()
                is not None
            )

    def messages(self, sid):
        with self.connect() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM messages WHERE session_id=? ORDER BY id", (sid,)
                )
            ]
        for row in rows:
            row["sources"] = json.loads(row.pop("sources_json"))
            row["retrieval"] = json.loads(row.pop("retrieval_json") or "null")
            for source in row["sources"]:
                if (
                    not {
                        "document_version",
                        "revision",
                        "generation",
                        "channels",
                        "channel_ranks",
                        "rank_position",
                        "graph_paths",
                    }
                    <= source.keys()
                ):
                    source["legacy"] = True
        return rows

    def runs(self, sid):
        with self.connect() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM harness_runs WHERE session_id=? ORDER BY id", (sid,)
                )
            ]
        for row in rows:
            row["events"] = json.loads(row.pop("events_json"))
        return rows

    def save_result(self, sid, question, result, revision, run=None):
        with self.connect(write=True) as db:
            if (
                db.execute("SELECT revision FROM rag_state WHERE id=1").fetchone()[0]
                != revision
            ):
                result.update(
                    answer="资料在问答期间已变化，请重新提问。",
                    sources=[],
                    status="model_error"
                    if result.get("mode") == "generate"
                    else "retrieval_error",
                    error={
                        "code": "CORPUS_CHANGED",
                        "message": "资料已变化，请重新提问。",
                        "details": {},
                    },
                )
            result.pop("mode", None)
            stamp = now()
            tid = result["turn_id"]
            db.execute(
                "INSERT INTO messages(session_id,turn_id,role,content,created_at) VALUES(?,?,?,?,?)",
                (sid, tid, "user", question, stamp),
            )
            meta = result.get("retrieval")
            if result.get("error"):
                # Persist the error in retrieval message where possible; the full user-facing error text also survives.
                if meta:
                    meta = {
                        **meta,
                        "message": result["error"]["code"]
                        + "："
                        + result["error"]["message"],
                    }
                    result["retrieval"] = meta
            db.execute(
                "INSERT INTO messages(session_id,turn_id,role,content,sources_json,status,retrieval_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    sid,
                    tid,
                    "assistant",
                    result["answer"],
                    json.dumps(result["sources"], ensure_ascii=False),
                    result["status"],
                    json.dumps(meta, ensure_ascii=False),
                    stamp,
                ),
            )
            if run:
                db.execute(
                    "INSERT INTO harness_runs(session_id,turn_id,harness_session_id,finish_reason,events_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        sid,
                        tid,
                        run["harness_session_id"],
                        run.get("finish_reason"),
                        json.dumps(run.get("events", []), ensure_ascii=False),
                        stamp,
                    ),
                )
            db.execute(
                "UPDATE sessions SET title=? WHERE id=? AND title='新会话'",
                (question[:28], sid),
            )
        return result

    def save_turn(self, sid, question, answer, sources, status, run=None):
        return self.save_result(
            sid,
            question,
            {
                "turn_id": str(uuid.uuid4()),
                "answer": answer,
                "sources": sources,
                "status": status,
                "retrieval": None,
                "error": None,
            },
            self.rag_state()["revision"],
            run,
        )

    def delete_session(self, sid):
        with self.connect(write=True) as db:
            if not db.execute("DELETE FROM sessions WHERE id=?", (sid,)).rowcount:
                raise AppError("NOT_FOUND", "会话不存在。", 404)

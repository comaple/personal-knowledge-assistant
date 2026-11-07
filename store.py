"""SQLite persistence: open a short-lived connection per operation."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, content TEXT NOT NULL,
 digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chunks (
 id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, content TEXT NOT NULL, UNIQUE(document_id, ordinal)
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_search USING fts5(tokens, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
 sources_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'ok',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            try:
                db.executescript(SCHEMA)
            except sqlite3.OperationalError as exc:
                raise RuntimeError('当前 Python 的 SQLite 需要支持 FTS5，请使用 python.org 的新版 Python。') from exc

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def documents(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('''
              SELECT d.id,d.name,d.created_at,COUNT(c.id) AS chunk_count
              FROM documents d LEFT JOIN chunks c ON c.document_id=d.id
              GROUP BY d.id ORDER BY d.id DESC''')]

    def add_document(self, name, content, digest, pieces, tokenize):
        with self.connect() as db:
            old = db.execute('SELECT id FROM documents WHERE digest=?', (digest,)).fetchone()
            if old:
                return old['id'], False
            doc_id = db.execute('INSERT INTO documents(name,content,digest) VALUES(?,?,?)',
                                (name, content, digest)).lastrowid
            for i, piece in enumerate(pieces, 1):
                chunk_id = db.execute('INSERT INTO chunks(document_id,ordinal,content) VALUES(?,?,?)',
                                      (doc_id, i, piece)).lastrowid
                db.execute('INSERT INTO chunk_search(rowid,tokens) VALUES(?,?)',
                           (chunk_id, ' '.join(tokenize(piece))))
            return doc_id, True

    def delete_document(self, doc_id):
        with self.connect() as db:
            db.execute('DELETE FROM chunk_search WHERE rowid IN (SELECT id FROM chunks WHERE document_id=?)', (doc_id,))
            db.execute('DELETE FROM documents WHERE id=?', (doc_id,))
        # Historical messages keep evidence snapshots for audit, as explained in README.

    def search(self, expression, limit=4):
        with self.connect() as db:
            return [dict(row) for row in db.execute('''
              SELECT c.id AS chunk_id,c.document_id,c.ordinal,c.content,d.name,
                     bm25(chunk_search) AS rank
              FROM chunk_search JOIN chunks c ON c.id=chunk_search.rowid
              JOIN documents d ON d.id=c.document_id
              WHERE chunk_search MATCH ? ORDER BY rank,c.id LIMIT ?''', (expression, limit))]

    def sessions(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM sessions ORDER BY rowid DESC')]

    def new_session(self):
        sid = str(uuid.uuid4())
        with self.connect() as db:
            db.execute('INSERT INTO sessions(id,title) VALUES(?,?)', (sid, '新会话'))
        return sid

    def has_session(self, sid):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM sessions WHERE id=?', (sid,)).fetchone() is not None

    def messages(self, sid):
        with self.connect() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM messages WHERE session_id=? ORDER BY id', (sid,))]
        for row in rows:
            row['sources'] = json.loads(row.pop('sources_json'))
        return rows

    def save_turn(self, sid, question, answer, sources, status):
        with self.connect() as db:
            db.execute('INSERT INTO messages(session_id,role,content) VALUES(?,?,?)', (sid, 'user', question))
            db.execute('INSERT INTO messages(session_id,role,content,sources_json,status) VALUES(?,?,?,?,?)',
                       (sid, 'assistant', answer, json.dumps(sources, ensure_ascii=False), status))
            db.execute("UPDATE sessions SET title=? WHERE id=? AND title='新会话'", (question[:28], sid))

    def delete_session(self, sid):
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE id=?', (sid,))

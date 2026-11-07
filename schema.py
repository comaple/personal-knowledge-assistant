"""Schema 2, synchronized with doc/SPEC.md."""

SCHEMA = """PRAGMA foreign_keys=ON;
CREATE TABLE documents (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('imported','note')),
 content TEXT NOT NULL,
 digest TEXT NOT NULL UNIQUE,
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE chunks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL CHECK(ordinal>0),
 content TEXT NOT NULL,
 UNIQUE(document_id,ordinal)
);
CREATE VIRTUAL TABLE chunk_search USING fts5(tokens,tokenize='unicode61');
CREATE TABLE tags (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL UNIQUE COLLATE BINARY
);
CREATE TABLE document_tags (
 document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
 PRIMARY KEY(document_id,tag_id)
);
CREATE INDEX document_tags_by_tag ON document_tags(tag_id,document_id);
CREATE TABLE sessions (
 id TEXT PRIMARY KEY,
 title TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 turn_id TEXT,
 role TEXT NOT NULL CHECK(role IN ('user','assistant')),
 content TEXT NOT NULL,
 sources_json TEXT NOT NULL DEFAULT '[]',
 status TEXT NOT NULL DEFAULT 'ok',
 retrieval_json TEXT,
 created_at TEXT NOT NULL,
 UNIQUE(turn_id,role)
);
CREATE TABLE harness_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 turn_id TEXT UNIQUE,
 harness_session_id TEXT NOT NULL,
 finish_reason TEXT,
 events_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE rag_state (
 id INTEGER PRIMARY KEY CHECK(id=1),
 revision INTEGER NOT NULL DEFAULT 0,
 catalog_revision INTEGER NOT NULL DEFAULT 0,
 generation TEXT,
 built_revision INTEGER,
 profile TEXT,
 updated_at TEXT
);
INSERT INTO rag_state(id) VALUES(1);
PRAGMA user_version=2;"""

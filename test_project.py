"""SDD acceptance regressions: storage, migration, API contracts and failure boundaries."""

import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib import error, request

from app import make_server
from contract import API
from errors import AppError
from exports import export_zip
from knowledge import import_text, retrieve, split_text
from maintain import backup, clean_index, restore
from model_client import ModelError, validate_result
from retrieval import search_knowledge
from runtime import DatabaseLock, kill_tree
from store import Store

try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    registry = Registry().with_resource(
        "urn:pka", Resource.from_contents(API, default_specification=DRAFT202012)
    )
except ImportError:
    raise RuntimeError("请安装 requirements-dev.lock 后运行验收测试。") from None


def schema(name, value):
    if Draft202012Validator:
        Draft202012Validator(
            {"$ref": "urn:pka#/components/schemas/" + name}, registry=registry
        ).validate(value)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "kb.db"
        self.store = Store(self.path)

    def test_import_deduplicate_restart_and_retrieve(self):
        a = import_text(self.store, "课程.MD", "\r\n摄影课提前72小时申请。\r\n")
        b = import_text(self.store, "同内容.txt", "摄影课提前72小时申请。")
        self.assertEqual(a["document_id"], b["document_id"])
        self.assertFalse(b["created"])
        self.assertEqual(self.store.rag_state()["revision"], 1)
        self.assertEqual(retrieve(Store(self.path), "摄影课")[0]["source_id"], "S1")
        self.assertEqual(
            Store(self.path).document(a["document_id"])["content"],
            "摄影课提前72小时申请。",
        )

    def test_chunk_coverage_and_overlap(self):
        content = "".join(chr(0x3400 + i) for i in range(2400))
        pieces = split_text(content)
        self.assertEqual(pieces[0][-120:], pieces[1][:120])
        self.assertEqual(pieces[0] + "".join(p[120:] for p in pieces[1:]), content)

    def test_new_schema_failure_is_atomic_and_retryable(self):
        from schema import SCHEMA

        path = Path(self.temp.name) / "failed.db"
        with patch(
            "store.SCHEMA", SCHEMA.replace("CREATE TABLE chunks", "BROKEN TABLE chunks")
        ):
            with self.assertRaises(sqlite3.Error):
                Store(path)
        recovered = Store(path)
        self.assertEqual(recovered.rag_state()["revision"], 0)

    def test_atomic_import_failure(self):
        original = Store._pieces

        def fail(*args):
            original(*args)
            raise sqlite3.OperationalError("injected write failure")

        with patch.object(Store, "_pieces", side_effect=fail):
            with self.assertRaises(sqlite3.Error):
                import_text(self.store, "资料.md", "原子写入")
        self.assertEqual(self.store.documents(), [])
        with self.store.connect() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM chunk_search").fetchone()[0], 0
            )
        self.assertEqual(self.store.rag_state()["revision"], 0)

    def test_edit_tags_versions_and_permanent_sources(self):
        a = import_text(self.store, "笔记", "旧内容摄影课", kind="note")
        did = a["document_id"]
        old = retrieve(self.store, "摄影课")[0]
        self.store.save_turn(
            self.store.new_session(), "问题", "旧回答", [old], "retrieval_only"
        )
        self.store.replace_tags(did, 1, [" 项目 ", "项目", "阅读"])
        state = self.store.rag_state()
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["catalog_revision"], 2)
        with self.assertRaises(AppError) as caught:
            self.store.edit_document(did, 1, {"content": "过期编辑"})
        self.assertEqual(caught.exception.detail["details"]["current_version"], 2)
        self.store.edit_document(did, 2, {"name": "新标题"})
        self.assertEqual(self.store.rag_state()["revision"], 1)
        self.store.edit_document(did, 3, {"content": "新内容开课前48小时"})
        self.assertEqual(retrieve(self.store, "摄影课"), [])
        self.assertGreater(
            retrieve(self.store, "48小时")[0]["chunk_id"], old["chunk_id"]
        )
        history = self.store.messages(self.store.sessions()[0]["id"])
        self.assertEqual(history[-1]["sources"][0]["content"], "旧内容摄影课")
        self.assertEqual(self.store.document_page(tag="项目")["total"], 1)
        self.store.delete_document(did, 4)
        self.assertEqual(self.store.tags(), [])
        next_doc = import_text(self.store, "再次.md", "新资料")
        self.assertGreater(next_doc["document_id"], did)

    def test_duplicate_edit_and_invalid_tag_set_roll_back(self):
        a = import_text(self.store, "A.md", "第一份内容")
        import_text(self.store, "B.md", "第二份内容")
        with self.assertRaises(AppError):
            self.store.edit_document(a["document_id"], 1, {"content": "第二份内容"})
        with self.assertRaises(AppError):
            self.store.replace_tags(a["document_id"], 1, ["合法", "x" * 41])
        self.assertEqual(self.store.document(a["document_id"])["version"], 1)
        self.assertEqual(self.store.tags(), [])
        self.assertFalse(
            self.store.edit_document(a["document_id"], 1, {"content": "第一份内容"})[
                "changed"
            ]
        )

    def test_byte_and_capacity_boundaries(self):
        import_text(self.store, "边界.md", "😀" * 262144)
        with self.assertRaises(AppError) as exc:
            import_text(self.store, "超限.md", "😀" * 262144 + "x")
        self.assertEqual(exc.exception.code, "PAYLOAD_TOO_LARGE")
        self.store.delete_document(1, 1)
        import_text(self.store, "容量.md", "x" * (800 + 499 * 680))
        with self.assertRaises(AppError) as exc:
            import_text(self.store, "新增.md", "多一个片段")
        self.assertEqual(exc.exception.code, "CAPACITY_EXCEEDED")
        self.assertEqual(len(self.store.corpus_snapshot()[1]), 500)

    def test_invalid_text_and_tag_boundaries(self):
        for name, content in [
            ("bad.pdf", "正文"),
            ("ok.md", ""),
            ("ok.md", "x\x00y"),
            ("ok.md", None),
            ("ok.md", "\ud800"),
            (None, "正文"),
        ]:
            with self.subTest(name=name, content=repr(content)):
                with self.assertRaises(AppError):
                    import_text(self.store, name, content)
        row = import_text(self.store, "标签.md", "标签正文")
        labels = ["标签" + str(i) for i in range(20)]
        self.store.replace_tags(row["document_id"], 1, labels)
        with self.assertRaises(AppError):
            self.store.replace_tags(row["document_id"], 2, labels + ["超额"])
        self.assertEqual(len(self.store.document(row["document_id"])["tags"]), 20)

    def test_export_and_backup_restore(self):
        a = import_text(self.store, "../../危险名字.md", "知识正文")
        self.store.replace_tags(a["document_id"], 1, ["项目"])
        sid = self.store.new_session()
        self.store.save_turn(sid, "问题", "回答", [], "no_evidence")
        self.store.publish_index("a" * 32, 1, "old")
        with export_zip(self.store) as stream, zipfile.ZipFile(stream) as z:
            meta = json.loads(z.read("metadata.json"))
            schema("KnowledgeExport", meta)
            self.assertEqual(z.namelist(), ["documents/1.md", "metadata.json"])
            self.assertEqual(meta["documents"][0]["tags"], ["项目"])
            self.assertEqual(
                hashlib.sha256(z.read("documents/1.md")).hexdigest(),
                meta["documents"][0]["digest"],
            )
        target = Path(self.temp.name) / "restored.db"
        output = Path(self.temp.name) / "backup"
        backup(self.path, output)
        restore(output, target)
        restored = Store(target)
        self.assertEqual(restored.document(1)["content"], "知识正文")
        self.assertEqual(restored.messages(sid)[-1]["content"], "回答")
        self.assertIsNone(restored.rag_state()["generation"])
        with self.assertRaises(ValueError):
            restore(output, target)
        with DatabaseLock(self.path):
            with self.assertRaises(AppError):
                backup(self.path, Path(self.temp.name) / "busy")
        indexdir = self.path.parent / (self.path.stem + "-rag-index")
        indexdir.mkdir()
        (indexdir / "old").write_text("derived")
        clean_index(self.path)
        self.assertFalse(indexdir.exists())
        self.assertIsNone(self.store.rag_state()["generation"])
        self.assertEqual(self.store.document(1)["content"], "知识正文")
        (output / "knowledge.db").write_bytes(b"bad")
        with self.assertRaises(ValueError):
            restore(output, Path(self.temp.name) / "bad.db")

    def test_readonly_bridge_and_database_lock(self):
        with Store(self.path, readonly=True).connect() as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM documents")
        with DatabaseLock(self.path):
            with self.assertRaises(AppError):
                DatabaseLock(self.path).acquire()
        with DatabaseLock(self.path):
            pass

    def test_atomic_turn_rollback_and_revision_guard(self):
        sid = self.store.new_session()
        revision = self.store.rag_state()["revision"]
        result = {
            "turn_id": "turn-1",
            "answer": "答案",
            "sources": [],
            "status": "no_evidence",
            "retrieval": None,
            "error": None,
        }
        with self.assertRaises(KeyError):
            self.store.save_result(
                sid, "问题", dict(result), revision, run={"bad": "run"}
            )
        self.assertEqual(self.store.messages(sid), [])
        import_text(self.store, "新.md", "新内容")
        saved = self.store.save_result(sid, "问题", dict(result), revision)
        self.assertEqual(saved["error"]["code"], "CORPUS_CHANGED")
        self.assertEqual(saved["sources"], [])


LEGACY = """CREATE TABLE documents(id INTEGER PRIMARY KEY,name TEXT,content TEXT,digest TEXT,created_at TEXT);
CREATE TABLE chunks(id INTEGER PRIMARY KEY,document_id INTEGER,ordinal INTEGER,content TEXT);
CREATE VIRTUAL TABLE chunk_search USING fts5(tokens);
CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT,created_at TEXT);
CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,sources_json TEXT,status TEXT,created_at TEXT);
CREATE TABLE harness_runs(id INTEGER PRIMARY KEY,session_id TEXT,harness_session_id TEXT,finish_reason TEXT,events_json TEXT,created_at TEXT);"""


class MigrationTest(unittest.TestCase):
    def test_legacy_copy_preserves_snapshots_and_high_water_mark(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "old.db"
            db = sqlite3.connect(p)
            db.executescript(LEGACY)
            db.execute(
                "INSERT INTO documents VALUES(1,?,?,?,?)",
                ("旧.md", "旧正文", "digest", "2026-01-01 00:00:00"),
            )
            db.execute("INSERT INTO chunks VALUES(1,1,1,?)", ("旧正文",))
            db.execute(
                "INSERT INTO sessions VALUES('s','旧会话','2026-01-01 00:00:00')"
            )
            source = {
                "source_id": "S80",
                "chunk_id": 80,
                "name": "历史.md",
                "content": "已删除的历史原文",
            }
            db.execute(
                "INSERT INTO messages VALUES(1,?,?,?,?,?,?)",
                (
                    "s",
                    "assistant",
                    "旧回答",
                    json.dumps([source]),
                    "generated",
                    "2026-01-01 00:00:00",
                ),
            )
            db.commit()
            db.close()
            store = Store(p)
            self.assertEqual(store.document(1)["kind"], "imported")
            self.assertTrue(store.messages("s")[0]["sources"][0]["legacy"])
            import_text(store, "新.md", "新正文")
            self.assertGreater(store.chunks_by_ids([81])[0]["chunk_id"], 80)
            self.assertTrue(list(Path(d).glob("*.pre-v2-*.db")))

    def test_bad_legacy_migration_keeps_original(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "old.db"
            db = sqlite3.connect(p)
            db.executescript(LEGACY)
            db.execute("INSERT INTO chunks VALUES(1,999,1,'失去原文')")
            db.commit()
            db.close()
            before = p.read_bytes()
            with self.assertRaises(sqlite3.Error):
                Store(p)
            self.assertEqual(p.read_bytes(), before)


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "db")
        self.server = make_server(self.store, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.opener = request.build_opener(request.ProxyHandler({}))

    def call(self, path, data=None, method=None, headers=None):
        method = method or ("POST" if data is not None else "GET")
        body = json.dumps(data).encode() if data is not None else None
        req = request.Request(
            self.base + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            r = self.opener.open(req, timeout=20)
        except error.HTTPError as exc:
            r = exc
        with r:
            code = r.status
            value = json.load(r)
        # Validate actual server responses against the formal contract, including error responses.
        import re

        for template, methods in API["paths"].items():
            if (
                re.fullmatch(
                    re.sub(r"\{[^}]+\}", r"[^/]+", template), path.split("?")[0]
                )
                and method.lower() in methods
            ):
                response = methods[method.lower()]["responses"].get(str(code))
                self.assertIsNotNone(response, (method, path, code))
                if Draft202012Validator:

                    def absolute(node):
                        if isinstance(node, dict):
                            return {
                                key: (
                                    "urn:pka" + v
                                    if key == "$ref" and v.startswith("#")
                                    else absolute(v)
                                )
                                for key, v in node.items()
                            }
                        if isinstance(node, list):
                            return [absolute(v) for v in node]
                        return node

                    Draft202012Validator(
                        absolute(response["content"]["application/json"]["schema"]),
                        registry=registry,
                    ).validate(value)
                break
        return code, value

    def test_document_note_tags_pagination_and_conflict_contract(self):
        code, a = self.call(
            "/api/notes", {"name": "项目笔记", "content": "需要提前72小时"}
        )
        self.assertEqual(code, 201)
        did = a["document_id"]
        _, r = self.call(
            f"/api/documents/{did}/tags",
            {"expected_version": 1, "tags": ["项目", "项目"]},
            "PUT",
        )
        self.assertEqual(r["version"], 2)
        code, r = self.call(
            f"/api/documents/{did}",
            {"expected_version": 1, "name": "冲突标题"},
            "PATCH",
        )
        self.assertEqual(code, 409)
        self.assertEqual(r["error"]["details"]["current_version"], 2)
        _, r = self.call("/api/documents?tag=" + request.quote("项目") + "&page_size=1")
        self.assertEqual(r["total"], 1)
        _, r = self.call(
            f"/api/documents/{did}",
            {"expected_version": 2, "content": "改为48小时"},
            "PATCH",
        )
        self.assertTrue(r["changed"])
        self.assertEqual(
            self.call(f"/api/documents/{did}", {"expected_version": 3}, "DELETE")[0],
            200,
        )
        self.assertEqual(
            self.call(f"/api/documents/{did}", {"expected_version": 3}, "DELETE")[0],
            404,
        )

    def test_local_mode_and_history_contract(self):
        self.call("/api/documents", {"name": "课程.md", "content": "退款提前72小时"})
        sid = self.call("/api/sessions", {})[1]["session_id"]
        with (
            patch(
                "retrieval.post_json", side_effect=AssertionError("no model allowed")
            ),
            patch(
                "retrieval.subprocess.Popen",
                side_effect=AssertionError("no subprocess allowed"),
            ),
        ):
            _, r = self.call("/api/ask", {"session_id": sid, "question": "退款"})
        self.assertEqual(r["status"], "retrieval_only")
        self.assertEqual(r["retrieval"]["mode"], "lexical")
        self.assertEqual(self.call("/api/messages?session_id=" + sid)[1]["total"], 2)
        self.assertEqual(self.call("/api/runs?session_id=" + sid)[1]["total"], 0)
        self.assertEqual(
            self.call(
                "/api/ask",
                {"session_id": sid, "question": "量子隧穿", "mode": "lexical"},
            )[1]["status"],
            "no_evidence",
        )

    def test_input_origin_host_and_missing_provider(self):
        self.assertEqual(
            self.call("/api/notes", {"name": "x", "content": "x", "sql": "bad"})[0], 400
        )
        self.assertEqual(
            self.call(
                "/api/notes",
                {"name": "x", "content": "x"},
                headers={"Origin": "https://example.com"},
            )[0],
            403,
        )
        self.assertEqual(
            self.call("/api/config", headers={"Host": "attacker.example"})[0], 403
        )
        self.assertEqual(self.call("/api/chunks?ids=1%20OR%201")[0], 400)
        self.assertEqual(self.call("/api/documents?page_size=101")[0], 400)
        self.assertEqual(self.call("/.env")[0], 404)
        sid = self.call("/api/sessions", {})[1]["session_id"]
        with patch.dict(os.environ, {"LLM_API_KEY": "", "DEEPSEEK_API_KEY": ""}):
            _, r = self.call(
                "/api/ask", {"session_id": sid, "question": "问题", "mode": "generate"}
            )
        self.assertEqual(r["status"], "model_error")
        self.assertEqual(r["error"]["code"], "MODEL_UNAVAILABLE")

    def test_zero_hits_provider_error_is_not_no_evidence(self):
        sid = self.call("/api/sessions", {})[1]["session_id"]
        bundle = search_knowledge(self.store, "问题", lexical_only=True)
        bundle["retrieval"]["routes"][1]["status"] = "error"
        with patch("app.search_knowledge", return_value=bundle):
            _, r = self.call(
                "/api/ask", {"session_id": sid, "question": "问题", "mode": "retrieve"}
            )
        self.assertEqual(r["status"], "retrieval_error")
        self.assertEqual(r["error"]["code"], "RETRIEVAL_FAILED")

    def test_generation_concurrent_edit_preserves_trace_and_rejects_old_sources(self):
        a = import_text(self.store, "资料.md", "原文72小时")
        sid = self.call("/api/sessions", {})[1]["session_id"]
        other = self.call("/api/sessions", {})[1]["session_id"]
        entered = threading.Event()
        release = threading.Event()
        source = search_knowledge(self.store, "72小时", lexical_only=True)["hits"]

        def generate(*args):
            entered.set()
            release.wait(10)
            return {
                "answer": "原文[S1]",
                "sources": source,
                "status": "generated",
                "retrieval": None,
                "harness_session_id": "test-run",
                "finish_reason": "completed",
                "events": [],
            }

        result = []
        with patch("app.generate", side_effect=generate):
            worker = threading.Thread(
                target=lambda: result.append(
                    self.call(
                        "/api/ask",
                        {"session_id": sid, "question": "多久", "mode": "generate"},
                    )
                )
            )
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(
                    self.call("/api/sessions/" + sid, {}, "DELETE")[0], 409
                )
                self.assertEqual(
                    self.call("/api/sessions/" + other, {}, "DELETE")[0], 200
                )
                self.call(
                    "/api/documents/" + str(a["document_id"]),
                    {"expected_version": 1, "content": "新内容48小时"},
                    "PATCH",
                )
            finally:
                release.set()
                worker.join(10)
        r = result[0][1]
        self.assertEqual(r["error"]["code"], "CORPUS_CHANGED")
        self.assertEqual(r["sources"], [])
        self.assertEqual(self.store.runs(sid)[0]["harness_session_id"], "test-run")

    def test_evaluation_refuses_missing_key_before_import(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "", "DEEPSEEK_API_KEY": ""}):
            result = subprocess.run(
                [sys.executable, "evaluate.py", "--url", self.base, "--mode", "all"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("BLOCKED", result.stderr)
        self.assertEqual(self.store.documents(), [])

    def test_index_graph_export_and_instance_lock_contract(self):
        self.call("/api/notes", {"name": "知识", "content": "正文"})
        self.call("/api/index")
        self.call("/api/graph")
        self.call("/api/config")
        self.call("/api/tags")
        with request.urlopen(self.base + "/api/export") as r:
            self.assertEqual(r.headers["Content-Type"], "application/zip")
            z = zipfile.ZipFile(io.BytesIO(r.read()))
            schema("KnowledgeExport", json.loads(z.read("metadata.json")))
        with self.assertRaises(AppError):
            make_server(Store(self.store.path), 0)


class HarnessResultTest(unittest.TestCase):
    def test_no_tool_and_invalid_citation_are_rejected(self):
        with self.assertRaises(ModelError):
            validate_result(
                {
                    "finish_reason": "completed",
                    "answer": "伪回答",
                    "sources": [],
                    "events": [],
                }
            )
        events = [
            {"type": "tool/call", "data": {"name": "kb_search", "callId": "c"}},
            {
                "type": "tool/result",
                "data": {"message": {"toolCallId": "c", "content": []}},
            },
        ]
        with self.assertRaises(ModelError) as caught:
            validate_result(
                {
                    "finish_reason": "completed",
                    "answer": "伪引用[S9]",
                    "sources": [{"source_id": "S1"}],
                    "events": events,
                }
            )
        self.assertEqual(caught.exception.code, "INVALID_CITATION")

    @unittest.skipIf(
        os.name == "nt", "POSIX process-group regression; Windows uses taskkill /T"
    )
    def test_timeout_cleanup_terminates_descendant_session(self):
        program = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); print(p.pid,flush=True); time.sleep(60)"
        proc = subprocess.Popen(
            [sys.executable, "-c", program],
            stdout=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        child = int(proc.stdout.readline())
        kill_tree(proc)
        proc.communicate(timeout=5)
        stat = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(child)], text=True, capture_output=True
        ).stdout.strip()
        self.assertTrue(not stat or stat.startswith("Z"), stat)


if __name__ == "__main__":
    unittest.main(verbosity=2)

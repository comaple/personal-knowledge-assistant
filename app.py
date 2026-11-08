"""Personal Knowledge Assistant: local, single-user HTTP application."""

import argparse
import json
import os
import re
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from contract import validate
from errors import AppError, text
from exports import export_zip
from index_jobs import IndexJobs
from knowledge import import_text
from model_client import ModelError, cancel_all, generate
from rag_backend import graph_view, index_status, query_context
from retrieval import cancel_queries, search_knowledge
from runtime import DatabaseLock
from settings import load_env
from store import Store
from titling import name_import

ROOT = Path(__file__).resolve().parent


def make_server(store, port=8501):
    instance = DatabaseLock(store.path).acquire()
    turn_lock = threading.Lock()
    session_lock = threading.Lock()
    active = {"session": None}
    jobs = IndexJobs(store)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def send(self, value, status=200, html=False):
            body = (
                value
                if html
                else json.dumps(value, ensure_ascii=False, allow_nan=False).encode(
                    "utf-8"
                )
            )
            self.send_response(status)
            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8"
                if html
                else "application/json; charset=utf-8",
            )
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def guard(self, write=False):
            allowed = {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }
            if self.headers.get("Host") not in allowed:
                raise AppError("ORIGIN_REJECTED", "请求主机不匹配。", 403)
            origin = self.headers.get("Origin")
            if write and origin and origin not in {"http://" + h for h in allowed}:
                raise AppError("ORIGIN_REJECTED", "请从本地页面操作。", 403)

        def read_json(self):
            if (
                self.headers.get("Content-Type", "").split(";")[0].strip()
                != "application/json"
            ):
                raise AppError("UNSUPPORTED_MEDIA_TYPE", "请求必须是 JSON。", 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise AppError("INVALID_INPUT", "请求长度不正确。") from None
            if length > 6400000:
                raise AppError("PAYLOAD_TOO_LARGE", "请求过大。", 413)
            if length <= 0:
                raise AppError("INVALID_INPUT", "请求体不能为空，请传 JSON 对象。")
            try:

                def reject_constant(value):
                    raise ValueError(value)

                data = json.loads(
                    self.rfile.read(length), parse_constant=reject_constant
                )
            except (ValueError, UnicodeError):
                raise AppError("INVALID_INPUT", "JSON 格式不正确。") from None
            if not isinstance(data, dict):
                raise AppError("INVALID_INPUT", "请求体必须是 JSON 对象。")
            return data

        def page(self, query):
            values = []
            for name, default in [("page", "1"), ("page_size", "20")]:
                raw = query.get(name, [default])
                if len(raw) != 1 or not re.fullmatch("[1-9][0-9]*", raw[0]):
                    raise AppError("INVALID_INPUT", "分页参数无效。")
                values.append(int(raw[0]))
            if values[1] > 100:
                raise AppError("INVALID_INPUT", "每页最多100项。")
            return values

        def session_id(self, query):
            raw = query.get("session_id", [])
            if len(raw) != 1:
                raise AppError("INVALID_INPUT", "缺少会话编号。")
            self.require_session(raw[0])
            return raw[0]

        def require_session(self, sid):
            try:
                uuid.UUID(sid)
            except (ValueError, TypeError, AttributeError):
                raise AppError("INVALID_INPUT", "会话编号无效。") from None
            if not store.has_session(sid):
                raise AppError("NOT_FOUND", "会话不存在。", 404)

        def handle_api(self, method):
            try:
                self.guard(method != "GET")
                route = urlparse(self.path)
                path = route.path
                query = parse_qs(route.query, keep_blank_values=True)
                data = self.read_json() if method != "GET" else None
                if method == "GET":
                    if path == "/":
                        return self.send((ROOT / "index.html").read_bytes(), html=True)
                    if path == "/api/config":
                        return self.send(
                            {
                                "model_ready": bool(
                                    os.getenv("LLM_API_KEY")
                                    or os.getenv("DEEPSEEK_API_KEY")
                                ),
                                "rag_enabled": os.getenv("RAG_ENABLED", "true").lower()
                                == "true",
                                "harness": "dsh",
                            }
                        )
                    if path == "/api/openapi.json":
                        return self.send(
                            json.loads((ROOT / "doc/openapi.json").read_text())
                        )
                    if path == "/api/documents":
                        tag = None
                        if "tag" in query:
                            if len(query["tag"]) != 1:
                                raise AppError("INVALID_INPUT", "标签参数无效。")
                            tag = text(query["tag"][0], 40, "标签")
                        return self.send(
                            store.document_page(*self.page(query), tag=tag)
                        )
                    match = re.fullmatch(r"/api/documents/([1-9][0-9]*)", path)
                    if match:
                        return self.send(store.document(int(match[1])))
                    if path == "/api/tags":
                        return self.send(store.tags())
                    if path == "/api/sessions":
                        return self.send(
                            store.paginate(store.sessions(), *self.page(query))
                        )
                    if path in ("/api/messages", "/api/runs"):
                        sid = self.session_id(query)
                        items = (
                            store.messages(sid)
                            if path.endswith("messages")
                            else store.runs(sid)
                        )
                        return self.send(store.paginate(items, *self.page(query)))
                    if path == "/api/index":
                        return self.send(
                            {**index_status(store), "job": jobs.snapshot()}
                        )
                    if path == "/api/graph":
                        return self.send(graph_view(store))
                    if path == "/api/chunks":
                        raw = query.get("ids", [])
                        if len(raw) != 1 or not re.fullmatch(
                            r"[1-9][0-9]*(,[1-9][0-9]*){0,11}", raw[0]
                        ):
                            raise AppError(
                                "INVALID_INPUT", "片段编号需要1—12个正整数。"
                            )
                        return self.send(
                            store.chunks_by_ids([int(v) for v in raw[0].split(",")])
                        )
                    if path == "/api/export":
                        with export_zip(store) as stream:
                            stream.seek(0, 2)
                            length = stream.tell()
                            stream.seek(0)
                            self.send_response(200)
                            self.send_header("Content-Type", "application/zip")
                            self.send_header(
                                "Content-Disposition",
                                'attachment; filename="personal-knowledge.zip"',
                            )
                            self.send_header("Content-Length", str(length))
                            self.send_header("Cache-Control", "no-store")
                            self.send_header("X-Content-Type-Options", "nosniff")
                            self.end_headers()
                            while part := stream.read(65536):
                                self.wfile.write(part)
                        return
                if method == "POST":
                    title_match = re.fullmatch(
                        r"/api/documents/([1-9][0-9]*)/title", path
                    )
                    if title_match:
                        validate("Empty", data)
                        doc = store.document(int(title_match[1]))
                        return self.send(
                            name_import(
                                store,
                                {
                                    "document_id": doc["id"],
                                    "created": False,
                                    "chunk_count": doc["chunk_count"],
                                    "version": doc["version"],
                                },
                                True,
                                force=True,
                            )
                        )
                    if path in ("/api/documents", "/api/notes"):
                        validate(
                            "ImportRequest"
                            if path.endswith("documents")
                            else "NoteRequest",
                            data,
                        )
                        result = import_text(
                            store,
                            data["name"],
                            data["content"],
                            "imported" if path.endswith("documents") else "note",
                        )
                        if path == "/api/documents":
                            result = name_import(
                                store, result, data.get("auto_title", False)
                            )
                        return self.send(result, 201 if result["created"] else 200)
                    if path == "/api/demo":
                        validate("Empty", data)
                        completed = []
                        try:
                            for p in sorted((ROOT / "sample_docs").glob("*.md")):
                                completed.append(
                                    import_text(
                                        store, p.name, p.read_text(encoding="utf-8")
                                    )
                                )
                        except AppError as exc:
                            exc.detail["details"]["completed"] = completed
                            raise
                        return self.send(completed)
                    if path == "/api/index":
                        validate("BuildIndexRequest", data)
                        job = jobs.start(data.get("document_id"))
                        return self.send(job, 200 if job.get("noop") else 202)
                    if path == "/api/knowledge/search":
                        validate("AgentSearchRequest", data)
                        return self.send(
                            search_knowledge(
                                store,
                                text(data["query"], 1000, "查询"),
                                lexical_only=data.get("mode", "retrieve") == "lexical",
                            )
                        )
                    if path == "/api/sessions":
                        validate("Empty", data)
                        return self.send({"session_id": store.new_session()}, 201)
                    if path == "/api/ask":
                        validate("AskRequest", data)
                        self.require_session(data["session_id"])
                        question = text(data["question"], 1000, "问题")
                        mode = data.get("mode", "lexical")
                        with session_lock:
                            if not turn_lock.acquire(blocking=False):
                                raise AppError(
                                    "TURN_BUSY", "上一问题仍在处理，请稍后重试。", 409
                                )
                            try:
                                self.require_session(data["session_id"])
                            except BaseException:
                                turn_lock.release()
                                raise
                            active["session"] = data["session_id"]
                        try:
                            # Lexical never inspects optional model providers.
                            ctx = (
                                {
                                    "revision": store.rag_state()["revision"],
                                    "generation": None,
                                    "profile": None,
                                    "index_status": "not_run",
                                    "message": "仅本地关键词",
                                }
                                if mode == "lexical"
                                else query_context(store)
                            )
                            result = {
                                "turn_id": str(uuid.uuid4()),
                                "answer": "",
                                "sources": [],
                                "status": "model_error"
                                if mode == "generate"
                                else "retrieval_error",
                                "retrieval": None,
                                "error": None,
                                "mode": mode,
                            }
                            run = None
                            try:
                                if mode == "generate":
                                    run = generate(question, store, ctx)
                                    result.update(
                                        answer=run["answer"],
                                        sources=run["sources"],
                                        status=run["status"],
                                        retrieval=run.get("retrieval"),
                                    )
                                else:
                                    bundle = search_knowledge(
                                        store,
                                        question,
                                        context=ctx,
                                        lexical_only=mode == "lexical",
                                    )
                                    hits = bundle["hits"]
                                    result.update(
                                        sources=hits, retrieval=bundle["retrieval"]
                                    )
                                    failed = any(
                                        r["status"] == "error"
                                        for r in bundle["retrieval"]["routes"]
                                    )
                                    if not hits and failed:
                                        raise AppError(
                                            "RETRIEVAL_FAILED",
                                            "检索服务未完成，暂未取得可靠证据。",
                                        )
                                    result.update(
                                        status="retrieval_only"
                                        if hits
                                        else "no_evidence",
                                        answer=(
                                            "找到以下原文候选，尚未生成回答：\n"
                                            + "\n".join(
                                                "["
                                                + h["source_id"]
                                                + "] "
                                                + h["name"]
                                                + " · 片段 "
                                                + str(h["ordinal"])
                                                for h in hits
                                            )
                                        )
                                        if hits
                                        else "没有检索到相关资料。请补充资料或换词检索。",
                                    )
                            except ModelError as exc:
                                run = exc.run
                                result.update(
                                    answer=str(exc),
                                    error={
                                        "code": exc.code,
                                        "message": str(exc),
                                        "details": {},
                                    },
                                    sources=[],
                                )
                            except AppError as exc:
                                result.update(
                                    answer=str(exc), error=exc.detail, sources=[]
                                )
                            return self.send(
                                store.save_result(
                                    data["session_id"],
                                    question,
                                    result,
                                    ctx["revision"],
                                    run,
                                )
                            )
                        finally:
                            with session_lock:
                                active["session"] = None
                                turn_lock.release()
                match = re.fullmatch(r"/api/documents/([1-9][0-9]*)(/tags)?", path)
                if match:
                    did = int(match[1])
                    if method == "PATCH" and not match[2]:
                        validate("PatchRequest", data)
                        return self.send(
                            store.edit_document(
                                did,
                                data["expected_version"],
                                {
                                    k: v
                                    for k, v in data.items()
                                    if k != "expected_version"
                                },
                            )
                        )
                    if method == "PUT" and match[2]:
                        validate("TagRequest", data)
                        return self.send(
                            store.replace_tags(
                                did, data["expected_version"], data["tags"]
                            )
                        )
                    if method == "DELETE" and not match[2]:
                        validate("VersionRequest", data)
                        store.delete_document(did, data["expected_version"])
                        return self.send({"ok": True})
                match = re.fullmatch(r"/api/sessions/([^/]+)", path)
                if method == "DELETE" and match:
                    validate("Empty", data)
                    self.require_session(match[1])
                    # Serialize deletion with turn-start to prevent session disappearance mid-save.
                    with session_lock:
                        if active["session"] == match[1]:
                            raise AppError(
                                "SESSION_BUSY", "问答处理中，请结束后再删除会话。", 409
                            )
                        store.delete_session(match[1])
                    return self.send({"ok": True})
                raise AppError("NOT_FOUND", "未找到接口。", 404)
            except AppError as exc:
                self.send({"error": exc.detail}, exc.status)
            except sqlite3.Error:
                self.send(
                    {
                        "error": {
                            "code": "STORAGE_ERROR",
                            "message": "数据库操作失败，请检查目录权限和磁盘。",
                            "details": {},
                        }
                    },
                    500,
                )
            except (ValueError, TypeError, UnicodeError):
                self.send(
                    {
                        "error": {
                            "code": "INVALID_INPUT",
                            "message": "输入格式不正确。",
                            "details": {},
                        }
                    },
                    400,
                )
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self.send(
                    {
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "操作未完成，请检查配置后重试。",
                            "details": {},
                        }
                    },
                    500,
                )

        def do_GET(self):
            self.handle_api("GET")

        def do_POST(self):
            self.handle_api("POST")

        def do_PATCH(self):
            self.handle_api("PATCH")

        def do_PUT(self):
            self.handle_api("PUT")

        def do_DELETE(self):
            self.handle_api("DELETE")

    class Server(ThreadingHTTPServer):
        def server_close(self):
            jobs.close()
            cancel_all()
            cancel_queries()
            try:
                super().server_close()
            finally:
                instance.close()

    try:
        server = Server(("127.0.0.1", port), Handler)
    except BaseException:
        instance.close()
        raise
    server.index_jobs = jobs
    return server


def main():
    p = argparse.ArgumentParser(description="个人知识管理助手")
    p.add_argument("--port", type=int, default=8501)
    p.add_argument("--db", default=str(ROOT / "data/knowledge.db"))
    a = p.parse_args()
    load_env(ROOT / ".env")
    server = make_server(Store(a.db), a.port)
    print(f"打开浏览器：http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

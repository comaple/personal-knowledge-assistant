"""Real LightRAG + local scripted model/embedding HTTP; no paid model calls."""

import importlib.util
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from graph_tools import execute_graph_plan, inspect_graph
from knowledge import import_text, retrieve
from rag_backend import graph_view, index_root, index_status
from retrieval import balanced_pool, search_knowledge, select_evidence
from store import Store


class RetrievalUnitTest(unittest.TestCase):
    def test_candidate_collection_is_balanced_and_deduplicated(self):
        pool, ranks = balanced_pool(
            {"bm25": [1, 1, 2], "vector": [2, 3], "graph": [4, 2]}
        )
        self.assertEqual(pool, [1, 2, 4, 3])
        self.assertEqual(ranks[2], {"bm25": 2, "vector": 1, "graph": 2})
        self.assertEqual(balanced_pool({"a": [1, 2, 3]}, limit=2)[0], [1, 2])

    def test_rerank_rejects_invented_evidence_ids(self):
        data = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"ordered_ids":[999],"reason":"invented"}'},
                }
            ]
        }
        with (
            patch.dict(os.environ, {"RAG_LLM_API_KEY": "offline-test-key"}),
            patch("retrieval.post_json", return_value=data),
        ):
            with self.assertRaises(ValueError):
                select_evidence("退款", [{"chunk_id": 1, "content": "提前72小时"}])

    def test_rerank_rejects_missing_duplicate_boolean_malformed_and_truncated_ids(self):
        for value, finish in [
            ({"ordered_ids": [1], "reason": "missing"}, "stop"),
            ({"ordered_ids": [1, 1], "reason": "duplicate"}, "stop"),
            ({"ordered_ids": [True, 2], "reason": "bool"}, "stop"),
            ({"ordered_ids": [1, 2], "reason": "truncated"}, "length"),
            ("not JSON", "stop"),
        ]:
            with self.subTest(value=value, finish=finish):
                response = {
                    "choices": [
                        {
                            "finish_reason": finish,
                            "message": {
                                "content": json.dumps(value)
                                if isinstance(value, dict)
                                else value
                            },
                        }
                    ]
                }
                with (
                    patch.dict(os.environ, {"RAG_LLM_API_KEY": "offline-test-key"}),
                    patch("retrieval.post_json", return_value=response),
                    self.assertRaises((ValueError, TypeError)),
                ):
                    select_evidence(
                        "退款",
                        [
                            {"chunk_id": 1, "content": "甲"},
                            {"chunk_id": 2, "content": "乙"},
                        ],
                    )
        routes = {
            name: list(range(offset, offset + 12))
            for name, offset in [("bm25", 1), ("vector", 13), ("graph", 25)]
        }
        pool, _ = balanced_pool(routes)
        self.assertEqual(len(pool), 24)
        self.assertEqual(len(set(pool)), 24)
        self.assertTrue({1, 13, 25} <= set(pool))

    def test_revision_guard_and_explicit_fallback(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, {"RAG_ENABLED": "true"}),
        ):
            store = Store(Path(temp) / "kb.db")
            old = store.rag_state()["revision"]
            import_text(store, "课.md", "摄影课退款提前72小时。")
            self.assertFalse(store.publish_index("old", old, "profile"))
            result = search_knowledge(store, "退款")
            self.assertEqual(result["retrieval"]["mode"], "lexical_fallback")
            self.assertEqual(result["hits"][0]["channels"], ["bm25"])
            self.assertEqual(result["retrieval"]["routes"][1]["status"], "not_built")


class MockProviders(BaseHTTPRequestHandler):
    requests = []
    fail_embeddings = False
    mutate = None
    message_count = 0

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append((self.path, body))
        if self.path == "/v1/messages":
            type(self).message_count += 1
            step = (type(self).message_count - 1) % 5 + 1
            if step == 1:
                name, args = "kb_search", {"query": "摄影课退费找谁？"}
            elif step == 2:
                name, args = "kb_graph_schema", {"query": "摄影课"}
            elif step == 3:
                name, args = (
                    "kb_graph_query",
                    {
                        "anchors": ["摄影课"],
                        "hops": [{"relation": "不存在的关系", "target_type": ""}],
                    },
                )
            elif step == 4:
                name, args = (
                    "kb_graph_query",
                    {
                        "anchors": ["摄影课"],
                        "hops": [
                            {"relation": "", "target_type": "organization"},
                            {"relation": "", "target_type": "policy"},
                        ],
                    },
                )
            else:
                answer = "摄影课课程事务联系青松教务。[S1] 退出课程的申请由青松教务受理，需在开课前至少72小时提交。[S2]"
            events = [
                {
                    "type": "message_start",
                    "message": {
                        "id": f"msg_{step}",
                        "model": "deepseek-flash",
                        "usage": {"input_tokens": 10, "output_tokens": 0},
                    },
                },
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": f"call_{step}",
                        "name": name,
                        "input": {},
                    }
                    if step < 5
                    else {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(args),
                    }
                    if step < 5
                    else {"type": "text_delta", "text": answer},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use" if step < 5 else "end_turn"},
                    "usage": {"output_tokens": 20},
                },
                {"type": "message_stop"},
            ]
            raw = "".join(
                "event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n"
                for e in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if self.path == "/v1/embeddings":
            if self.fail_embeddings:
                self.send_response(503)
                self.end_headers()
                return

            def vector(text):
                groups = [
                    ["摄影课"],
                    ["青松"],
                    ["退款", "退费", "退出课程", "退课", "返还"],
                    ["72小时"],
                    ["林老师"],
                    ["远航", "星际"],
                    ["受理", "办理"],
                ]
                values = [
                    float(any(word in text for word in group)) for group in groups
                ] + [0.01]
                length = math.sqrt(sum(v * v for v in values))
                return [v / length for v in values]

            response = {
                "data": [
                    {"index": i, "embedding": vector(text)}
                    for i, text in enumerate(body["input"])
                ]
            }
        else:
            self.assert_path = self.path
            text = "\n".join(m["content"] for m in body["messages"])
            if "ordered_ids" in body["messages"][0]["content"]:
                candidates = json.loads(body["messages"][-1]["content"])["candidates"]
                order = sorted(
                    candidates,
                    key=lambda c: ("72小时" not in c["content"], c["chunk_id"]),
                )
                answer = json.dumps(
                    {
                        "ordered_ids": [c["chunk_id"] for c in order],
                        "reason": "保留办理条件与联系部门的原文证据",
                    },
                    ensure_ascii=False,
                )
            elif "high_level_keywords" in text:
                answer = json.dumps(
                    {
                        "high_level_keywords": ["退课办理"],
                        "low_level_keywords": ["摄影课"],
                    },
                    ensure_ascii=False,
                )
            elif "摄影课由林老师负责" in body["messages"][-1]["content"]:
                answer = "\n".join(
                    [
                        "entity<|#|>摄影课<|#|>Course<|#|>摄影课由林老师负责，课程事务联系青松教务。",
                        "entity<|#|>青松教务<|#|>Organization<|#|>青松教务负责摄影课课程事务。",
                        "entity<|#|>林老师<|#|>Person<|#|>林老师负责摄影课。",
                        "relation<|#|>摄影课<|#|>青松教务<|#|>课程事务<|#|>摄影课课程事务联系青松教务。",
                        "relation<|#|>摄影课<|#|>林老师<|#|>负责<|#|>林老师负责摄影课。",
                        "<|COMPLETE|>",
                    ]
                )
            elif "退出课程的申请" in body["messages"][-1]["content"]:
                answer = "\n".join(
                    [
                        "entity<|#|>青松教务<|#|>Organization<|#|>青松教务负责受理退课申请。",
                        "entity<|#|>退课申请<|#|>Policy<|#|>退出课程的申请需提前72小时提交。",
                        "entity<|#|>72小时<|#|>Time<|#|>退课申请的提前提交时间。",
                        "relation<|#|>青松教务<|#|>退课申请<|#|>受理,办理<|#|>青松教务受理退出课程的申请。",
                        "relation<|#|>退课申请<|#|>72小时<|#|>期限<|#|>退出课程需提前72小时申请。",
                        "<|COMPLETE|>",
                    ]
                )
            else:
                answer = "青松教务负责摄影课课程事务，并受理提前72小时提交的退课申请。"
            if type(self).mutate:
                callback, type(self).mutate = type(self).mutate, None
                callback()
            response = {
                "choices": [{"finish_reason": "stop", "message": {"content": answer}}]
            }
        raw = json.dumps(response, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@unittest.skipUnless(
    importlib.util.find_spec("lightrag"),
    "install requirements-rag.txt to run real LightRAG tests",
)
class LightRagTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "kb.db")
        self.doc1 = import_text(
            self.store, "课程联系.md", "摄影课由林老师负责，课程事务联系青松教务。"
        )
        self.doc2 = import_text(
            self.store,
            "办理规则.md",
            "青松教务负责受理退出课程的申请，需在开课前至少72小时提交。",
        )
        MockProviders.requests = []
        MockProviders.fail_embeddings = False
        MockProviders.mutate = None
        MockProviders.message_count = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockProviders)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        base = "http://127.0.0.1:" + str(self.server.server_port) + "/v1"
        env = dict(
            RAG_ENABLED="true",
            RAG_LLM_BASE_URL=base,
            RAG_LLM_MODEL="offline-graph-model",
            RAG_LLM_API_KEY="offline-test-key",
            EMBEDDING_BASE_URL=base,
            EMBEDDING_MODEL="offline-8d",
            EMBEDDING_DIM="8",
            EMBEDDING_API_KEY="offline-test-key",
        )
        self.environ = patch.dict(os.environ, env)
        self.environ.start()
        self.addCleanup(self.environ.stop)

    def build(self):
        # Fresh process also proves persistence survives process exit.
        result = subprocess.run(
            [sys.executable, "rag_backend.py", "build", "--db", str(self.store.path)],
            text=True,
            capture_output=True,
            timeout=120,
        )
        if result.returncode:
            self.fail(result.stdout + "\n" + result.stderr[-6000:])
        return json.loads(result.stdout)

    def test_real_graph_vector_recall_and_persistent_provenance(self):
        status = self.build()
        self.assertEqual(status["status"], "ready")
        self.assertGreaterEqual(status["counts"]["entities"], 5)
        self.assertGreaterEqual(status["counts"]["relations"], 4)
        graph = graph_view(Store(self.store.path))
        self.assertTrue(
            any(
                e["source"] == "青松教务" or e["target"] == "青松教务"
                for e in graph["edges"]
            )
        )
        query = "摄影课退费找谁？"
        self.assertNotIn(
            "办理规则.md", [h["name"] for h in retrieve(self.store, query, 12)]
        )
        result = search_knowledge(self.store, query)
        self.assertEqual(result["retrieval"]["mode"], "hybrid", result)
        self.assertEqual(result["retrieval"]["ranking"]["status"], "ok", result)
        policy = next(h for h in result["hits"] if h["name"] == "办理规则.md")
        self.assertIn("vector", policy["channels"])
        self.assertIn("graph", policy["channels"])
        self.assertIn("72小时", policy["content"])
        self.assertTrue(policy["graph_paths"])
        self.assertEqual(policy["source_id"], "S" + str(policy["chunk_id"]))
        self.assertEqual(
            len({h["chunk_id"] for h in result["hits"]}), len(result["hits"])
        )
        if os.getenv("PKA_RECORD_RAG_EXAMPLE") == "1":
            output = {
                "note": "真实 LightRAG 1.5.7 / NanoVectorDB / NetworkX；模型和8维向量由本地脚本模拟，不是检索质量评测。",
                "query": query,
                "lexical_hits": [h["name"] for h in retrieve(self.store, query)],
                "graph": graph,
                "result": result,
            }
            Path("doc/rag-run-example.json").write_text(
                json.dumps(output, ensure_ascii=False, indent=2) + "\n"
            )

    def test_identical_chunks_keep_all_sqlite_sources(self):
        prefix = ("摄影课由林老师负责，课程事务联系青松教务。" * 40)[:800]
        import_text(self.store, "相同开头甲.md", prefix + "甲记录")
        import_text(self.store, "相同开头乙.md", prefix + "乙记录")
        _, chunks = self.store.corpus_snapshot()
        repeated = {c["chunk_id"] for c in chunks if c["content"] == prefix}
        self.assertEqual(len(repeated), 2)
        self.assertEqual(self.build()["status"], "ready")
        state = self.store.rag_state()
        manifest = json.loads(
            (index_root(self.store) / state["generation"] / "manifest.json").read_text()
        )
        self.assertTrue(
            any(
                repeated.issubset(values)
                for values in map(set, manifest["source_map"].values())
            )
        )
        self.assertEqual(
            {cid for values in manifest["source_map"].values() for cid in values},
            {c["chunk_id"] for c in chunks},
        )

    def test_schema_plan_execution_and_empty_plan_repair(self):
        self.build()
        schema = inspect_graph(self.store, "摄影课")
        self.assertIn("摄影课", schema["anchors"])
        self.assertTrue(schema["relation_descriptors"])
        wrong = execute_graph_plan(
            self.store, ["摄影课"], [{"relation": "不存在的关系", "target_type": ""}]
        )
        self.assertEqual(wrong["graph_result"]["status"], "empty")
        plan = [
            {"relation": "", "target_type": "organization"},
            {"relation": "", "target_type": "policy"},
        ]
        good = execute_graph_plan(self.store, ["摄影课"], plan)
        self.assertEqual(good["graph_result"]["status"], "completed", good)
        self.assertIn(
            ["摄影课", "青松教务", "退课申请"],
            [w["nodes"] for w in good["graph_result"]["witnesses"]],
        )
        self.assertEqual(
            {h["name"] for h in good["hits"]}, {"课程联系.md", "办理规则.md"}
        )
        for bad in [
            [],
            [{"relation": "", "target_type": ""}] * 4,
            [{"sql": "DELETE FROM chunks"}],
        ]:
            with self.assertRaises(ValueError):
                execute_graph_plan(self.store, ["摄影课"], bad)

    @unittest.skipUnless(
        __import__("shutil").which("node")
        and Path("node_modules/@deepseek-ai/dsh").exists(),
        "install npm dependencies",
    )
    def test_real_dsh_explores_executes_repairs_and_persists_witness(self):
        from urllib import request

        from app import make_server

        self.build()
        base = "http://127.0.0.1:" + str(self.server.server_port)
        with patch.dict(
            os.environ,
            {
                "LLM_API_KEY": "offline-test-key",
                "DSH_BASE_URL": base,
                "DSH_MODEL": "deepseek-flash",
            },
        ):
            app = make_server(self.store, 0)
            threading.Thread(target=app.serve_forever, daemon=True).start()
            self.addCleanup(app.server_close)
            self.addCleanup(app.shutdown)
            address = "http://127.0.0.1:" + str(app.server_port)

            def post(route, body):
                req = request.Request(
                    address + route,
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with request.urlopen(req, timeout=100) as response:
                    return json.load(response)

            sid = post("/api/sessions", {})["session_id"]
            answer = post(
                "/api/ask",
                {"session_id": sid, "question": "摄影课退费找谁？", "mode": "generate"},
            )
            self.assertEqual(answer["status"], "generated", answer)
            self.assertIn("72小时", answer["answer"])
            run = self.store.runs(sid)[0]
            self.assertEqual(run["finish_reason"], "completed")
            names = [
                e["data"]["name"] for e in run["events"] if e["type"] == "tool/call"
            ]
            self.assertEqual(
                names,
                ["kb_search", "kb_graph_schema", "kb_graph_query", "kb_graph_query"],
            )
            outcomes = [
                json.loads(e["data"]["message"]["content"][0]["text"])
                for e in run["events"]
                if e["type"] == "tool/result"
            ]
            self.assertEqual(outcomes[-2]["graph_result"]["status"], "empty")
            self.assertEqual(outcomes[-1]["graph_result"]["status"], "completed")
            if os.getenv("PKA_RECORD_RAG_EXAMPLE") == "1":
                example = {
                    "note": "真实 DSH 与 LightRAG；模型输出由本地脚本模拟。ArcRAG流程工程改编，不是作者实现或论文效果复现。",
                    "answer": answer,
                    "tool_trace": [
                        e
                        for e in run["events"]
                        if e["type"] in ("tool/call", "tool/result")
                    ],
                }
                Path("doc/agentic-graph-run-example.json").write_text(
                    json.dumps(example, ensure_ascii=False, indent=2) + "\n"
                )

    def test_http_background_build_and_graph_sources(self):
        import time
        from urllib import error, request

        from app import make_server

        app = make_server(self.store, 0)
        threading.Thread(target=app.serve_forever, daemon=True).start()
        self.addCleanup(app.server_close)
        self.addCleanup(app.shutdown)
        self.addCleanup(app.index_jobs.close)
        base = "http://127.0.0.1:" + str(app.server_port)
        req = request.Request(
            base + "/api/index",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with request.urlopen(req) as response:
            self.assertEqual(response.status, 202)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            with request.urlopen(base + "/api/index") as response:
                status = json.load(response)
            if status["job"]["status"] != "running":
                break
            time.sleep(0.2)
        self.assertEqual(status["status"], "ready", status)
        with request.urlopen(base + "/api/graph") as response:
            self.assertGreater(len(json.load(response)["edges"]), 0)
        with request.urlopen(base + "/api/chunks?ids=1,2") as response:
            self.assertEqual(len(json.load(response)), 2)
        with self.assertRaises(error.HTTPError) as bad:
            request.urlopen(base + "/api/chunks?ids=1%20OR%201")
        self.assertEqual(bad.exception.code, 400)
        bad.exception.close()

    def test_background_job_rejects_duplicate_and_stale_configuration_is_not_queried(
        self,
    ):
        import time
        from urllib import error, request

        from app import make_server

        entered, release = threading.Event(), threading.Event()
        MockProviders.mutate = lambda: (entered.set(), release.wait(10))
        app = make_server(self.store, 0)
        threading.Thread(target=app.serve_forever, daemon=True).start()
        self.addCleanup(app.server_close)
        self.addCleanup(app.shutdown)
        base = f"http://127.0.0.1:{app.server_port}"

        def start():
            return request.urlopen(
                request.Request(
                    base + "/api/index",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                )
            )

        try:
            with start() as response:
                self.assertEqual(response.status, 202)
                job_id = json.load(response)["job_id"]
            self.assertTrue(entered.wait(8))
            with self.assertRaises(error.HTTPError) as caught:
                start()
            self.assertEqual(caught.exception.code, 409)
            self.assertEqual(json.load(caught.exception)["error"]["code"], "INDEX_BUSY")
        finally:
            release.set()
        deadline = time.monotonic() + 60
        while (
            time.monotonic() < deadline
            and app.index_jobs.snapshot()["status"] == "running"
        ):
            time.sleep(0.1)
        self.assertEqual(app.index_jobs.snapshot()["status"], "completed")
        self.assertEqual(app.index_jobs.snapshot()["job_id"], job_id)
        with (
            patch.dict(os.environ, {"EMBEDDING_DIM": "9"}),
            patch(
                "retrieval.subprocess.Popen",
                side_effect=AssertionError("stale index queried"),
            ),
        ):
            self.assertEqual(index_status(self.store)["status"], "stale")
            self.assertEqual(
                search_knowledge(self.store, "摄影课")["retrieval"]["mode"],
                "lexical_fallback",
            )
        directory = index_root(self.store) / self.store.rag_state()["generation"]
        (directory / "graph.json").unlink()
        self.assertEqual(index_status(self.store)["status"], "missing")

    def test_bad_rerank_is_visible_and_keeps_real_candidate_sources(self):
        self.build()
        with patch(
            "retrieval.select_evidence", side_effect=ValueError("invalid permutation")
        ):
            result = search_knowledge(self.store, "摄影课退款")
        self.assertEqual(result["retrieval"]["ranking"]["status"], "error")
        self.assertLessEqual(len(result["hits"]), 4)
        self.assertTrue(
            all(r["candidates"] <= 12 for r in result["retrieval"]["routes"])
        )
        self.assertEqual(
            len({h["chunk_id"] for h in result["hits"]}), len(result["hits"])
        )
        self.assertTrue(any(len(h["channels"]) > 1 for h in result["hits"]))

    def test_one_real_backend_route_fails_without_losing_other_evidence(self):
        import asyncio

        import rag_backend

        self.build()
        original = rag_backend.create_rag

        async def partial(*args):
            rag = await original(*args)
            query = rag.aquery_data

            async def fail_graph(text, params):
                if params.mode == "hybrid":
                    raise RuntimeError("injected graph-only failure")
                return await query(text, params)

            rag.aquery_data = fail_graph
            return rag

        with patch.object(rag_backend, "create_rag", side_effect=partial):
            bundle = asyncio.run(rag_backend.query_index(self.store, "摄影课"))
        self.assertEqual(bundle["vector"]["status"], "ok")
        self.assertTrue(bundle["vector"]["ids"])
        self.assertEqual(bundle["graph"], {"status": "error", "ids": []})
        with patch("retrieval.subprocess.Popen") as spawn:
            process = spawn.return_value
            process.returncode = 0
            process.communicate.return_value = (json.dumps(bundle), "")
            result = search_knowledge(self.store, "摄影课")
        self.assertEqual(result["retrieval"]["mode"], "partial")
        self.assertEqual(result["retrieval"]["routes"][2]["status"], "error")
        self.assertTrue(result["hits"])
        self.assertTrue(any("vector" in h["channels"] for h in result["hits"]))

    def test_concurrent_build_edit_and_ask_cannot_publish_mixed_versions(self):
        import time
        from urllib import request

        from app import make_server

        self.build()
        old_generation = self.store.rag_state()["generation"]
        build_entered, build_release = threading.Event(), threading.Event()
        turn_entered, turn_release = threading.Event(), threading.Event()
        MockProviders.mutate = lambda: (build_entered.set(), build_release.wait(10))
        app = make_server(self.store, 0)
        threading.Thread(target=app.serve_forever, daemon=True).start()
        self.addCleanup(app.server_close)
        self.addCleanup(app.shutdown)
        base = f"http://127.0.0.1:{app.server_port}"

        def call(path, data, method="POST"):
            with request.urlopen(
                request.Request(
                    base + path,
                    data=json.dumps(data).encode(),
                    method=method,
                    headers={"Content-Type": "application/json"},
                ),
                timeout=15,
            ) as response:
                return json.load(response)

        sources = search_knowledge(self.store, "摄影课", lexical_only=True)["hits"]

        def generate(*args):
            turn_entered.set()
            if not turn_release.wait(10):
                raise RuntimeError("turn barrier timeout")
            return {
                "answer": "旧答案[S1]",
                "sources": sources,
                "status": "generated",
                "harness_session_id": "concurrent-build-run",
                "finish_reason": "completed",
                "events": [],
            }

        sid = call("/api/sessions", {})["session_id"]
        results = []
        with patch("app.generate", side_effect=generate):
            call("/api/index", {})
            self.assertTrue(build_entered.wait(8))
            worker = threading.Thread(
                target=lambda: results.append(
                    call(
                        "/api/ask",
                        {"session_id": sid, "question": "摄影课", "mode": "generate"},
                    )
                )
            )
            worker.start()
            try:
                self.assertTrue(turn_entered.wait(5))
                call(
                    "/api/documents/" + str(self.doc1["document_id"]),
                    {"expected_version": 1, "content": "摄影课现已变更"},
                    "PATCH",
                )
            finally:
                turn_release.set()
                build_release.set()
                worker.join(15)
        deadline = time.monotonic() + 60
        while (
            time.monotonic() < deadline
            and app.index_jobs.snapshot()["status"] == "running"
        ):
            time.sleep(0.1)
        self.assertEqual(results[0]["error"]["code"], "CORPUS_CHANGED")
        self.assertEqual(results[0]["sources"], [])
        self.assertEqual(
            self.store.runs(sid)[0]["harness_session_id"], "concurrent-build-run"
        )
        self.assertEqual(app.index_jobs.snapshot()["status"], "error")
        self.assertEqual(self.store.rag_state()["generation"], old_generation)
        self.assertEqual(index_status(self.store)["status"], "stale")

    def test_query_deadline_falls_back_without_leaving_processes(self):
        import retrieval

        self.build()
        with patch.object(retrieval, "QUERY_TIMEOUT_SECONDS", 0.01):
            result = search_knowledge(self.store, "摄影课")
        self.assertEqual(result["retrieval"]["routes"][1]["status"], "error")
        self.assertFalse(retrieval.QUERIES)
        self.assertTrue(
            search_knowledge(self.store, "摄影课", lexical_only=True)["hits"]
        )

    def test_large_graph_reports_full_totals_and_bounded_view(self):
        self.build()
        directory = index_root(self.store) / self.store.rag_state()["generation"]
        graph = json.loads((directory / "graph.json").read_text())
        cid = self.store.corpus_snapshot()[1][0]["chunk_id"]
        graph["nodes"] = [
            {
                "id": f"N{i}",
                "type": "concept",
                "description": "测试大图",
                "chunk_ids": [cid],
            }
            for i in range(151)
        ]
        graph["edges"] = [
            {
                "source": f"N{i % 150}",
                "target": f"N{(i + 1) % 151}",
                "relation": f"关系{i}",
                "description": "来源回指",
                "chunk_ids": [cid],
            }
            for i in range(301)
        ]
        raw = json.dumps(graph, ensure_ascii=False).encode()
        (directory / "graph.json").write_bytes(raw)
        import hashlib

        manifest = json.loads((directory / "manifest.json").read_text())
        manifest["files"]["graph.json"] = hashlib.sha256(raw).hexdigest()
        (directory / "manifest.json").write_text(json.dumps(manifest))
        result = graph_view(self.store)
        self.assertTrue(result["truncated"])
        self.assertEqual((result["total_nodes"], result["total_edges"]), (151, 301))
        self.assertEqual((len(result["nodes"]), len(result["edges"])), (150, 300))
        self.assertTrue(
            all(set(r["chunk_ids"]) <= {cid} for r in result["nodes"] + result["edges"])
        )

    def test_delete_invalidates_both_graph_and_vectors(self):
        self.build()
        self.store.delete_document(self.doc2["document_id"])
        self.assertEqual(index_status(self.store)["status"], "stale")
        self.assertEqual(graph_view(self.store)["nodes"], [])
        result = search_knowledge(Store(self.store.path), "退出课程")
        self.assertEqual(result["retrieval"]["mode"], "lexical_fallback")
        self.assertFalse(any(h["name"] == "办理规则.md" for h in result["hits"]))

    def test_failed_rebuild_cannot_replace_published_generation(self):
        self.build()
        generation = self.store.rag_state()["generation"]
        MockProviders.fail_embeddings = True
        failed = subprocess.run(
            [sys.executable, "rag_backend.py", "build", "--db", str(self.store.path)],
            text=True,
            capture_output=True,
            timeout=120,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self.store.rag_state()["generation"], generation)
        MockProviders.fail_embeddings = False
        with patch.dict(os.environ, {"EMBEDDING_DIM": "9"}):
            self.assertEqual(index_status(self.store)["status"], "stale")

    def test_lexical_ready_index_never_calls_model_providers(self):
        self.build()
        MockProviders.requests = []
        with (
            patch.dict(os.environ, {"LLM_API_KEY": "offline-test-key"}),
            patch(
                "retrieval.subprocess.Popen",
                side_effect=AssertionError("local mode spawned a process"),
            ),
        ):
            result = search_knowledge(self.store, "摄影课", lexical_only=True)
        self.assertEqual(result["retrieval"]["mode"], "lexical")
        self.assertEqual(MockProviders.requests, [])

    def test_corrupted_generation_is_not_used(self):
        status = self.build()
        directory = index_root(self.store) / status["generation"]
        manifest = json.loads((directory / "manifest.json").read_text())
        name = next(n for n in manifest["files"] if n.endswith(".json"))
        (directory / name).write_text("{}")
        self.assertEqual(index_status(self.store)["status"], "missing")
        result = search_knowledge(self.store, "摄影课")
        self.assertEqual(result["retrieval"]["mode"], "lexical_fallback")

    def test_provider_failure_is_visible_and_falls_back(self):
        self.build()
        MockProviders.fail_embeddings = True
        result = search_knowledge(self.store, "摄影课")
        self.assertEqual(result["retrieval"]["mode"], "lexical_fallback")
        self.assertEqual(result["retrieval"]["routes"][1]["status"], "error")
        self.assertEqual(result["hits"][0]["channels"], ["bm25"])
        MockProviders.fail_embeddings = False
        self.assertEqual(
            search_knowledge(self.store, "摄影课")["retrieval"]["mode"], "hybrid"
        )

    def test_edit_during_build_cannot_publish_a_stale_graph(self):
        MockProviders.mutate = lambda: import_text(self.store, "新.md", "资料已变更")
        failed = subprocess.run(
            [sys.executable, "rag_backend.py", "build", "--db", str(self.store.path)],
            text=True,
            capture_output=True,
            timeout=120,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertIsNone(self.store.rag_state()["generation"])
        self.assertEqual(list(index_root(self.store).iterdir()), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

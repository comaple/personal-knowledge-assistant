"""Disposable local API benchmark matching doc/VERIFY.md AC-15."""

import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib import request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import make_server
from knowledge import import_text
from store import Store

with tempfile.TemporaryDirectory() as d:
    store = Store(Path(d) / "benchmark.db")
    for i in range(500):
        import_text(
            store, f"资料{i}.md", f"资料编号{i} 摄影课关键词 " + ("普通正文" * 100)
        )
    app = make_server(store, 0)
    threading.Thread(target=app.serve_forever, daemon=True).start()
    opener = request.build_opener(request.ProxyHandler({}))
    base = f"http://127.0.0.1:{app.server_port}"

    def call(path, data=None):
        req = request.Request(
            base + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Content-Type": "application/json"},
        )
        with opener.open(req) as r:
            return json.load(r)

    try:
        sid = call("/api/sessions", {})["session_id"]
        report = {
            "framework": "Real HTTP / SQLite; no model calls",
            "cpu_logical": os.cpu_count(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "chunks": len(store.corpus_snapshot()[1]),
            "measurements": {},
        }
        if sys.platform == "darwin":
            report["memory_bytes"] = int(
                subprocess.check_output(["sysctl", "-n", "hw.memsize"])
            )
            info = subprocess.run(
                ["diskutil", "info", "/"], capture_output=True, text=True
            ).stdout
            report["ssd_reported"] = any(
                "Solid State" in line and "Yes" in line for line in info.splitlines()
            )
        for name, path, data in [
            ("list", "/api/documents", None),
            ("read", "/api/documents/1", None),
            (
                "lexical",
                "/api/ask",
                {"session_id": sid, "question": "摄影课关键词", "mode": "lexical"},
            ),
        ]:
            for _ in range(5):
                call(path, data)
            samples = []
            for _ in range(100):
                start = time.perf_counter()
                call(path, data)
                samples.append(round((time.perf_counter() - start) * 1000, 3))
            p95 = sorted(samples)[94]
            report["measurements"][name] = {
                "samples_ms": samples,
                "p95_ms": p95,
                "within_500ms": p95 <= 500,
            }
        report["status"] = (
            "PASS"
            if all(m["within_500ms"] for m in report["measurements"].values())
            else "FAIL"
        )
        Path("doc/verification").mkdir(exist_ok=True)
        Path("doc/verification/performance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps({k: v["p95_ms"] for k, v in report["measurements"].items()}))
    finally:
        app.shutdown()
        app.server_close()

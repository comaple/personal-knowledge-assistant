"""Optional local browser acceptance; requires externally installed Puppeteer and Chrome."""

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from app import make_server
from knowledge import import_text
from store import Store
from test_rag import MockProviders

with tempfile.TemporaryDirectory() as tmp:
    store = Store(Path(tmp) / "kb.db")
    import_text(store, "课程联系.md", "摄影课由林老师负责，课程事务联系青松教务。")
    import_text(
        store,
        "办理规则.md",
        "青松教务负责受理退出课程的申请，需在开课前至少72小时提交。",
    )
    mock = ThreadingHTTPServer(("127.0.0.1", 0), MockProviders)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{mock.server_port}/v1"
    env = {
        "RAG_ENABLED": "true",
        "RAG_LLM_BASE_URL": base,
        "RAG_LLM_API_KEY": "offline-test-key",
        "RAG_LLM_MODEL": "offline-graph-model",
        "EMBEDDING_BASE_URL": base,
        "EMBEDDING_MODEL": "offline-8d",
        "EMBEDDING_DIM": "8",
        "EMBEDDING_API_KEY": "offline-test-key",
        "LLM_API_KEY": "offline-test-key",
        "DSH_BASE_URL": base,
        "DSH_MODEL": "deepseek-flash",
    }
    try:
        with patch.dict(os.environ, env):
            build = subprocess.run(
                [sys.executable, "rag_backend.py", "build", "--db", str(store.path)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if build.returncode:
                raise RuntimeError(build.stdout + build.stderr[-3000:])
            app = make_server(store, 0)
            threading.Thread(target=app.serve_forever, daemon=True).start()
            try:
                result = subprocess.run(
                    ["node", "scripts/verify-ui.mjs"],
                    env={
                        **os.environ,
                        "PKA_UI_URL": f"http://127.0.0.1:{app.server_port}",
                    },
                    timeout=120,
                )
                if result.returncode:
                    raise SystemExit(result.returncode)
            finally:
                app.shutdown()
                app.server_close()
    finally:
        mock.shutdown()
        mock.server_close()

"""One bounded background index process per application instance."""

import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from errors import AppError
from rag_backend import index_status
from runtime import kill_tree
from store import now

ROOT = Path(__file__).resolve().parent


class IndexJobs:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.process = None
        self.worker = None
        self.state = {
            "job_id": None,
            "status": "idle",
            "message": "",
            "started_at": None,
            "finished_at": None,
        }

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def start(self):
        with self.lock:
            if self.state["status"] == "running":
                raise AppError("INDEX_BUSY", "索引正在构建，请等待。", 409)
            status = index_status(self.store)
            if status["status"] == "disabled":
                raise AppError("CONFIG_REQUIRED", "请先启用 RAG_ENABLED。", 422)
            if status["status"] == "config_error":
                raise AppError("CONFIG_INVALID", status["message"], 422)
            if not status["dependency_ready"]:
                raise AppError(
                    "DEPENDENCY_MISSING", "请安装 requirements-rag.txt。", 422
                )
            if not status["llm_ready"]:
                raise AppError("CONFIG_REQUIRED", "请配置图谱模型 Key。", 422)
            chunks = self.store.corpus_snapshot()[1]
            if not chunks:
                raise AppError("EMPTY_CORPUS", "请先保存资料。", 409)
            if len(chunks) > 500:
                raise AppError("CAPACITY_EXCEEDED", "最多构建500个片段。", 422)
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "rag_backend.py"),
                    "build",
                    "--db",
                    str(self.store.path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                cwd=ROOT,
                start_new_session=os.name != "nt",
            )
            self.state = {
                "job_id": str(uuid.uuid4()),
                "status": "running",
                "message": "正在构建图谱与向量，请保持应用运行。",
                "started_at": now(),
                "finished_at": None,
            }
            self.worker = threading.Thread(
                target=self._wait, args=(self.process,), daemon=True
            )
            self.worker.start()
            return dict(self.state)

    def _wait(self, process):
        status = "error"
        message = "构建失败，请检查配置与模型服务后重试。"
        try:
            out, _ = process.communicate(timeout=1800)
            result = json.loads(out)
            if (
                process.returncode == 0
                and isinstance(result, dict)
                and result.get("status") == "ready"
            ):
                status = "completed"
                message = "索引构建完成。"
        except (OSError, ValueError, subprocess.TimeoutExpired):
            kill_tree(process)
            process.communicate()
        with self.lock:
            self.state.update(status=status, message=message, finished_at=now())

    def close(self):
        if self.process:
            kill_tree(self.process)
        if self.worker:
            self.worker.join(timeout=5)

"""One bounded background index process per application instance."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from errors import AppError
from rag_backend import build_plan, index_status
from runtime import kill_tree
from store import now

ROOT = Path(__file__).resolve().parent


class IndexJobs:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.process = None
        self.worker = None
        self.progress_directory = None
        self.progress_file = None
        self.state = {
            "job_id": None,
            "status": "idle",
            "message": "",
            "started_at": None,
            "finished_at": None,
        }

    def snapshot(self):
        with self.lock:
            if self.state["status"] == "running" and self.progress_file:
                try:
                    value = json.loads(self.progress_file.read_text())
                    if (
                        value.get("phase") in ("preparing", "processing", "publishing")
                        and type(value.get("completed_chunks")) is int
                        and type(value.get("total_chunks")) is int
                        and 0
                        <= value["completed_chunks"]
                        <= value["total_chunks"]
                        == self.state["progress"]["total_chunks"]
                    ):
                        self.state["progress"] = {
                            k: value[k]
                            for k in ("phase", "completed_chunks", "total_chunks")
                        }
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
            return dict(self.state)

    def start(self, document_id=None):
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
            revision, chunks, _, pending = build_plan(self.store, document_id)
            if not chunks:
                raise AppError("EMPTY_CORPUS", "请先保存资料。", 409)
            if len(chunks) > 500:
                raise AppError("CAPACITY_EXCEEDED", "最多构建500个片段。", 422)
            if not pending and status["status"] == "ready":
                self.state = {
                    "job_id": None,
                    "status": "completed",
                    "noop": True,
                    "document_id": document_id,
                    "message": "本文已构建，没有新内容可处理。"
                    if document_id
                    else "没有新文档可构建。",
                    "started_at": None,
                    "finished_at": None,
                    "progress": {
                        "phase": "completed",
                        "completed_chunks": 0,
                        "total_chunks": 0,
                    },
                }
                return dict(self.state)
            self.progress_directory = tempfile.TemporaryDirectory(prefix="pka-build-")
            self.progress_file = Path(self.progress_directory.name) / "progress.json"
            self.process = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "rag_backend.py"),
                    "build",
                    "--db",
                    str(self.store.path),
                    "--progress-file",
                    str(self.progress_file),
                    "--expected-revision",
                    str(revision),
                ]
                + (
                    ["--document-id", str(document_id)]
                    if document_id is not None
                    else []
                ),
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
                "document_id": document_id,
                "progress": {
                    "phase": "preparing",
                    "completed_chunks": 0,
                    "total_chunks": len(pending),
                },
            }
            self.worker = threading.Thread(
                target=self._wait, args=(self.process,), daemon=True
            )
            self.worker.start()
            return dict(self.state)

    def _wait(self, process):
        status = "error"
        message = "构建失败。请确认模型和向量服务可用后重试；若期间修改过正文，请重新构建。本地关键词检索仍可用。"
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
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            kill_tree(process)
            process.communicate()
            if isinstance(exc, subprocess.TimeoutExpired):
                message = "构建超过30分钟已停止。请检查模型/向量服务，减少资料后重试；本地关键词检索仍可用。"
        with self.lock:
            self.state.update(status=status, message=message, finished_at=now())
            progress = self.state["progress"]
            self.state["progress"] = {
                **progress,
                "phase": status,
                "completed_chunks": progress["total_chunks"]
                if status == "completed"
                else progress["completed_chunks"],
            }
            if self.progress_directory:
                self.progress_directory.cleanup()
                self.progress_directory = None
                self.progress_file = None

    def close(self):
        if self.process:
            kill_tree(self.process)
        if self.worker:
            self.worker.join(timeout=5)

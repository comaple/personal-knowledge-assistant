"""DSH SDK process adapter. No hand-written agent or direct model HTTP loop."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from rag_backend import query_context
from runtime import kill_tree

ROOT = Path(__file__).resolve().parent
ACTIVE = set()
ACTIVE_LOCK = threading.Lock()


def cancel_all():
    with ACTIVE_LOCK:
        for process in list(ACTIVE):
            kill_tree(process)


class ModelError(Exception):
    def __init__(self, message, run=None, code="MODEL_FAILED"):
        super().__init__(message)
        self.run = run
        self.code = code


def validate_result(result):
    if result.get("budget_exceeded") or result.get("finish_reason") == "max-tokens":
        raise ModelError("本轮达到模型或工具预算。", code="BUDGET_EXCEEDED")
    if result.get("finish_reason") != "completed":
        raise ModelError("DSH 未完成本次问答，请检查模型服务和工具配置。")
    hits = result.get("sources", [])
    answer = result.get("answer", "").strip()
    if not answer:
        raise ModelError("DSH 未返回有效回答。")
    if not any(
        e["type"] == "tool/call" and e["data"]["name"] == "kb_search"
        for e in result.get("events", [])
    ):
        raise ModelError("模型未调用知识库检索工具，本次回答未通过验收。")
    successful_searches = {
        e["data"]["message"]["toolCallId"]
        for e in result.get("events", [])
        if e["type"] == "tool/result" and not e["data"]["message"].get("isError")
    }
    if not any(
        e["type"] == "tool/call"
        and e["data"]["name"] == "kb_search"
        and e["data"]["callId"] in successful_searches
        for e in result.get("events", [])
    ):
        raise ModelError("知识库检索工具未成功执行，本次回答未通过验收。")
    if not hits:
        search_ids = {
            e["data"]["callId"]
            for e in result.get("events", [])
            if e["type"] == "tool/call" and e["data"]["name"] == "kb_search"
        }
        for event in result.get("events", []):
            if (
                event["type"] != "tool/result"
                or event["data"]["message"]["toolCallId"] not in search_ids
            ):
                continue
            text = "".join(
                b.get("text", "")
                for b in event["data"]["message"].get("content", [])
                if b.get("type") == "text"
            )
            if not text:
                continue
            metadata = json.loads(text).get("retrieval", {})
            if any(
                route.get("status") == "error" for route in metadata.get("routes", [])
            ):
                raise ModelError(
                    "部分检索服务未完成，本次未取得可靠证据；请检查图谱或向量服务后重试。",
                    code="RETRIEVAL_FAILED",
                )
        result["answer"] = (
            "没有检索到相关资料。请补充文档，或使用文档里的关键词重新提问。"
        )
        result["status"] = "no_evidence"
        return result
    cited = set(re.findall(r"\[(S\d+)\]", answer))
    known = {h["source_id"] for h in hits}
    if not cited or not cited <= known:
        raise ModelError(
            "模型未提供有效的出处编号，本次回答未通过验收。", code="INVALID_CITATION"
        )
    result["answer"] = answer
    result["status"] = "generated"
    return result


def generate(question, store, context=None):
    context = context or query_context(store)
    env = os.environ.copy()
    env["LLM_API_KEY"] = env.get("LLM_API_KEY") or env.get("DEEPSEEK_API_KEY", "")
    if not env["LLM_API_KEY"]:
        raise ModelError(
            "请在 .env 中配置 LLM_API_KEY，再启用 DSH 问答。", code="MODEL_UNAVAILABLE"
        )
    node = shutil.which("node")
    if not node or not (ROOT / "node_modules/@deepseek-ai/dsh-sdk-client").exists():
        raise ModelError(
            "DSH 尚未安装：请安装 Node.js 24.9+，并在项目根目录执行 npm ci。",
            code="MODEL_UNAVAILABLE",
        )
    try:
        # Dedicated short-lived home: no global DSH settings; durable events go to SQLite.
        with tempfile.TemporaryDirectory(prefix="pka-dsh-") as home:
            payload = dict(
                question=question,
                database=str(store.path.resolve()),
                python=sys.executable,
                home=home,
                context=context,
            )
            process = subprocess.Popen(
                [node, str(ROOT / "harness/runner.mjs")],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
                cwd=ROOT,
                start_new_session=(os.name != "nt"),
            )
            with ACTIVE_LOCK:
                ACTIVE.add(process)
            try:
                output, _ = process.communicate(json.dumps(payload), timeout=150)
            except subprocess.TimeoutExpired:
                kill_tree(process)
                process.communicate()
                raise
            finally:
                with ACTIVE_LOCK:
                    ACTIVE.discard(process)
            if process.returncode:
                raise ModelError(
                    "DSH 运行失败，请检查模型名称、Messages 接口地址、Key 和网络。"
                )
            result = json.loads(output)
            # SDK events may contain provider configuration. Redact exact configured secrets.
            secrets = [
                v for k, v in env.items() if k.endswith("API_KEY") and len(v) >= 8
            ]

            def redact(value):
                if isinstance(value, str):
                    for secret in secrets:
                        value = value.replace(secret, "[REDACTED]")
                    return value
                if isinstance(value, list):
                    return [redact(v) for v in value]
                if isinstance(value, dict):
                    return {k: redact(v) for k, v in value.items()}
                return value

            result["events"] = redact(result.get("events", []))
            if result.get("budget_exceeded"):
                raise ModelError(
                    "已达到工具调用预算。", run=result, code="BUDGET_EXCEEDED"
                )

            try:
                return validate_result(result)
            except ModelError as exc:
                exc.run = result
                raise
    except subprocess.TimeoutExpired as exc:
        raise ModelError(
            "DSH 问答超过 150 秒，已终止本次运行进程。", code="TIMEOUT"
        ) from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ModelError("DSH 返回异常，请检查 Node.js 版本与安装依赖。") from exc

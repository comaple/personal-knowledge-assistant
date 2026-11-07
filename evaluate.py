"""Run the frozen quality set against an isolated empty application; never fabricates semantic scores."""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://127.0.0.1:8501")
    p.add_argument(
        "--mode", choices=["lexical", "retrieve", "generate", "all"], default="all"
    )
    p.add_argument("--output", default="doc/verification/quality.json")
    a = p.parse_args()
    if parse.urlparse(a.url).hostname not in ("127.0.0.1", "localhost"):
        p.error("仅允许隔离的本地测试实例。")
    opener = request.build_opener(request.ProxyHandler({}))

    def call(path, data=None):
        req = request.Request(
            a.url.rstrip("/") + path,
            data=json.dumps(data, ensure_ascii=False).encode()
            if data is not None
            else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with opener.open(req, timeout=160) as response:
                return json.load(response)
        except error.HTTPError as exc:
            raise RuntimeError(
                "HTTP " + str(exc.code) + "；请核对服务配置或现有资料。"
            ) from None

    config = call("/api/config")
    if a.mode in ("generate", "all") and not config["model_ready"]:
        p.exit(2, "BLOCKED：DSH Key 未配置；未导入测试资料，未产生模型调用。\n")
    if call("/api/documents")["total"]:
        p.exit(2, "请使用空的隔离数据库，拒绝向日常资料库混入测试语料。\n")
    corpus = json.loads((ROOT / "doc/fixtures/knowledge.json").read_text())
    questions = json.loads((ROOT / "doc/fixtures/questions.json").read_text())[
        "questions"
    ]
    mapping = {}
    for doc in corpus["documents"]:
        mapping[doc["fixture_id"]] = call(
            "/api/documents", {"name": doc["name"], "content": doc["content"]}
        )["document_id"]
    modes = ["lexical", "retrieve", "generate"] if a.mode == "all" else [a.mode]
    if any(mode != "lexical" for mode in modes):
        call("/api/index", {})
        deadline = time.monotonic() + 1810
        while time.monotonic() < deadline:
            state = call("/api/index")
            if state["job"]["status"] in ("completed", "error"):
                break
            time.sleep(1)
        if state["status"] != "ready":
            p.exit(2, "BLOCKED：索引未就绪；请核对模型/Embedding配置。\n")
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "corpus_sha256": hashlib.sha256(
            (ROOT / "doc/fixtures/knowledge.json").read_bytes()
        ).hexdigest(),
        "questions_sha256": hashlib.sha256(
            (ROOT / "doc/fixtures/questions.json").read_bytes()
        ).hexdigest(),
        "index": call("/api/index"),
        "results": [],
        "quality_passed": None,
        "note": "证据覆盖可自动计算；完整回答、正确拒答、冲突处理及事实支持必须人工逐题复核。用量未提供时为null，不写0。",
    }
    for mode in modes:
        sid = call("/api/sessions", {})["session_id"]
        for q in questions:
            start = time.monotonic()
            result = call(
                "/api/ask", {"session_id": sid, "question": q["question"], "mode": mode}
            )
            seen = {s["document_id"] for s in result["sources"]}
            report["results"].append(
                {
                    "id": q["id"],
                    "mode": mode,
                    "elapsed_seconds": round(time.monotonic() - start, 3),
                    "expected_behavior": q["expected_behavior"],
                    "expected_facts": q["expected_facts"],
                    "required_documents": q["required_documents"],
                    "evidence_complete": all(
                        mapping[d] in seen for d in q["required_documents"]
                    )
                    if q["required_documents"]
                    else None,
                    "response": result,
                    "human_review": {
                        "complete_answer": None,
                        "correct_abstention_or_conflict": None,
                        "unsupported_facts": None,
                        "reason": None,
                    },
                    "api_usage": None,
                    "cost": None,
                }
            )
    output = Path(a.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("已保存逐题结果：" + str(output) + "；语义与质量通过状态仍需人工复核。")


if __name__ == "__main__":
    main()

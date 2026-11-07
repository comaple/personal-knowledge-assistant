"""Whitelisted read-only knowledge operations, no DB/path/SQL in model arguments."""

import json
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contract import validate
from errors import AppError, text
from graph_tools import execute_graph_plan, inspect_graph
from rag_backend import query_context
from retrieval import cancel_queries, search_knowledge
from store import Store


def call(store, operation, args, context=None):
    context = context or query_context(store)
    store.assert_revision(context["revision"])
    if operation == "kb_search":
        validate("KnowledgeQuery", args)
        return search_knowledge(
            store, text(args["query"], 1000, "查询"), context=context
        )
    if operation == "kb_graph_schema":
        validate("KnowledgeQuery", args)
        return inspect_graph(store, text(args["query"], 1000, "查询"), context)
    if operation == "kb_graph_query":
        validate("GraphPlan", args)
        return execute_graph_plan(store, args["anchors"], args["hops"], context)
    if operation == "kb_list_documents":
        validate("Empty", args)
        rows = [
            {"name": r["name"], "chunk_count": r["chunk_count"]}
            for r in store.documents()[:100]
        ]
        store.assert_revision(context["revision"])
        return rows
    raise AppError("INVALID_INPUT", "未知工具。")


def stop(*_):
    cancel_queries()
    raise SystemExit(1)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    try:
        payload = json.load(sys.stdin)
        result = call(
            Store(sys.argv[1], readonly=True),
            sys.argv[2],
            payload["args"],
            payload.get("context"),
        )
        print(json.dumps(result, ensure_ascii=False))
    except Exception:
        print("知识库工具执行失败，请核对版本和参数。", file=sys.stderr)
        sys.exit(1)

"""Read-only graph inspection and bounded plans bound to a corpus snapshot."""

from contract import validate
from knowledge import tokenize
from rag_backend import query_context, validate_generation
from retrieval import source_row


def load_graph(store, context=None):
    ctx = context or query_context(store)
    store.assert_revision(ctx["revision"])
    graph = {"nodes": [], "edges": []}
    if ctx["generation"]:
        _, graph = validate_generation(
            store, ctx["generation"], ctx["revision"], ctx["profile"]
        )
    return ctx, graph


def inspect_graph(store, query, context=None):
    ctx, graph = load_graph(store, context)
    tokens = set(tokenize(query))
    ranked = sorted(
        graph["nodes"],
        key=lambda n: (
            -(100 if n["id"].casefold() in query.casefold() else 0)
            - len(tokens.intersection(tokenize(n["id"]))),
            n["id"],
        ),
    )
    anchors = [
        n["id"]
        for n in ranked
        if n["id"].casefold() in query.casefold()
        or tokens.intersection(tokenize(n["id"]))
    ][:8]
    near = sorted(
        (e for e in graph["edges"] if e["source"] in anchors or e["target"] in anchors),
        key=lambda e: (e["source"], e["target"]),
    )
    store.assert_revision(ctx["revision"])
    return {
        "status": "ready" if ctx["generation"] else ctx["index_status"],
        "message": ctx["message"],
        "revision": ctx["revision"],
        "generation": ctx["generation"],
        "anchors": anchors,
        "entity_types": sorted({n.get("type", "") for n in graph["nodes"]})[:40],
        "relation_descriptors": list(
            dict.fromkeys(e.get("relation") or e["description"] for e in near)
        )[:20],
        "node_properties": sorted({k for n in graph["nodes"] for k in n}),
        "edge_properties": sorted({k for e in graph["edges"] for k in e}),
    }


def execute_graph_plan(store, anchors, hops, context=None):
    validate("GraphPlan", {"anchors": anchors, "hops": hops})
    ctx, graph = load_graph(store, context)
    result = {
        "hits": [],
        "graph_result": {
            "status": ctx["index_status"],
            "diagnostic": ctx["message"],
            "revision": ctx["revision"],
            "generation": ctx["generation"],
            "witnesses": [],
        },
    }
    if not ctx["generation"]:
        return result
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = sorted(
        graph["edges"], key=lambda e: (e["source"], e["target"], e["relation"])
    )
    frontier = [
        {"nodes": [a], "relations": [], "chunk_ids": []}
        for a in sorted(set(anchors))
        if a in nodes
    ]
    for hop in hops:
        expanded = []
        for path in frontier:
            last = path["nodes"][-1]
            for e in edges:
                if last not in (e["source"], e["target"]):
                    continue
                other = e["target"] if last == e["source"] else e["source"]
                if other in path["nodes"]:
                    continue
                if (
                    hop["relation"].casefold()
                    not in (e["relation"] + " " + e["description"]).casefold()
                ):
                    continue
                if (
                    hop["target_type"]
                    and nodes[other]["type"].casefold() != hop["target_type"].casefold()
                ):
                    continue
                expanded.append(
                    {
                        "nodes": path["nodes"] + [other],
                        "relations": path["relations"]
                        + [e["relation"] or e["description"]],
                        "chunk_ids": sorted(set(path["chunk_ids"] + e["chunk_ids"])),
                    }
                )
                if len(expanded) >= 30:
                    break
            if len(expanded) >= 30:
                break
        frontier = expanded
        if not frontier:
            break
    store.assert_revision(ctx["revision"])
    if not frontier:
        result["graph_result"].update(
            status="empty", diagnostic="未匹配完整路径，请核对实际锚点/关系并修正。"
        )
        return result
    witnesses = frontier[:6]
    ids = list(dict.fromkeys(cid for w in witnesses for cid in w["chunk_ids"]))[:8]
    rows = {r["chunk_id"]: r for r in store.chunks_by_ids(ids)}
    result["hits"] = [
        source_row(
            rows[cid],
            ctx,
            rank,
            ["graph_query"],
            paths=[" — ".join(w["nodes"]) for w in witnesses if cid in w["chunk_ids"]],
        )
        for rank, cid in enumerate(ids, 1)
        if cid in rows
    ]
    store.assert_revision(ctx["revision"])
    result["graph_result"].update(
        status="completed",
        diagnostic="完整路径已执行，关系为自动抽取，事实仍须核对原文。",
        witnesses=witnesses,
    )
    return result

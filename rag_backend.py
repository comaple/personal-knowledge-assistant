"""LightRAG graph/vector snapshots. Run build/query in a separate Python process."""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import uuid
from contextlib import redirect_stdout, suppress
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request
from urllib.parse import urlparse

from settings import load_env
from store import Store

ROOT = Path(__file__).resolve().parent
LIGHTRAG_VERSION = "1.5.7"


@dataclass(frozen=True)
class RagConfig:
    llm_url: str
    llm_model: str
    llm_key: str
    embedding_url: str
    embedding_model: str
    embedding_dim: int
    embedding_key: str

    @classmethod
    def from_env(cls):
        dim = int(os.getenv("EMBEDDING_DIM", "1024"))
        if not 1 <= dim <= 8192:
            raise ValueError("EMBEDDING_DIM 需要在 1—8192 之间")
        return cls(
            os.getenv("RAG_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            os.getenv("RAG_LLM_MODEL", "deepseek-flash"),
            os.getenv("RAG_LLM_API_KEY")
            or os.getenv("LLM_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY", ""),
            os.getenv("EMBEDDING_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/"),
            os.getenv("EMBEDDING_MODEL", "bge-m3"),
            dim,
            os.getenv("EMBEDDING_API_KEY", "ollama"),
        )

    def profile(self):
        public = [
            LIGHTRAG_VERSION,
            self.llm_url,
            self.llm_model,
            self.embedding_url,
            self.embedding_model,
            self.embedding_dim,
            "pka-schema2-v1",
        ]
        return hashlib.sha256(json.dumps(public).encode()).hexdigest()


def index_root(store):
    return store.path.resolve().parent / (store.path.stem + "-rag-index")


def validate_generation(store, generation, revision, profile, check_live=True):
    if (
        not isinstance(generation, str)
        or len(generation) != 32
        or any(c not in "0123456789abcdef" for c in generation)
    ):
        raise ValueError("索引代名无效")
    directory = index_root(store) / generation
    manifest = json.loads((directory / "manifest.json").read_text())
    graph = json.loads((directory / "graph.json").read_text())
    if (
        manifest.get("format_version") not in (2, 3)
        or manifest["revision"] != revision
        or manifest["profile"] != profile
    ):
        raise ValueError("索引版本不匹配")
    for name in manifest["files"]:
        if Path(name).name != name or not (directory / name).is_file():
            raise ValueError("索引文件缺失")
        if (
            hashlib.sha256((directory / name).read_bytes()).hexdigest()
            != manifest["files"][name]
        ):
            raise ValueError("索引文件摘要不匹配")
    referenced = {cid for values in manifest["source_map"].values() for cid in values}
    if not referenced or len(referenced) != manifest["counts"]["chunks"]:
        raise ValueError("索引来源无效")
    if check_live:
        snapshot_revision, rows = store.corpus_snapshot()
        store.assert_revision(revision)
        live = {row["chunk_id"] for row in rows}
        if snapshot_revision != revision or not referenced <= live:
            raise ValueError("语料版本已变化")
        if manifest["format_version"] == 2 and referenced != live:
            raise ValueError("索引来源无效")
        document_chunks = {}
        for row in rows:
            document_chunks.setdefault(row["document_id"], set()).add(row["chunk_id"])
        if any(
            cids & referenced and not cids <= referenced
            for cids in document_chunks.values()
        ):
            raise ValueError("文档索引不完整")
    node_ids = {n["id"] for n in graph["nodes"]}
    if len(node_ids) != len(graph["nodes"]):
        raise ValueError("重复实体")
    for row in graph["nodes"] + graph["edges"]:
        if not row["chunk_ids"] or not set(row["chunk_ids"]) <= referenced:
            raise ValueError("图谱来源缺失")
    for edge in graph["edges"]:
        if edge["source"] not in node_ids or edge["target"] not in node_ids:
            raise ValueError("关系端点缺失")
    return manifest, graph


def index_status(store):
    state, documents = store.export_snapshot()
    info = {
        "status": "not_built",
        "message": "尚未构建图谱与向量索引",
        "revision": state["revision"],
        "catalog_revision": state["catalog_revision"],
        "generation": state["generation"],
        "profile": None,
        "dependency_ready": importlib.util.find_spec("lightrag") is not None,
        "llm_ready": False,
        "coverage": "none",
        "indexed_document_ids": [],
        "counts": {
            "documents": len(documents),
            "chunks": sum(row["chunk_count"] for row in documents),
            "entities": 0,
            "relations": 0,
            "indexed_chunks": 0,
        },
        "job": {
            "job_id": None,
            "status": "idle",
            "message": "",
            "started_at": None,
            "finished_at": None,
        },
    }
    if os.getenv("RAG_ENABLED", "true").lower() != "true":
        return {**info, "status": "disabled", "message": "当前仅关键词模式"}
    try:
        config = RagConfig.from_env()
        info.update(profile=config.profile(), llm_ready=bool(config.llm_key))
    except ValueError:
        return {**info, "status": "config_error", "message": "Embedding 维度配置不正确"}
    if not state["generation"]:
        return info
    if not info["dependency_ready"] or not info["llm_ready"]:
        return {**info, "status": "unavailable", "message": "图谱依赖或模型配置缺失"}
    try:
        current_revision, rows, reusable, _ = build_plan(store)
        if current_revision == info["revision"]:
            info["indexed_document_ids"] = sorted(
                {r["document_id"] for r in rows if r["chunk_id"] in reusable}
            )
            info["counts"]["indexed_chunks"] = len(reusable)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if (
        state["built_revision"] != state["revision"]
        or state["profile"] != config.profile()
    ):
        return {
            **info,
            "status": "stale",
            "message": "资料或模型配置已变更，请重建索引",
        }
    try:
        manifest, _ = validate_generation(
            store, state["generation"], state["revision"], config.profile()
        )
    except (OSError, ValueError, KeyError, TypeError):
        return {
            **info,
            "status": "missing",
            "message": "索引文件、格式或来源映射无效，请重建",
        }
    referenced = {cid for values in manifest["source_map"].values() for cid in values}
    indexed_docs = sorted(
        {r["document_id"] for r in store.chunks_by_ids(sorted(referenced))}
    )
    complete = len(referenced) == info["counts"]["chunks"]
    return {
        **info,
        "status": "ready",
        "message": (
            "图谱和向量索引就绪"
            if complete
            else f"已构建{len(indexed_docs)}/{len(documents)}份资料；其余资料仍可关键词检索"
        ),
        "coverage": "complete" if complete else "partial",
        "indexed_document_ids": indexed_docs,
        "counts": {
            **info["counts"],
            **manifest["counts"],
            "chunks": info["counts"]["chunks"],
            "indexed_chunks": len(referenced),
        },
    }


def query_context(store):
    status = index_status(store)
    return {
        "revision": status["revision"],
        "generation": status["generation"] if status["status"] == "ready" else None,
        "profile": status["profile"],
        "index_status": status["status"],
        "message": status["message"],
    }


def post_json(url, key, payload, timeout=30):
    req = request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            if len(raw) > 16 * 1024 * 1024:
                raise RuntimeError("模型响应超过大小限制")
            return json.loads(raw)
    except error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise RuntimeError(f"检索服务 HTTP {code}") from None
    except (OSError, ValueError) as exc:
        raise RuntimeError("检索服务连接失败或响应格式错误") from exc


async def create_rag(directory, config):
    from importlib.metadata import version

    if version("lightrag-hku") != LIGHTRAG_VERSION:
        raise RuntimeError("请安装 requirements-rag.txt 中固定的 LightRAG 版本")
    import numpy as np
    from lightrag import LightRAG
    from lightrag.utils import EmbeddingFunc

    async def complete(prompt, system_prompt=None, history_messages=None, **kwargs):
        if not config.llm_key:
            raise RuntimeError("请配置 RAG_LLM_API_KEY 或 LLM_API_KEY")
        messages = (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        )
        messages += list(history_messages or []) + [{"role": "user", "content": prompt}]
        payload = {
            "model": config.llm_model,
            "messages": messages,
            "stream": False,
            "max_tokens": min(int(kwargs.get("max_tokens") or 3000), 4096),
        }
        if urlparse(config.llm_url).hostname == "api.deepseek.com":
            payload["thinking"] = {"type": "disabled"}
        data = await asyncio.to_thread(
            post_json, config.llm_url + "/chat/completions", config.llm_key, payload
        )
        choice = data["choices"][0]
        if choice.get("finish_reason", "stop") != "stop":
            raise RuntimeError("图谱抽取或检索词提取未正常结束")
        text = choice["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("图谱模型返回空内容")
        return text

    async def embed(texts, **kwargs):
        data = await asyncio.to_thread(
            post_json,
            config.embedding_url + "/embeddings",
            config.embedding_key,
            {"model": config.embedding_model, "input": texts},
        )
        rows = sorted(data["data"], key=lambda row: row["index"])
        if [row["index"] for row in rows] != list(range(len(texts))):
            raise RuntimeError("Embedding 行数或编号不正确")
        vectors = np.asarray([row["embedding"] for row in rows], dtype=np.float32)
        if (
            vectors.shape != (len(texts), config.embedding_dim)
            or not np.isfinite(vectors).all()
        ):
            raise RuntimeError("Embedding 维度或数值不正确")
        if (np.linalg.norm(vectors, axis=1) == 0).any():
            raise RuntimeError("Embedding 返回了零向量")
        return vectors

    rag = LightRAG(
        working_dir=str(directory),
        workspace="",
        llm_model_func=complete,
        llm_model_name=config.llm_model,
        embedding_func=EmbeddingFunc(
            embedding_dim=config.embedding_dim,
            max_token_size=4096,
            model_name=config.embedding_model,
            func=embed,
        ),
        kv_storage="JsonKVStorage",
        graph_storage="NetworkXStorage",
        vector_storage="NanoVectorDBStorage",
        chunk_token_size=4000,
        chunk_overlap_token_size=0,
        max_parallel_insert=1,
        llm_model_max_async=2,
        embedding_func_max_async=2,
        entity_extract_max_gleaning=0,
        cosine_better_than_threshold=0.35,
        default_llm_timeout=35,
        default_embedding_timeout=35,
        addon_params={"language": "Chinese"},
    )
    await rag.initialize_storages()
    return rag


def build_plan(store, document_id=None):
    revision, rows = store.corpus_snapshot()
    if document_id is not None:
        store.document(document_id)
    config, state = RagConfig.from_env(), store.rag_state()
    refs = set()
    if state["generation"] and state["profile"] == config.profile():
        try:
            previous, _ = validate_generation(
                store,
                state["generation"],
                state["built_revision"],
                config.profile(),
                check_live=False,
            )
            refs = {cid for values in previous["source_map"].values() for cid in values}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    by_doc = {}
    for row in rows:
        by_doc.setdefault(row["document_id"], set()).add(row["chunk_id"])
    reusable = {cid for cids in by_doc.values() if cids <= refs for cid in cids}
    pending = {
        r["chunk_id"]
        for r in rows
        if r["chunk_id"] not in reusable
        and (document_id is None or r["document_id"] == document_id)
    }
    return revision, rows, reusable, pending


async def build_index(store, progress=None, document_id=None, expected_revision=None):
    config = RagConfig.from_env()
    if not config.llm_key:
        raise RuntimeError("构建图谱需要配置 RAG_LLM_API_KEY 或 LLM_API_KEY")
    revision, chunks, reusable, target_ids = build_plan(store, document_id)
    if expected_revision is not None and revision != expected_revision:
        raise RuntimeError("构建开始前资料已变化，请重新构建")
    if not chunks:
        raise ValueError("请先导入资料")
    if len(chunks) > 500:
        raise ValueError("教学版每次最多构建 500 个片段，请缩小资料集")
    report = progress or (lambda *args: None)
    report("preparing", 0, len(target_ids))
    generation = uuid.uuid4().hex
    directory = index_root(store) / generation
    directory.mkdir(parents=True)
    rag = None
    monitor = None
    published = False
    try:
        state = store.rag_state()
        if state["generation"] and state["profile"] == config.profile():
            try:
                previous, _ = validate_generation(
                    store,
                    state["generation"],
                    state["built_revision"],
                    config.profile(),
                    check_live=False,
                )
                shutil.copytree(
                    index_root(store) / state["generation"],
                    directory,
                    dirs_exist_ok=True,
                )
                (directory / "manifest.json").unlink()
                (directory / "graph.json").unlink()
            except (OSError, ValueError, KeyError, TypeError):
                if reusable:
                    raise RuntimeError("复用索引不可用，请重试构建") from None
                shutil.rmtree(directory)
                directory.mkdir()
        if document_id is not None:
            chunks = [r for r in chunks if r["chunk_id"] in target_ids | reusable]
        rag = await create_rag(directory, config)
        # LightRAG deduplicates normalized input text even with explicit IDs.
        # Retain every SQLite source behind each unique backend input.
        from lightrag.utils import sanitize_text_for_encoding

        grouped = {}
        for chunk in chunks:
            grouped.setdefault(sanitize_text_for_encoding(chunk["content"]), []).append(
                chunk
            )
        groups = list(grouped.values())
        ids = [f"sqlite-{group[0]['chunk_id']}" for group in groups]
        status_path = directory / "kv_store_doc_status.json"
        old_ids = (
            set(json.loads(status_path.read_text())) if status_path.exists() else set()
        )
        for obsolete in old_ids - set(ids):
            deletion = await rag.adelete_by_doc_id(obsolete)
            if deletion.status not in ("success", "not_found"):
                raise RuntimeError("旧片段清理失败，请重试全库构建")

        async def observe():
            while True:
                try:
                    states = await rag.doc_status.get_by_ids(ids)
                    done = sum(
                        sum(c["chunk_id"] in target_ids for c in group)
                        for group, state in zip(groups, states)
                        if state and state.get("status") == "processed"
                    )
                    report("processing", done, len(target_ids))
                except Exception:
                    pass  # Telemetry must not break the atomic build.
                await asyncio.sleep(0.5)

        report("processing", 0, len(target_ids))
        monitor = asyncio.create_task(observe())
        existing = await rag.doc_status.get_by_ids(ids)
        pending = [
            (group, value)
            for group, value, status in zip(groups, ids, existing)
            if not status or status.get("status") != "processed"
        ]
        if pending:
            await rag.ainsert(
                [g[0]["content"] for g, _ in pending],
                ids=[value for _, value in pending],
                file_paths=[f"{value}.txt" for _, value in pending],
            )
        statuses = await rag.doc_status.get_by_ids(ids)
        monitor.cancel()
        with suppress(asyncio.CancelledError):
            await monitor
        monitor = None
        if len(statuses) != len(groups) or any(
            not item or item.get("status") != "processed" for item in statuses
        ):
            raise RuntimeError("部分片段未完成图谱构建，本次索引未发布")
        report("publishing", len(target_ids), len(target_ids))
        mapping = {}
        for group, status in zip(groups, statuses):
            for key in status.get("chunks_list", []):
                mapping.setdefault(key, []).extend(chunk["chunk_id"] for chunk in group)
        nodes = await rag.chunk_entity_relation_graph.get_all_nodes()
        edges = await rag.chunk_entity_relation_graph.get_all_edges()

        def sources(raw):
            return sorted(
                {
                    value
                    for key in str(raw or "").split("<SEP>")
                    for value in mapping.get(key, [])
                }
            )

        graph = {
            "nodes": [
                {
                    "id": row["id"],
                    "type": row.get("entity_type", ""),
                    "description": row.get("description", ""),
                    "chunk_ids": sources(row.get("source_id")),
                }
                for row in nodes
            ],
            "edges": [
                {
                    "source": row["source"],
                    "target": row["target"],
                    "description": row.get("description", ""),
                    "relation": row.get("keywords", ""),
                    "chunk_ids": sources(row.get("source_id")),
                }
                for row in edges
            ],
        }
        # Close and flush ALL LightRAG stores before the generation becomes visible.
        await rag.finalize_storages()
        rag = None
        manifest = {
            "format_version": 3,
            "version": LIGHTRAG_VERSION,
            "revision": revision,
            "profile": config.profile(),
            "counts": {
                "chunks": len(chunks),
                "entities": len(nodes),
                "relations": len(edges),
            },
            "source_map": mapping,
            "files": sorted(p.name for p in directory.iterdir() if p.is_file()),
        }
        (directory / "graph.json").write_text(
            json.dumps(graph, ensure_ascii=False), encoding="utf-8"
        )
        manifest["files"] = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.iterdir()
            if p.is_file()
        }
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        validate_generation(store, generation, revision, config.profile())
        if not store.publish_index(generation, revision, config.profile()):
            raise RuntimeError("构建期间资料已变更，本次索引未发布，请重新构建")
        published = True
        return index_status(store)
    finally:
        if monitor is not None:
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor
        if rag is not None:
            await rag.finalize_storages()
        if not published:
            shutil.rmtree(directory, ignore_errors=True)


async def query_index(store, query, context=None):
    from lightrag import QueryParam

    status = context or query_context(store)
    store.assert_revision(status["revision"])
    if not status["generation"]:
        raise RuntimeError(status["message"])
    config = RagConfig.from_env()
    validate_generation(
        store, status["generation"], status["revision"], status["profile"]
    )
    source_dir = index_root(store) / status["generation"]
    manifest = json.loads((source_dir / "manifest.json").read_text())
    graph = json.loads((source_dir / "graph.json").read_text())
    # File stores are small-demo backends. Each read gets its own copy, so query caches
    # and finalization cannot mutate the published graph or race another query process.
    with tempfile.TemporaryDirectory(prefix="pka-rag-query-") as temp:
        copy = Path(temp) / "index"
        shutil.copytree(source_dir, copy)
        rag = await create_rag(copy, config)
        try:

            async def route(mode):
                try:
                    data = await rag.aquery_data(
                        query,
                        QueryParam(
                            mode=mode,
                            top_k=8,
                            chunk_top_k=12,
                            enable_rerank=False,
                            max_entity_tokens=2500,
                            max_relation_tokens=2500,
                            max_total_tokens=12000,
                        ),
                    )
                    if (
                        data.get("status") == "failure"
                        and data.get("metadata", {}).get("failure_reason")
                        != "no_results"
                    ):
                        raise RuntimeError("检索未正常完成")
                    raw = data.get("data", {}).get("chunks", [])
                    ids = list(
                        dict.fromkeys(
                            cid
                            for row in raw
                            for cid in manifest["source_map"].get(row["chunk_id"], [])
                        )
                    )
                    return {"status": "ok", "ids": ids[:12]}
                except Exception:
                    return {"status": "error", "ids": []}

            # Native hybrid retrieves from entity/relation graph; naive is text vectors.
            vector, graph_route = await asyncio.gather(route("naive"), route("hybrid"))
            return {
                "generation": status["generation"],
                "revision": manifest["revision"],
                "vector": vector,
                "graph": graph_route,
                "graph_data": graph,
            }
        finally:
            await rag.finalize_storages()


def graph_view(store):
    status = index_status(store)
    graph = {"nodes": [], "edges": []}
    if status["status"] == "ready":
        try:
            _, graph = validate_generation(
                store, status["generation"], status["revision"], status["profile"]
            )
            store.assert_revision(status["revision"])
        except (ValueError, OSError, KeyError, TypeError):
            status = {**status, "status": "stale", "message": "资料已变化，请刷新"}
            graph = {"nodes": [], "edges": []}
    return {
        "index": status,
        "nodes": graph["nodes"][:150],
        "edges": graph["edges"][:300],
        "total_nodes": len(graph["nodes"]),
        "total_edges": len(graph["edges"]),
        "truncated": len(graph["nodes"]) > 150 or len(graph["edges"]) > 300,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["build", "query"])
    parser.add_argument("--db", required=True)
    parser.add_argument("--query")
    parser.add_argument("--context")
    parser.add_argument("--progress-file")
    parser.add_argument("--document-id", type=int)
    parser.add_argument("--expected-revision", type=int)
    args = parser.parse_args()
    load_env(ROOT / ".env")

    def report(phase, completed, total):
        if not args.progress_file:
            return
        path = Path(args.progress_file)
        try:
            temp = path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(
                    {
                        "phase": phase,
                        "completed_chunks": completed,
                        "total_chunks": total,
                    }
                )
            )
            temp.replace(path)
        except OSError:
            pass

    try:
        store = Store(args.db, readonly=args.operation == "query")
        with redirect_stdout(sys.stderr):
            result = asyncio.run(
                build_index(store, report, args.document_id, args.expected_revision)
                if args.operation == "build"
                else query_index(
                    store,
                    args.query,
                    json.loads(args.context) if args.context else None,
                )
            )
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        # Provider bodies and credentials are never emitted to stdout.
        message = (
            str(exc)
            if isinstance(exc, (RuntimeError, ValueError))
            else "图谱服务未完成，请检查依赖与模型配置"
        )
        print(json.dumps({"error": message}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()

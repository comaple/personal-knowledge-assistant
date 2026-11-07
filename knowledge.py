"""Canonical text normalization, chunking and local lexical search."""

import hashlib
import re
from pathlib import Path

from errors import AppError, text

MAX_DOCUMENT_BYTES = 1048576


def tokenize(value):
    result = []
    for match in re.finditer(r"[\u3400-\u9fff]+|[a-z0-9_]+", value.lower()):
        run = match.group()
        if re.fullmatch(r"[\u3400-\u9fff]+", run):
            result.extend(run[i : i + 2] for i in range(len(run) - 1))
            if len(run) == 1:
                result.append(run)
        else:
            result.append(run)
    return list(dict.fromkeys(result))


def split_text(value, size=800, overlap=120):
    if not 0 <= overlap < size:
        raise ValueError("overlap 必须小于 size")
    pieces, start = [], 0
    while start < len(value):
        end = min(start + size, len(value))
        pieces.append(value[start:end])
        if end == len(value):
            break
        start = end - overlap
    return pieces


def normalize_content(content):
    if not isinstance(content, str):
        raise AppError("INVALID_INPUT", "正文必须是字符串。")
    try:
        size = len(content.encode("utf-8"))
    except UnicodeError:
        raise AppError("INVALID_INPUT", "正文不是有效 UTF-8 文本。") from None
    if size > MAX_DOCUMENT_BYTES:
        raise AppError("PAYLOAD_TOO_LARGE", "正文不能超过 1 MB。", 413)
    if "\x00" in content:
        raise AppError("INVALID_INPUT", "正文不能包含 NUL。")
    content = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        raise AppError("INVALID_INPUT", "正文不能为空。")
    return content


def normalize_name(name, imported=False):
    name = text(name, 120, "标题")
    if imported:
        name = Path(name.replace("\\", "/")).name.strip()
        if not name or Path(name).suffix.lower() not in (".md", ".txt"):
            raise AppError("INVALID_INPUT", "只接收 UTF-8 TXT 或 Markdown 文件。")
    return name


def digest_text(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def import_text(store, name, content, kind="imported"):
    name = normalize_name(name, kind == "imported")
    content = normalize_content(content)
    pieces = split_text(content)
    doc_id, created = store.add_document(
        name, content, digest_text(content), pieces, tokenize, kind
    )
    row = store.document(doc_id)
    return {
        "document_id": doc_id,
        "created": created,
        "chunk_count": row["chunk_count"],
        "version": row["version"],
    }


def retrieve(store, question, limit=4):
    tokens = tokenize(question)[:48]
    if not tokens:
        return []
    expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)
    hits = store.search(expression, limit)
    for hit in hits:
        hit["source_id"] = "S" + str(hit["chunk_id"])
        hit.pop("rank", None)
    return hits

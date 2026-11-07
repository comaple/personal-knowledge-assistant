"""Small lexical knowledge base. No embeddings or semantic-search claims."""
import hashlib
import re
from pathlib import Path

MAX_DOCUMENT_BYTES = 1024 * 1024


def tokenize(text):
    text = text.lower()
    result = []
    for match in re.finditer(r'[\u3400-\u9fff]+|[a-z0-9_]+', text):
        run = match.group()
        if re.fullmatch(r'[\u3400-\u9fff]+', run):
            result.extend(run[i:i+2] for i in range(len(run)-1))
            if len(run) == 1:
                result.append(run)
        else:
            result.append(run)
    return list(dict.fromkeys(result))


def split_text(text, size=800, overlap=120):
    if not 0 <= overlap < size:
        raise ValueError('overlap 必须小于 size')
    pieces = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        pieces.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return pieces


def import_text(store, name, content):
    name = Path(name.replace('\\', '/')).name[:120]
    if Path(name).suffix.lower() not in ('.txt', '.md'):
        raise ValueError('第一版只接收 UTF-8 编码的 TXT / Markdown 文件。')
    if not isinstance(content, str) or len(content.encode('utf-8')) > MAX_DOCUMENT_BYTES:
        raise ValueError('单个文件不能超过 1 MB。')
    if '\x00' in content:
        raise ValueError('请上传纯文本文件。')
    content = content.replace('\r\n', '\n').strip()
    if not content:
        raise ValueError('文件没有可导入的文本。')
    digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
    pieces = split_text(content)
    doc_id, created = store.add_document(name, content, digest, pieces, tokenize)
    return {'document_id': doc_id, 'created': created, 'chunk_count': len(pieces)}


def retrieve(store, question, limit=4):
    tokens = tokenize(question)[:48]
    if not tokens:
        return []
    # Quote each token: user input never becomes arbitrary FTS query syntax.
    expression = ' OR '.join('"' + t.replace('"', '""') + '"' for t in tokens)
    hits = store.search(expression, limit)
    for i, hit in enumerate(hits, 1):
        hit['source_id'] = 'S' + str(i)
        hit.pop('rank', None)
    return hits

"""Fixed read-only knowledge operations; stdin JSON, stdout JSON, no shell/SQL input."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from knowledge import retrieve
from store import Store


def call(store, operation, args):
    if operation == 'kb_search':
        query = args.get('query', '')
        if not isinstance(query, str) or not 0 < len(query.strip()) <= 1000:
            raise ValueError('query 需要 1—1000 字')
        hits = retrieve(store, query.strip())
        for hit in hits:
            # Stable within a turn, even after the agent changes its query.
            hit['source_id'] = 'S' + str(hit['chunk_id'])
        return hits
    if operation == 'kb_list_documents':
        return [{'name': row['name'], 'chunk_count': row['chunk_count']}
                for row in store.documents()[:100]]
    raise ValueError('未知工具')


if __name__ == '__main__':
    try:
        result = call(Store(sys.argv[1]), sys.argv[2], json.load(sys.stdin))
        print(json.dumps(result, ensure_ascii=False))
    except Exception:
        print('知识库工具执行失败，请检查数据库或输入。', file=sys.stderr)
        sys.exit(1)

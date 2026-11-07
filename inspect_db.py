"""Run after importing documents: python inspect_db.py."""
import sqlite3
from pathlib import Path

path = Path(__file__).resolve().parent / 'data' / 'knowledge.db'
if not path.exists():
    raise SystemExit('先运行 app.py 并导入资料，才会在 data 目录生成数据库。')
with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
    for name in ('documents', 'chunks', 'sessions', 'messages', 'chunk_search'):
        count = db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
        print(f'{name}: {count} 条记录')

"""Run: python app.py --port 8501 (local single-user teaching app)."""
import argparse
import json
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from knowledge import import_text, retrieve
from model_client import ModelError, generate
from store import Store

ROOT = Path(__file__).resolve().parent


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if key.strip() in ('LLM_API_KEY', 'DEEPSEEK_API_KEY', 'DSH_MODEL', 'DSH_BASE_URL'):
            os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def make_server(store, port=8501):
    turn_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def send(self, value, status=200, html=False):
            body = value if html else json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def read_json(self):
            origin = self.headers.get('Origin')
            allowed = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
            if origin and origin not in allowed:
                raise ValueError('请求来源不匹配，请从本地页面操作。')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1500000:
                raise ValueError('请求大小不合适，文件上限为 1 MB。')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('请求需要使用 JSON。')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('请求格式不正确。')
            return data

        def do_GET(self):
            route = urlparse(self.path)
            if route.path == '/':
                self.send((ROOT / 'index.html').read_bytes(), html=True)
            elif route.path == '/api/documents':
                self.send(store.documents())
            elif route.path == '/api/sessions':
                self.send(store.sessions())
            elif route.path == '/api/messages':
                sid = parse_qs(route.query).get('session_id', [''])[0]
                self.send(store.messages(sid))
            elif route.path == '/api/runs':
                sid = parse_qs(route.query).get('session_id', [''])[0]
                self.send(store.runs(sid))
            elif route.path == '/api/config':
                self.send({'model_ready': bool(os.getenv('LLM_API_KEY') or os.getenv('DEEPSEEK_API_KEY')), 'harness': 'dsh'})
            else:
                self.send({'error': '未找到资源'}, 404)

        def do_POST(self):
            try:
                data = self.read_json()
                if self.path == '/api/documents':
                    self.send(import_text(store, data.get('name', ''), data.get('content', '')))
                elif self.path == '/api/demo':
                    result = []
                    for p in sorted((ROOT / 'sample_docs').glob('*.md')):
                        result.append(import_text(store, p.name, p.read_text(encoding='utf-8')))
                    self.send(result)
                elif self.path == '/api/sessions':
                    self.send({'session_id': store.new_session()})
                elif self.path == '/api/ask':
                    sid = data.get('session_id', '')
                    question = str(data.get('question', '')).strip()
                    mode = data.get('mode', 'retrieve')
                    if not store.has_session(sid):
                        raise ValueError('请先选择或创建会话。')
                    if not question or len(question) > 1000:
                        raise ValueError('请输入 1—1000 字的问题。')
                    if mode not in ('retrieve', 'generate'):
                        raise ValueError('不支持的问答模式。')
                    if not turn_lock.acquire(blocking=False):
                        self.send({'error': '上一个问题还在处理，请稍后再试。'}, 409)
                        return
                    try:
                        run = None
                        hits = []
                        if mode == 'retrieve':
                            hits = retrieve(store, question)
                            status = 'retrieval_only' if hits else 'no_evidence'
                            answer = ('仅检索模式：找到以下候选片段，尚未调用模型生成答案。\n' + '\n'.join(
                                '[' + h['source_id'] + '] ' + h['name'] + ' · 片段 ' + str(h['ordinal']) for h in hits)
                                if hits else '没有检索到相关资料。请补充文档，或使用文档里的关键词重新提问。')
                        else:
                            try:
                                run = generate(question, store)
                                answer, hits, status = run['answer'], run['sources'], run['status']
                            except ModelError as exc:
                                run = exc.run
                                hits = run.get('sources', []) if run else []
                                answer, status = str(exc), 'model_error'
                        store.save_turn(sid, question, answer, hits, status, run=run)
                        self.send({'answer': answer, 'sources': hits, 'status': status})
                    finally:
                        turn_lock.release()
                else:
                    self.send({'error': '未找到接口'}, 404)
            except (ValueError, TypeError, UnicodeError) as exc:
                self.send({'error': str(exc)}, 400)
            except sqlite3.Error:
                self.send({'error': '本地数据库操作失败，请检查 data 目录是否可写。'}, 500)

        def do_DELETE(self):
            try:
                self.read_json()
                if self.path.startswith('/api/documents/'):
                    store.delete_document(int(self.path.rsplit('/', 1)[1]))
                elif self.path.startswith('/api/sessions/'):
                    store.delete_session(self.path.rsplit('/', 1)[1])
                else:
                    self.send({'error': '未找到接口'}, 404)
                    return
                self.send({'ok': True})
            except (ValueError, TypeError, sqlite3.Error):
                self.send({'error': '删除失败，请刷新后重试。'}, 400)

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description='个人资料问答助手（本地单人教学版）')
    parser.add_argument('--port', type=int, default=8501)
    parser.add_argument('--db', default=str(ROOT / 'data' / 'knowledge.db'))
    args = parser.parse_args()
    load_env(ROOT / '.env')
    server = make_server(Store(args.db), args.port)
    print(f'打开浏览器：http://127.0.0.1:{server.server_port}', flush=True)
    print(f'数据库：{Path(args.db).resolve()}；按 Ctrl+C 停止。', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()

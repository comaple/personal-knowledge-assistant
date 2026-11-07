"""Offline checks for storage, retrieval, HTTP routes and model-response handling."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request
from unittest.mock import patch

from app import make_server
from knowledge import import_text, retrieve, split_text, tokenize
from model_client import ModelError, validate_result
from store import Store


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.db'
        self.store = Store(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_deduplicate_restart_and_retrieve(self):
        doc = import_text(self.store, '课程.md', '摄影课退款需在开课前至少72小时申请。')
        duplicate = import_text(self.store, '同一内容.txt', '摄影课退款需在开课前至少72小时申请。')
        self.assertTrue(doc['created'])
        self.assertFalse(duplicate['created'])
        reopened = Store(self.path)
        hits = retrieve(reopened, '摄影课退款要提前多久？')
        self.assertEqual(len(reopened.documents()), 1)
        self.assertIn('72小时', hits[0]['content'])
        self.assertEqual(hits[0]['name'], '课程.md')
        self.assertEqual(hits[0]['source_id'], 'S1')

    def test_chunk_coverage_and_overlap(self):
        text = ''.join(chr(0x4e00 + n) for n in range(2000))
        chunks = split_text(text)
        restored = chunks[0] + ''.join(c[120:] for c in chunks[1:])
        self.assertEqual(restored, text)
        self.assertTrue(all(len(c) <= 800 for c in chunks))

    def test_atomic_import_failure(self):
        with self.assertRaises(RuntimeError):
            self.store.add_document('坏文档.md', '例子', 'digest', ['片段'],
                                    lambda _: (_ for _ in ()).throw(RuntimeError('test')))
        self.assertEqual(self.store.documents(), [])

    def test_delete_removes_index_but_history_snapshot_survives(self):
        doc = import_text(self.store, '课程.md', '摄影课退款需要提前72小时。')
        hits = retrieve(self.store, '退款')
        sid = self.store.new_session()
        self.store.save_turn(sid, '退款？', '提前72小时。[S1]', hits, 'generated')
        self.store.delete_document(doc['document_id'])
        reopened = Store(self.path)
        self.assertEqual(retrieve(reopened, '退款'), [])
        history = reopened.messages(sid)
        self.assertEqual(len(history), 2)
        self.assertIn('72小时', history[1]['sources'][0]['content'])
        reopened.delete_session(sid)
        self.assertEqual(reopened.messages(sid), [])

    def test_text_validation_and_fts_input(self):
        for name, content in [('a.pdf', '退款'), ('a.md', ''), ('a.md', 'a\x00b')]:
            with self.assertRaises(ValueError):
                import_text(self.store, name, content)
        import_text(self.store, 'a.md', '摄影课退款与 API 调用。')
        self.assertTrue(retrieve(self.store, '退款" OR API* NEAR('))
        self.assertEqual(retrieve(self.store, '!!!'), [])
        self.assertEqual(retrieve(self.store, '量子隧穿'), [])

    def start_server(self):
        server = make_server(self.store, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:' + str(server.server_port)

    def send(self, base, path, body, method='POST', origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        req = request.Request(base + path, data=json.dumps(body).encode(), headers=headers, method=method)
        with request.urlopen(req) as response:
            return json.load(response)

    def test_http_import_ask_persistence_and_no_unnecessary_model_call(self):
        base = self.start_server()
        self.send(base, '/api/documents', {'name': 'a.md', 'content': '退款需提前72小时。'})
        sid = self.send(base, '/api/sessions', {})['session_id']
        with patch('app.generate') as model:
            result = self.send(base, '/api/ask', {'session_id': sid, 'question': '退款', 'mode': 'retrieve'})
            self.assertEqual(result['status'], 'retrieval_only')
            miss = self.send(base, '/api/ask', {'session_id': sid, 'question': '量子隧穿', 'mode': 'retrieve'})
            self.assertEqual(miss['status'], 'no_evidence')
            model.assert_not_called()
        with patch('app.generate', side_effect=ModelError('模型超时')):
            fail = self.send(base, '/api/ask', {'session_id': sid, 'question': '退款', 'mode': 'generate'})
            self.assertEqual(fail['status'], 'model_error')
        self.assertEqual(len(Store(self.path).messages(sid)), 6)
        with self.assertRaises(error.HTTPError) as exc:
            self.send(base, '/api/sessions', {}, origin='https://unrelated.example')
        self.assertEqual(exc.exception.code, 400)
        exc.exception.close()
        with self.assertRaises(error.HTTPError) as exc2:
            request.urlopen(base + '/.env')
        exc2.exception.close()

    @staticmethod
    def dsh_result(answer='提前72小时。[S1]', hits=None):
        return {'answer': answer,
                'sources': hits if hits is not None else [{'source_id': 'S1', 'name': 'a.md', 'ordinal': 1, 'content': '退款提前72小时'}],
                'finish_reason': 'completed', 'harness_session_id': 'offline-dsh-session',
                'events': [
                    {'type': 'tool/call', 'data': {'name': 'kb_search', 'callId': 'c1', 'arguments': '{"query":"退款"}'}},
                    {'type': 'tool/result', 'data': {'message': {'toolCallId': 'c1', 'content': [], 'isError': False}}},
                ]}

    def test_dsh_answer_acceptance(self):
        good = validate_result(self.dsh_result())
        self.assertEqual(good['status'], 'generated')
        for text in ['没有引用', '非法引用[S9]', '混合引用[S1][S9]']:
            with self.assertRaises(ModelError):
                validate_result(self.dsh_result(text))
        missing = validate_result(self.dsh_result('随便编造的内容', hits=[]))
        self.assertEqual(missing['status'], 'no_evidence')
        self.assertNotIn('编造', missing['answer'])
        for field, value in [('events', []), ('finish_reason', 'error')]:
            result = self.dsh_result()
            result[field] = value
            with self.assertRaises(ModelError):
                validate_result(result)
        failed = self.dsh_result()
        failed['events'][1]['data']['message']['isError'] = True
        with self.assertRaises(ModelError):
            validate_result(failed)

    def test_dsh_trace_persist_and_session_delete(self):
        base = self.start_server()
        sid = self.send(base, '/api/sessions', {})['session_id']
        run = validate_result(self.dsh_result())
        with patch('app.generate', return_value=run) as model:
            result = self.send(base, '/api/ask', {'session_id': sid, 'question': '退款', 'mode': 'generate'})
            self.assertEqual(result['status'], 'generated')
            self.assertEqual(model.call_args.args[1].path, self.path)
        runs = Store(self.path).runs(sid)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]['harness_session_id'], 'offline-dsh-session')
        with request.urlopen(base + '/api/runs?session_id=' + sid) as response:
            self.assertEqual(json.load(response)[0]['events'][0]['type'], 'tool/call')
        failed = self.dsh_result()
        failed['finish_reason'] = 'error'
        with patch('app.generate', side_effect=ModelError('达到步数上限', run=failed)):
            response = self.send(base, '/api/ask', {'session_id': sid, 'question': '退款', 'mode': 'generate'})
            self.assertEqual(response['status'], 'model_error')
        self.assertEqual(len(Store(self.path).runs(sid)), 2)
        self.send(base, '/api/sessions/' + sid, {}, method='DELETE')
        self.assertEqual(Store(self.path).runs(sid), [])

    def test_dsh_missing_key_does_not_start_runtime(self):
        from model_client import generate
        with patch.dict('os.environ', {'LLM_API_KEY': '', 'DEEPSEEK_API_KEY': ''}), patch('model_client.subprocess.Popen') as child:
            with self.assertRaisesRegex(ModelError, 'LLM_API_KEY'):
                generate('退款', self.store)
            child.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)

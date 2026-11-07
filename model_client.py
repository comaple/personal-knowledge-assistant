"""DSH SDK process adapter. No hand-written agent or direct model HTTP loop."""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class ModelError(Exception):
    def __init__(self, message, run=None):
        super().__init__(message)
        self.run = run


def validate_result(result):
    if result.get('finish_reason') != 'completed':
        raise ModelError('DSH 未完成本次问答，可能达到步数、时间或输出上限。')
    hits = result.get('sources', [])
    answer = result.get('answer', '').strip()
    if not answer:
        raise ModelError('DSH 未返回有效回答。')
    if not any(e['type'] == 'tool/call' and e['data']['name'] == 'kb_search' for e in result.get('events', [])):
        raise ModelError('模型未调用知识库检索工具，本次回答未通过验收。')
    successful_searches = {
        e['data']['message']['toolCallId'] for e in result.get('events', [])
        if e['type'] == 'tool/result' and not e['data']['message'].get('isError')
    }
    if not any(e['type'] == 'tool/call' and e['data']['name'] == 'kb_search'
               and e['data']['callId'] in successful_searches for e in result.get('events', [])):
        raise ModelError('知识库检索工具未成功执行，本次回答未通过验收。')
    if not hits:
        result['answer'] = '没有检索到相关资料。请补充文档，或使用文档里的关键词重新提问。'
        result['status'] = 'no_evidence'
        return result
    cited = set(re.findall(r'\[(S\d+)\]', answer))
    known = {h['source_id'] for h in hits}
    if not cited or not cited <= known:
        raise ModelError('模型未提供有效的出处编号，本次回答未通过验收。')
    result['answer'] = answer
    result['status'] = 'generated'
    return result


def generate(question, store):
    env = os.environ.copy()
    env['LLM_API_KEY'] = env.get('LLM_API_KEY') or env.get('DEEPSEEK_API_KEY', '')
    if not env['LLM_API_KEY']:
        raise ModelError('请在 .env 中配置 LLM_API_KEY，再启用 DSH 问答。')
    node = shutil.which('node')
    if not node or not (ROOT / 'node_modules/@deepseek-ai/dsh-sdk-client').exists():
        raise ModelError('DSH 尚未安装：请安装 Node.js 24.9+，并在项目根目录执行 npm ci。')
    try:
        # Dedicated short-lived home: no global DSH settings; durable events go to SQLite.
        with tempfile.TemporaryDirectory(prefix='pka-dsh-') as home:
            payload = dict(question=question, database=str(store.path.resolve()), python=sys.executable, home=home)
            process = subprocess.Popen(
                [node, str(ROOT / 'harness/runner.mjs')], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                env=env, cwd=ROOT, start_new_session=(os.name != 'nt'),
            )
            try:
                output, _ = process.communicate(json.dumps(payload), timeout=150)
            except subprocess.TimeoutExpired:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise
            if process.returncode:
                raise ModelError('DSH 运行失败，请检查模型名称、Messages 接口地址、Key 和网络。')
            result = json.loads(output)
            try:
                return validate_result(result)
            except ModelError as exc:
                exc.run = result
                raise
    except subprocess.TimeoutExpired as exc:
        raise ModelError('DSH 问答超过 150 秒，已终止本次运行进程。') from exc
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ModelError('DSH 返回异常，请检查 Node.js 版本与安装依赖。') from exc

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn, execFileSync } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = process.env.PKA_TEST_PYTHON || 'python3';

function sse(content, count) {
  const tool = typeof content !== 'string';
  const events = [
    { type: 'message_start', message: { id: `msg_${count}`, model: 'deepseek-flash', usage: { input_tokens: 10, output_tokens: 0 } } },
    { type: 'content_block_start', index: 0, content_block: tool
      ? { type: 'tool_use', id: `call_${count}`, name: 'kb_search', input: {} }
      : { type: 'text', text: '' } },
    { type: 'content_block_delta', index: 0, delta: tool
      ? { type: 'input_json_delta', partial_json: JSON.stringify(content) }
      : { type: 'text_delta', text: content } },
    { type: 'content_block_stop', index: 0 },
    { type: 'message_delta', delta: { stop_reason: tool ? 'tool_use' : 'end_turn' }, usage: { output_tokens: 20 } },
    { type: 'message_stop' },
  ];
  return events.map(e => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`).join('');
}

async function scenario(t, mode) {
  const dir = await mkdtemp(path.join(tmpdir(), 'pka-dsh-test-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const database = path.join(dir, 'knowledge.db');
  execFileSync(python, ['-c',
    "from store import Store; from knowledge import import_text; import sys; import_text(Store(sys.argv[1]), '虚构课程.md', '摄影课退款需要在开课前至少72小时申请。')", database], { cwd: root });
  const requests = [];
  const server = createServer(async (req, res) => {
    let raw = '';
    for await (const part of req) raw += part;
    const body = JSON.parse(raw);
    requests.push(body);
    assert.equal(req.url, '/v1/messages');
    const count = requests.length;
    let response;
    if (mode === 'limit') response = { query: '退款' };
    else if (count === 1) response = { query: '量子隧穿' };
    else if (mode === 'miss') response = '这份资料没有相关内容。';
    else if (count === 2) response = { query: '退款' };
    else {
      const results = body.messages.flatMap(m => m.content).filter(b => b.type === 'tool_result');
      const hits = JSON.parse(results.at(-1).content.filter(b => b.type === 'text').map(b => b.text).join(''));
      response = `${hits[0].content}[${hits[0].source_id}]`;
    }
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.end(sse(response, count));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const env = { ...process.env, LLM_API_KEY: 'offline-test-key',
    DSH_MODEL: 'deepseek-flash', DSH_BASE_URL: `http://127.0.0.1:${server.address().port}` };
  if (mode === 'http') {
    const app = spawn(python, ['app.py', '--port', '0', '--db', database], { cwd: root, env });
    t.after(() => new Promise(resolve => { app.once('close', resolve); app.kill('SIGINT'); }));
    app.stderr.resume();
    const address = await new Promise((resolve, reject) => {
      let output = '';
      app.stdout.on('data', b => { output += b; const match = output.match(/http:\/\/127\.0\.0\.1:\d+/); if (match) resolve(match[0]); });
      app.once('error', reject);
    });
    const post = async (route, body) => {
      const response = await fetch(address + route, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      assert.equal(response.status, 200);
      return response.json();
    };
    const { session_id } = await post('/api/sessions', {});
    let response;
    if (process.env.PKA_PUPPETEER_PATH) {
      const { default: puppeteer } = await import(process.env.PKA_PUPPETEER_PATH);
      const browser = await puppeteer.launch({ executablePath: process.env.PKA_CHROME_PATH, headless: true, args: ['--no-sandbox'] });
      try {
        const page = await browser.newPage();
        await page.setViewport({ width: 1440, height: 1100 });
        await page.goto(address);
        await page.waitForFunction(() => document.querySelector('#sessions').value !== '');
        await page.click('#generate');
        await page.type('#question', '摄影课退款需要提前多久？');
        const received = page.waitForResponse(r => r.url().endsWith('/api/ask'));
        await page.click('#send');
        response = await (await received).json();
        await page.waitForFunction(() => document.querySelector('#status').textContent.includes('已保存'));
        await page.evaluate(() => document.querySelectorAll('details').forEach(d => d.open = true));
        assert.match(await page.$eval('body', el => el.innerText), /kb_search/);
        await page.screenshot({ path: path.join(root, 'docs/assets/dsh-app-preview.png'), fullPage: true });
        await page.setViewport({ width: 390, height: 844 });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      } finally { await browser.close(); }
    } else {
      response = await post('/api/ask', { session_id, question: '摄影课退款需要提前多久？', mode: 'generate' });
    }
    assert.equal(response.status, 'generated');
    assert.match(response.answer, /72小时/);
    const history = await (await fetch(address + '/api/messages?session_id=' + session_id)).json();
    const runs = await (await fetch(address + '/api/runs?session_id=' + session_id)).json();
    assert.equal(history.length, 2);
    assert.equal(runs.length, 1);
    assert.equal(runs[0].finish_reason, 'completed');
    assert.equal(runs[0].events.filter(e => e.type === 'tool/call').length, 2);
    return;
  }
  const run = await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['harness/runner.mjs'], {
      cwd: root, env,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let out = '', err = '';
    child.stdout.on('data', b => out += b);
    child.stderr.on('data', b => err += b);
    child.on('error', reject);
    child.on('close', code => {
      if (code !== 0) return reject(new Error(`runner exit ${code}: ${err}`));
      try { resolve(JSON.parse(out)); } catch { reject(new Error(`Invalid output: ${out}\n${err}`)); }
    });
    child.stdin.end(JSON.stringify({ question: '摄影课退款需要提前多久？', database, python,
      home: path.join(dir, 'dsh-home') }));
  });
  assert.deepEqual(requests[0].tools.map(t => t.name).sort(), ['kb_list_documents', 'kb_search']);
  assert.equal(requests[0].thinking.type, 'disabled');
  return { run, requests };
}

test('real DSH: empty search -> rewrite -> evidence -> cited answer', { timeout: 90_000 }, async t => {
  const { run, requests } = await scenario(t, 'normal');
  assert.equal(run.finish_reason, 'completed');
  assert.equal(requests.length, 3);
  assert.match(run.answer, /72小时/);
  assert.equal(run.sources.length, 1);
  assert.match(run.answer, new RegExp(`\\[${run.sources[0].source_id}\\]`));
  assert.equal(run.events.filter(e => e.type === 'tool/call').length, 2);
  assert.equal(run.events.filter(e => e.type === 'tool/result').length, 2);
  if (process.env.PKA_RECORD_EXAMPLE === '1') {
    const summary = {
      note: '真实 DSH 0.2.2-rc.1 + 本地脚本模拟 Messages 模型；使用虚构资料，不是付费模型效果评测。',
      command: 'npm run test:harness', model_requests: requests.length,
      answer: run.answer, sources: run.sources,
      trace: run.events.filter(e => ['tool/call', 'tool/result', 'turn/end'].includes(e.type)),
    };
    await writeFile(path.join(root, 'docs/dsh-run-example.json'), JSON.stringify(summary, null, 2) + '\n');
  }
});

test('real DSH: no evidence stays empty', { timeout: 90_000 }, async t => {
  const { run, requests } = await scenario(t, 'miss');
  assert.equal(run.finish_reason, 'completed');
  assert.equal(run.sources.length, 0);
  assert.equal(requests.length, 2);
});

test('real DSH: repeated tools stop after five model requests', { timeout: 90_000 }, async t => {
  const { run, requests } = await scenario(t, 'limit');
  assert.equal(requests.length, 5);
  assert.notEqual(run.finish_reason, 'completed');
});

test('HTTP app -> Python adapter -> DSH -> tools -> SQLite trace', { timeout: 90_000 }, async t => {
  await scenario(t, 'http');
});

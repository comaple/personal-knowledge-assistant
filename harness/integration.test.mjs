import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { spawn, execFileSync } from "node:child_process";
import {
  mkdtemp, rm, writeFile, readFile, mkdir, symlink, copyFile,
} from "node:fs/promises";
import { existsSync } from "node:fs";
import {
  invokeBridge,
  TOOL_TIMEOUT_MS,
  TOOL_OUTPUT_MAX_BYTES,
} from "./knowledge-plugin.mjs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const python = process.env.PKA_TEST_PYTHON || "python3";

function sse(content, count) {
  if (Array.isArray(content)) {
    const events = [
      {
        type: "message_start",
        message: {
          id: `msg_${count}`,
          model: "deepseek-flash",
          usage: { input_tokens: 10, output_tokens: 0 },
        },
      },
    ];
    content.forEach((args, index) =>
      events.push(
        {
          type: "content_block_start",
          index,
          content_block: {
            type: "tool_use",
            id: `call_${count}_${index}`,
            name: "kb_search",
            input: {},
          },
        },
        {
          type: "content_block_delta",
          index,
          delta: {
            type: "input_json_delta",
            partial_json: JSON.stringify(args),
          },
        },
        { type: "content_block_stop", index },
      ),
    );
    events.push(
      {
        type: "message_delta",
        delta: { stop_reason: "tool_use" },
        usage: { output_tokens: 20 },
      },
      { type: "message_stop" },
    );
    return events
      .map((e) => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`)
      .join("");
  }
  const tool = typeof content !== "string";
  const events = [
    {
      type: "message_start",
      message: {
        id: `msg_${count}`,
        model: "deepseek-flash",
        usage: { input_tokens: 10, output_tokens: 0 },
      },
    },
    {
      type: "content_block_start",
      index: 0,
      content_block: tool
        ? {
            type: "tool_use",
            id: `call_${count}`,
            name: content.__tool || "kb_search",
            input: {},
          }
        : { type: "text", text: "" },
    },
    {
      type: "content_block_delta",
      index: 0,
      delta: tool
        ? {
            type: "input_json_delta",
            partial_json: JSON.stringify(content.__args || content),
          }
        : { type: "text_delta", text: content },
    },
    { type: "content_block_stop", index: 0 },
    {
      type: "message_delta",
      delta: { stop_reason: tool ? "tool_use" : "end_turn" },
      usage: { output_tokens: 20 },
    },
    { type: "message_stop" },
  ];
  return events
    .map((e) => `event: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`)
    .join("");
}

async function scenario(t, mode) {
  const dir = await mkdtemp(path.join(tmpdir(), "pka-dsh-test-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const database = path.join(dir, "knowledge.db");
  execFileSync(
    python,
    [
      "-c",
      "from store import Store; from knowledge import import_text; import sys; import_text(Store(sys.argv[1]), '虚构课程.md', '摄影课退款需要在开课前至少72小时申请。')",
      database,
    ],
    { cwd: root },
  );
  const requests = [];
  const server = createServer(async (req, res) => {
    let raw = "";
    for await (const part of req) raw += part;
    const body = JSON.parse(raw);
    requests.push(body);
    assert.equal(req.url, "/v1/messages");
    const count = requests.length;
    let response;
    if (mode === "injection")
      response =
        count === 1
          ? { query: "退款" }
          : count === 2
            ? {
                __tool: "bash",
                __args: {
                  command: `touch ${path.join(dir, "forbidden-marker")}`,
                },
              }
            : "退款需提前72小时。[S1]";
    else if (mode === "tool_limit")
      response =
        count === 1
          ? Array.from({ length: 9 }, () => ({ query: "退款" }))
          : "已找到退款资料。[S1]";
    else if (mode === "token_limit") response = "因输出预算被截断的回答";
    else if (mode === "limit") response = { query: "退款" };
    else if (count === 1) response = { query: "量子隧穿" };
    else if (mode === "miss") response = "这份资料没有相关内容。";
    else if (count === 2) response = { query: "退款" };
    else {
      const results = body.messages
        .flatMap((m) => m.content)
        .filter((b) => b.type === "tool_result");
      const hits = JSON.parse(
        results
          .at(-1)
          .content.filter((b) => b.type === "text")
          .map((b) => b.text)
          .join(""),
      ).hits;
      response = `${hits[0].content}[${hits[0].source_id}]`;
    }
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    const stream = sse(response, count);
    res.end(
      mode === "token_limit"
        ? stream.replace('"end_turn"', '"max_tokens"')
        : stream,
    );
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const env = {
    ...process.env,
    LLM_API_KEY: "offline-test-key",
    DSH_MODEL: "deepseek-flash",
    DSH_BASE_URL: `http://127.0.0.1:${server.address().port}`,
  };
  if (mode === "http") {
    const app = spawn(python, ["app.py", "--port", "0", "--db", database], {
      cwd: root,
      env,
    });
    t.after(
      () =>
        new Promise((resolve) => {
          app.once("close", resolve);
          app.kill("SIGINT");
        }),
    );
    app.stderr.resume();
    const address = await new Promise((resolve, reject) => {
      let output = "";
      app.stdout.on("data", (b) => {
        output += b;
        const match = output.match(/http:\/\/127\.0\.0\.1:\d+/);
        if (match) resolve(match[0]);
      });
      app.once("error", reject);
    });
    const post = async (route, body) => {
      const response = await fetch(address + route, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      assert.equal(response.status, route === "/api/sessions" ? 201 : 200);
      return response.json();
    };
    const { session_id } = await post("/api/sessions", {});
    let response;
    response = await post("/api/ask", {
      session_id,
      question: "摄影课退款需要提前多久？ PRIVATE_OLD_TURN_123",
      mode: "generate",
    });
    assert.equal(response.status, "generated");
    assert.match(response.answer, /72小时/);
    const history = await (
      await fetch(address + "/api/messages?session_id=" + session_id)
    ).json();
    const runs = await (
      await fetch(address + "/api/runs?session_id=" + session_id)
    ).json();
    assert.equal(history.items.length, 2);
    assert.equal(runs.items.length, 1);
    assert.equal(runs.items[0].finish_reason, "completed");
    assert.equal(
      runs.items[0].events.filter((e) => e.type === "tool/call").length,
      2,
    );
    const oldRunId = runs.items[0].harness_session_id;
    requests.length = 0;
    const next = await post("/api/ask", {
      session_id,
      question: "重新询问退款时间",
      mode: "generate",
    });
    assert.equal(next.status, "generated");
    assert.equal(requests.length, 3);
    assert.ok(
      !JSON.stringify(requests).includes("PRIVATE_OLD_TURN_123"),
      "prior turn must not enter a fresh DSH prompt",
    );
    const nextRuns = await (
      await fetch(address + "/api/runs?session_id=" + session_id)
    ).json();
    assert.equal(nextRuns.items.length, 2);
    assert.notEqual(nextRuns.items[1].harness_session_id, oldRunId);
    return;
  }
  let runnerRoot = root;
  if (mode === "dotenv") {
    runnerRoot = path.join(dir, "project");
    await mkdir(path.join(runnerRoot, "harness"), { recursive: true });
    await copyFile(
      path.join(root, "harness/runner.mjs"),
      path.join(runnerRoot, "harness/runner.mjs"),
    );
    await symlink(
      path.join(root, "node_modules"), path.join(runnerRoot, "node_modules"),
    );
    for (const name of ["knowledge-plugin.mjs", "kb_bridge.py"])
      await symlink(
        path.join(root, "harness", name), path.join(runnerRoot, "harness", name),
      );
    await writeFile(
      path.join(runnerRoot, ".env"),
      "DSH_BASE_URL=https://invalid.example\nLLM_API_KEY=fixture-only-key\n",
    );
  }
  const run = await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["harness/runner.mjs"], {
      cwd: runnerRoot,
      env,
      stdio: ["pipe", "pipe", "pipe"],
    });
    let out = "",
      err = "";
    child.stdout.on("data", (b) => (out += b));
    child.stderr.on("data", (b) => (err += b));
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) return reject(new Error(`runner exit ${code}: ${err}`));
      try {
        resolve(JSON.parse(out));
      } catch {
        reject(new Error(`Invalid output: ${out}\n${err}`));
      }
    });
    child.stdin.end(
      JSON.stringify({
        question: "摄影课退款需要提前多久？",
        database,
        python,
        home: path.join(dir, "dsh-home"),
      }),
    );
  });
  assert.deepEqual(requests[0].tools.map((t) => t.name).sort(), [
    "kb_graph_query",
    "kb_graph_schema",
    "kb_list_documents",
    "kb_search",
  ]);
  assert.equal(requests[0].thinking.type, "disabled");
  if (mode === "injection")
    assert.equal(existsSync(path.join(dir, "forbidden-marker")), false);
  return { run, requests };
}

test("real DSH: project dotenv does not override or block inherited configuration", { timeout: 90_000 }, async (t) => {
  const { run, requests } = await scenario(t, "dotenv");
  assert.equal(run.finish_reason, "completed");
  assert.equal(requests.length, 3);
  assert.match(run.answer, /72小时/);
  assert.equal(run.sources.length, 1);
  assert.ok(!JSON.stringify(run).includes("fixture-only-key"));
});

test(
  "real DSH: empty search -> rewrite -> evidence -> cited answer",
  { timeout: 90_000 },
  async (t) => {
    const { run, requests } = await scenario(t, "normal");
    assert.equal(run.finish_reason, "completed");
    assert.equal(requests.length, 3);
    assert.match(run.answer, /72小时/);
    assert.equal(run.sources.length, 1);
    assert.match(run.answer, new RegExp(`\\[${run.sources[0].source_id}\\]`));
    assert.equal(run.events.filter((e) => e.type === "tool/call").length, 2);
    assert.equal(run.events.filter((e) => e.type === "tool/result").length, 2);
    if (process.env.PKA_RECORD_EXAMPLE === "1") {
      const summary = {
        note: "真实 DSH 0.2.2-rc.1 + 本地脚本模拟 Messages 模型；使用虚构资料，不是付费模型效果评测。",
        command: "npm run test:harness",
        model_requests: requests.length,
        answer: run.answer,
        sources: run.sources,
        trace: run.events.filter((e) =>
          ["tool/call", "tool/result", "turn/end"].includes(e.type),
        ),
      };
      await writeFile(
        path.join(root, "doc/dsh-run-example.json"),
        JSON.stringify(summary, null, 2) + "\n",
      );
    }
  },
);

test("real DSH: no evidence stays empty", { timeout: 90_000 }, async (t) => {
  const { run, requests } = await scenario(t, "miss");
  assert.equal(run.finish_reason, "completed");
  assert.equal(run.sources.length, 0);
  assert.equal(requests.length, 2);
});

test(
  "real DSH: repeated tools stop after five model requests",
  { timeout: 90_000 },
  async (t) => {
    const { run, requests } = await scenario(t, "limit");
    assert.equal(requests.length, 5);
    assert.notEqual(run.finish_reason, "completed");
    assert.equal(run.budget_exceeded, true);
  },
);

test(
  "HTTP app -> Python adapter -> DSH -> tools -> SQLite trace",
  { timeout: 90_000 },
  async (t) => {
    await scenario(t, "http");
  },
);

test(
  "real DSH: ninth tool call is rejected even when model completes",
  { timeout: 90_000 },
  async (t) => {
    const { run } = await scenario(t, "tool_limit");
    assert.equal(run.budget_exceeded, true);
    const results = run.events.filter((e) => e.type === "tool/result");
    assert.equal(results.filter((e) => !e.data.message.isError).length, 8);
    assert.ok(results.some((e) => e.data.message.isError));
  },
);

test(
  "real DSH: model request for a forbidden shell tool cannot execute",
  { timeout: 90_000 },
  async (t) => {
    const { run, requests } = await scenario(t, "injection");
    assert.ok(requests.length >= 2);
    assert.ok(
      run.events.some(
        (e) => e.type === "tool/result" && e.data.message.isError,
      ) || run.finish_reason !== "completed",
    );
  },
);

test(
  "tool bridge: timeout cleans even TERM-ignoring descendants in other sessions",
  { timeout: 15_000 },
  async (t) => {
    if (process.platform === "win32")
      return t.skip("POSIX signal fixture; Windows uses taskkill /T");
    const dir = await mkdtemp(path.join(tmpdir(), "pka-tool-timeout-"));
    t.after(() => rm(dir, { recursive: true, force: true }));
    const bridge = path.join(dir, "stubborn.py"),
      database = path.join(dir, "pids.json");
    await writeFile(
      bridge,
      `import signal,subprocess,sys,time,json,os
signal.signal(signal.SIGTERM,signal.SIG_IGN)
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
open(sys.argv[1],'w').write(json.dumps([os.getpid(),child.pid]))
time.sleep(60)
`,
    );
    await assert.rejects(
      invokeBridge(
        { python, bridge, database },
        "kb_search",
        { query: "退款" },
        undefined,
        500,
      ),
      /知识库工具执行失败/,
    );
    for (const pid of JSON.parse(await readFile(database, "utf8"))) {
      let state = "";
      try {
        state = execFileSync("ps", ["-o", "stat=", "-p", String(pid)], {
          encoding: "utf8",
        }).trim();
      } catch {}
      assert.ok(
        !state || state.startsWith("Z"),
        `process ${pid} still running: ${state}`,
      );
    }
    const good = path.join(dir, "good.py");
    await writeFile(good, 'print("{}")');
    assert.deepEqual(
      await invokeBridge(
        { python, bridge: good, database },
        "kb_list_documents",
        {},
        undefined,
        500,
      ),
      {},
    );
  },
);

test(
  "tool bridge: oversized stdout fails and subsequent calls recover",
  { timeout: 15_000 },
  async (t) => {
    const dir = await mkdtemp(path.join(tmpdir(), "pka-tool-size-"));
    t.after(() => rm(dir, { recursive: true, force: true }));
    const bridge = path.join(dir, "large.py"),
      database = path.join(dir, "unused.db");
    await writeFile(bridge, 'print("x"*100001)');
    await assert.rejects(
      invokeBridge(
        { python, bridge, database },
        "kb_search",
        { query: "x" },
        undefined,
        2000,
      ),
      /知识库工具执行失败/,
    );
    await writeFile(bridge, 'print("[]")');
    assert.deepEqual(
      await invokeBridge(
        { python, bridge, database },
        "kb_list_documents",
        {},
        undefined,
        2000,
      ),
      [],
    );
  },
);

test(
  "real DSH: provider output-token limit is a failed budgeted turn",
  { timeout: 90_000 },
  async (t) => {
    const { run, requests } = await scenario(t, "token_limit");
    assert.equal(requests.length, 1);
    assert.equal(run.finish_reason, "max-tokens");
    assert.equal(run.budget_exceeded, true);
    assert.equal(TOOL_TIMEOUT_MS, 75000);
    assert.equal(TOOL_OUTPUT_MAX_BYTES, 100000);
  },
);

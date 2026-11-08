import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
const { default: puppeteer } = await import(process.env.PKA_PUPPETEER_PATH);
const browser = await puppeteer.launch({
  executablePath: process.env.PKA_CHROME_PATH,
  headless: true,
  args: ["--no-sandbox"],
});
const report = {
  framework:
    "Real Chrome + isolated HTTP application; deterministic job-state responses for browser transition tests",
  checks: [],
};
try {
  const page = await browser.newPage(),
    errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => d.accept());
  await page.setViewport({ width: 1440, height: 1000 });
  await page.goto(process.env.PKA_UI_URL);
  await page.waitForFunction(() =>
    document.querySelector("#status").textContent.includes("资料库已打开"),
  );
  await page.click('[data-tab="api"]');
  const example = await page.$eval("#api-write", (e) => e.textContent);
  assert.ok(example.includes(process.env.PKA_UI_URL + "/api/documents"));
  assert.ok(
    example.includes(" \\\n  -H"),
    "curl contains a real newline after shell continuation",
  );
  assert.ok(!example.includes("\\n  -H"));
  const doc = await page.evaluate(async () => {
    const r = await fetch("/api/documents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: "智能体知识.md",
        content: "部署必须先备份，然后执行测试。",
        auto_title: false,
      }),
    });
    const d = await r.json();
    const q = await fetch("/api/knowledge/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: "部署 测试", mode: "lexical" }),
    });
    return {
      saved: d,
      search: await q.json(),
      spec: await (await fetch("/api/openapi.json")).json(),
    };
  });
  assert.ok(
    doc.search.hits.some((h) => h.document_id === doc.saved.document_id),
  );
  assert.ok(doc.spec.paths["/api/knowledge/search"]);
  report.checks.push(
    "API navigation, valid copyable multiline curl, actual agent write/search/OpenAPI without chat",
  );
  let state = await page.evaluate(() =>
    fetch("/api/index").then((r) => r.json()),
  );
  state = {
    ...state,
    status: "not_built",
    indexed_document_ids: [],
    coverage: "none",
    job: {
      job_id: null,
      status: "idle",
      message: "",
      started_at: null,
      finished_at: null,
    },
  };
  let failPoll = false,
    noopNext = false,
    posted = null;
  const id = doc.saved.document_id;
  await page.setRequestInterception(true);
  page.on("request", (req) => {
    if (!new URL(req.url()).pathname.endsWith("/api/index"))
      return void req.continue();
    if (req.method() === "POST") {
      posted = JSON.parse(req.postData());
      state.job = noopNext
        ? {
            job_id: null,
            document_id: null,
            status: "completed",
            noop: true,
            message: "没有新文档可构建。",
            started_at: null,
            finished_at: null,
            progress: {
              phase: "completed",
              completed_chunks: 0,
              total_chunks: 0,
            },
          }
        : {
            job_id: "e6afcdd4-b587-4eb2-9399-0507d187529e",
            document_id: id,
            status: "running",
            message: "构建中",
            started_at: new Date(Date.now() - 4000).toISOString(),
            finished_at: null,
            progress: {
              phase: "processing",
              completed_chunks: 1,
              total_chunks: 2,
            },
          };
      return void req.respond({
        status: noopNext ? 200 : 202,
        contentType: "application/json",
        body: JSON.stringify(state.job),
      });
    }
    if (failPoll) {
      failPoll = false;
      return void req.abort("failed");
    }
    void req.respond({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(state),
    });
  });
  await page.reload();
  await page.waitForFunction(() =>
    document.querySelector("#status").textContent.includes("资料库已打开"),
  );
  assert.ok(await page.$eval("#auto-title", (e) => e.checked));
  await page.click("#auto-title");
  assert.equal(await page.$eval("#auto-title", (e) => e.checked), false);
  await page.click(`[data-build-document="${id}"]`);
  await page.waitForFunction(() =>
    document
      .querySelector("#build-title")
      .textContent.includes("正在构建当前文档"),
  );
  assert.deepEqual(posted, { document_id: id });
  assert.equal(await page.$eval("#build-progress", (e) => e.value), 1);
  assert.ok(
    await page.$eval("#build-elapsed", (e) => e.textContent.includes("已用时")),
  );
  assert.ok(
    await page.$eval(`[data-document-id="${id}"] progress`, (e) => !e.hidden),
  );
  await page.reload();
  await page.waitForFunction(() =>
    document
      .querySelector("#build-title")
      .textContent.includes("正在构建当前文档"),
  );
  await page.setViewport({ width: 390, height: 844 });
  await page.click('[data-tab="api"]');
  assert.ok(
    await page.$eval("#build-card", (e) => e.getBoundingClientRect().width > 0),
  );
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  await mkdir("doc/assets", { recursive: true });
  await page.screenshot({
    path: "doc/assets/build-progress-mobile.png",
    fullPage: true,
  });
  report.checks.push(
    "Document-row action targets document_id; progress and elapsed survive reload/tab switch/390px",
  );
  failPoll = true;
  await page.waitForFunction(
    () => !document.querySelector("#build-connection").hidden,
    { timeout: 6000 },
  );
  state.job.progress = {
    phase: "publishing",
    completed_chunks: 2,
    total_chunks: 2,
  };
  await page.waitForFunction(
    () =>
      document.querySelector("#build-detail").textContent.includes("正在校验"),
    { timeout: 7000 },
  );
  assert.ok(
    await page.$eval("#build-progress", (e) => !e.hasAttribute("value")),
  );
  assert.ok(await page.$eval("#build-connection", (e) => e.hidden));
  report.checks.push(
    "Polling failure keeps progress and retries; publishing is indeterminate rather than premature completion",
  );
  state = {
    ...state,
    status: "ready",
    coverage: "complete",
    indexed_document_ids: [id],
    job: {
      ...state.job,
      status: "completed",
      finished_at: new Date().toISOString(),
      message: "索引构建完成。",
      progress: { phase: "completed", completed_chunks: 2, total_chunks: 2 },
    },
  };
  await page.waitForFunction(() =>
    document.querySelector("#build-title").textContent.includes("已准备好"),
  );
  assert.equal(await page.$eval("#build-progress", (e) => e.value / e.max), 1);
  assert.ok(await page.$eval("#build-open-graph", (e) => !e.hidden));
  state.job = {
    ...state.job,
    status: "error",
    message: "模型服务暂不可用，请检查后重试。",
    progress: { phase: "error", completed_chunks: 1, total_chunks: 2 },
  };
  await page.evaluate(() => indexStatus());
  assert.ok(await page.$eval("#build-retry", (e) => !e.hidden));
  assert.ok(await page.$eval("#build-progress", (e) => e.hidden));
  noopNext = true;
  await page.click("#build-global");
  await page.waitForFunction(() =>
    document.querySelector("#build-title").textContent.includes("没有新文档"),
  );
  assert.ok(await page.$eval("#build-progress", (e) => e.hidden));
  assert.deepEqual(posted, {});
  report.checks.push(
    "Completion offers next actions; failure allows retry; all-built noop is explicit and starts no progress",
  );
  state = { ...state, status: "stale", coverage: "none" };
  await page.evaluate(() => indexStatus());
  assert.ok(
    !(await page.$eval("#build-title", (e) => e.textContent)).includes(
      "没有新文档",
    ),
    "new content must invalidate an older noop notice",
  );
  assert.deepEqual(errors, []);
  report.status = "PASS";
  await writeFile(
    "doc/verification/build-ui.json",
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report));
} finally {
  await browser.close();
}

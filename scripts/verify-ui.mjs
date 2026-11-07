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
    "Real Chrome + HTTP + DSH + LightRAG; scripted model and 8d embeddings",
  checks: [],
};
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("dialog", async (d) => d.accept());
  await page.setViewport({ width: 1440, height: 1000 });
  await page.goto(process.env.PKA_UI_URL);
  await page.waitForFunction(() =>
    document.querySelector("#status").textContent.includes("资料库已打开"),
  );
  await page.click('[data-tab="graph"]');
  await page.waitForSelector("#graph-canvas circle");
  await page.click("#graph-canvas circle");
  await page.waitForFunction(() =>
    document.querySelector("#graph-sources").textContent.includes("[S"),
  );
  await mkdir("doc/assets", { recursive: true });
  await page.screenshot({
    path: "doc/assets/knowledge-graph.png",
    fullPage: true,
  });
  await page.setViewport({ width: 390, height: 844 });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "ready graph at 390px",
  );
  await page.setViewport({ width: 1440, height: 1000 });
  report.checks.push(
    "Current graph click opens source text, including 390px ready graph layout",
  );
  await page.click('[data-tab="search"]');
  await page.select("#mode", "generate");
  await page.type("#question", "摄影课退费找谁？");
  const generated = page.waitForResponse((r) => r.url().endsWith("/api/ask"));
  await page.click("#send");
  const result = await (await generated).json();
  assert.equal(result.status, "generated", JSON.stringify(result));
  assert.match(result.answer, /72小时/);
  await page.waitForFunction(() =>
    document.querySelector("#status").textContent.includes("已保存"),
  );
  await page.evaluate(() =>
    document
      .querySelectorAll("#messages details")
      .forEach((d) => (d.open = true)),
  );
  await page.waitForFunction(() =>
    document.querySelector("#messages").textContent.includes("kb_graph_schema"),
  );
  report.checks.push(
    "DSH answer, source snapshots and real tool traces render",
  );
  await page.click('[data-tab="library"]');
  await page.click("#new-note");
  await page.type("#note-name", "项目笔记");
  await page.type(
    "#note-content",
    "项目资料要记录来源、关键结论和下一步行动。\n\n本周整理：\n- 用标签归类阅读笔记\n- 核对问答引用原文\n- 每月导出一份资料备份",
  );
  await page.type("#note-tags", "项目, 待整理");
  await page.click("#save-note");
  await page.waitForFunction(() =>
    document.querySelector("#status").textContent.includes("资料已保存"),
  );
  await page.waitForSelector('#tag-filter option[value="项目"]');
  await page.select("#tag-filter", "项目");
  await page.waitForFunction(() =>
    document.querySelector("#doc-total").textContent.includes("1 份"),
  );
  report.checks.push("Create note and tags, filter document list");
  await page.screenshot({
    path: "doc/assets/personal-knowledge-assistant.png",
    fullPage: true,
  });
  const selected = await page.evaluate(
    async () =>
      (
        await (
          await fetch("/api/documents?tag=" + encodeURIComponent("项目"))
        ).json()
      ).items[0],
  );
  await page.evaluate(async (row) => {
    await fetch("/api/documents/" + row.id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_version: row.version,
        name: "服务端的新标题",
        content: "项目资料 <script>window.injected=1</script>",
      }),
    });
  }, selected);
  await page.click('[data-tab="search"]');
  await page.select("#mode", "lexical");
  await page.$eval("#question", (n) => (n.value = "项目资料"));
  const local = page.waitForResponse((r) => r.url().endsWith("/api/ask"));
  await page.click("#send");
  await local;
  await page.waitForFunction(() =>
    document
      .querySelector("#messages")
      .textContent.includes("<script>window.injected=1</script>"),
  );
  assert.equal(await page.evaluate(() => window.injected), undefined);
  await page.click('[data-tab="library"]');
  await page.type("#note-content", "我的未保存草稿");
  await page.click("#save-note");
  await page.waitForFunction(() =>
    document.querySelector("#editor-status").textContent.includes("保存冲突"),
  );
  assert.match(
    await page.$eval("#note-content", (n) => n.value),
    /我的未保存草稿/,
  );
  assert.equal(await page.evaluate(() => window.injected), undefined);
  report.checks.push(
    "Concurrent edit preserves draft; document markup is inert",
  );
  await page.reload();
  await page.waitForFunction(() =>
    document.querySelector("#status").textContent.includes("资料库已打开"),
  );
  await page.setViewport({ width: 390, height: 844 });
  for (const tab of ["library", "search", "graph"]) {
    await page.click(`[data-tab="${tab}"]`);
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      tab + " horizontal overflow",
    );
  }
  await page.click('[data-tab="library"]');
  await page.screenshot({
    path: "doc/assets/personal-knowledge-mobile.png",
    fullPage: true,
  });
  assert.deepEqual(errors, []);
  report.checks.push(
    "390px library/search/graph have no horizontal overflow or page errors",
  );
  report.status = "PASS";
  await writeFile(
    "doc/verification/ui.json",
    JSON.stringify(report, null, 2) + "\n",
  );
  process.stdout.write(JSON.stringify(report));
} finally {
  await browser.close();
}

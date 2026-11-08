import { spawn, execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineTool } from "@deepseek-ai/dsh-tools";

export const name = "personal-knowledge-tools";
export const inject = ["tools"];
export const TOOL_TIMEOUT_MS = 75_000;
export const TOOL_OUTPUT_MAX_BYTES = 100_000;
const api = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../doc/openapi.json", import.meta.url)),
    "utf8",
  ),
);

// DSH's schema DSL supports a checked subset; Python independently enforces bounds.
function sdkSchema(input) {
  if (input.$ref)
    return sdkSchema(api.components.schemas[input.$ref.split("/").at(-1)]);
  if (input.anyOf) return { oneOf: input.anyOf.map(sdkSchema) };
  const out = { type: input.type || "json" };
  if (input.enum) out.enum = input.enum;
  if (input.type === "array") out.items = sdkSchema(input.items || {});
  if (input.type === "object") {
    out.additionalProperties = input.additionalProperties !== false;
    out.properties = Object.fromEntries(
      Object.entries(input.properties || {}).map(([key, value]) => [
        key,
        {
          ...sdkSchema(value),
          ...(input.required?.includes(key) ? { required: true } : {}),
        },
      ]),
    );
  }
  return out;
}

// The deadline argument is for trusted callers/tests, never model tool parameters.
export function invokeBridge(
  config,
  operation,
  args,
  signal,
  timeoutMs = TOOL_TIMEOUT_MS,
) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      config.python,
      [config.bridge, config.database, operation],
      {
        stdio: ["pipe", "pipe", "pipe"],
        signal,
        detached: process.platform !== "win32",
      },
    );
    let bytes = 0;
    const chunks = [];
    let hardTimer;
    let stopped = false;
    const forceStop = () => {
      if (process.platform === "win32") {
        try {
          execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
            stdio: "ignore",
          });
        } catch {}
        return;
      }
      // Query subprocesses can create their own sessions; a process group alone
      // is insufficient. Collect descendants before terminating the bridge.
      try {
        const children = new Map();
        for (const row of execFileSync("ps", ["-eo", "pid=,ppid="], {
          encoding: "utf8",
        })
          .trim()
          .split("\n")) {
          const [pid, parent] = row.trim().split(/\s+/).map(Number);
          children.set(parent, [...(children.get(parent) || []), pid]);
        }
        const visit = (pid) => {
          for (const descendant of children.get(pid) || []) visit(descendant);
          try {
            process.kill(pid, "SIGKILL");
          } catch {}
        };
        visit(child.pid);
      } catch {
        child.kill("SIGKILL");
      }
    };
    const terminate = () => {
      stopped = true;
      child.kill("SIGTERM");
      hardTimer ??= setTimeout(forceStop, 1000);
    };
    const timer = setTimeout(terminate, timeoutMs);
    signal?.addEventListener("abort", terminate, { once: true });
    child.stdout.on("data", (part) => {
      bytes += part.length;
      if (bytes > TOOL_OUTPUT_MAX_BYTES) terminate();
      else chunks.push(part);
    });
    child.stderr.resume();
    child.on("error", reject);
    child.on("close", (code) => {
      clearTimeout(timer);
      clearTimeout(hardTimer);
      signal?.removeEventListener("abort", terminate);
      if (stopped || code !== 0 || bytes > TOOL_OUTPUT_MAX_BYTES)
        return reject(
          new Error("知识库工具执行失败；请检查资料版本或服务配置"),
        );
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch {
        reject(new Error("工具返回无效JSON"));
      }
    });
    child.stdin.on("error", () => {});
    child.stdin.end(JSON.stringify({ args, context: config.context || null }));
  });
}

export function apply(ctx, config) {
  const descriptions = {
    kb_search:
      "检索本地原文：关键词、向量、图谱，多路状态与失败明确返回；只用hits原文回答，可换词。",
    kb_list_documents: "列出文档名和片段数，帮助找词；文件名不是事实证据。",
    kb_graph_schema:
      "探索问题附近真实存在的图谱实体、关系、类型和属性；多跳查询前先检查。",
    kb_graph_query:
      "执行1至3跳只读计划，anchors必须实际存在；每跳relation/target_type空字符串表示不限。empty时核对schema并修正，不编造路径；hits是原文。",
  };
  let toolCalls = 0;
  for (const [operation, contract] of Object.entries(api["x-dsh-tools"])) {
    const parameters = sdkSchema(contract.input).properties;
    ctx.tools.register(
      defineTool({
        name: operation,
        description: descriptions[operation],
        parameters,
        output: {
          schema: sdkSchema(contract.output),
          render: (_args, value) => [
            { type: "text", text: JSON.stringify(value) },
          ],
        },
        execute: (args, exec) => {
          if (++toolCalls > 8) throw new Error("已达到8次工具调用预算");
          return invokeBridge(config, operation, args, exec.signal);
        },
      }),
    );
  }
  ctx.on("agent/created", ({ agent }) =>
    agent.ctx.tools.restrict({ allow: Object.keys(descriptions) }),
  );
  const steps = new WeakMap();
  ctx.on("agent/pre-step", ({ agent }, next) => {
    const count = (steps.get(agent) ?? 0) + 1;
    steps.set(agent, count);
    if (count > config.maxSteps) throw new Error("已达到本轮模型请求上限");
    return next();
  });
}

import { spawn } from 'node:child_process';
import { defineTool } from '@deepseek-ai/dsh-tools';

export const name = 'personal-knowledge-tools';
export const inject = ['tools'];

function invoke(config, operation, args, signal) {
  return new Promise((resolve, reject) => {
    const child = spawn(config.python, [config.bridge, config.database, operation], {
      stdio: ['pipe', 'pipe', 'pipe'], signal,
    });
    const chunks = [];
    let bytes = 0;
    const timer = setTimeout(() => child.kill(), 10_000);
    child.stdout.on('data', part => {
      bytes += part.length;
      if (bytes > 100_000) child.kill();
      else chunks.push(part);
    });
    child.stderr.resume();
    child.on('error', reject);
    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0 || bytes > 100_000) return reject(new Error('知识库工具执行失败'));
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
      catch { reject(new Error('知识库工具返回格式不正确')); }
    });
    child.stdin.on('error', () => {});
    child.stdin.end(JSON.stringify(args));
  });
}

export function apply(ctx, config) {
  const field = type => ({ type, required: true });
  const source = {
    type: 'object', additionalProperties: false,
    properties: {
      chunk_id: field('integer'), document_id: field('integer'), ordinal: field('integer'),
      name: field('string'), content: field('string'), source_id: field('string'),
    },
  };
  for (const definition of [
    {
      name: 'kb_search',
      description: '用中文关键词查询本地资料，最多返回四个原文片段及可引用的 source_id。无结果时可换词重查，不得编造。',
      parameters: { query: { type: 'string', required: true, description: '1—1000 字检索关键词' } },
      items: source,
    },
    {
      name: 'kb_list_documents',
      description: '列出本地知识库中前 100 份文档的名称和片段数，帮助选择检索关键词。文件名不能作为事实证据。',
      parameters: {},
      items: { type: 'object', additionalProperties: false,
        properties: { name: field('string'), chunk_count: field('integer') } },
    },
  ]) {
    ctx.tools.register(defineTool({
      name: definition.name, description: definition.description, parameters: definition.parameters,
      output: {
        schema: { type: 'array', items: definition.items },
        render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
      },
      execute: (args, exec) => invoke(config, definition.name, args, exec.signal),
    }));
  }
  // Restrict the actual registry as well as removing the stock tool producers.
  ctx.on('agent/created', ({ agent }) => {
    agent.ctx.tools.restrict({ allow: ['kb_search', 'kb_list_documents'] });
  });
  const steps = new WeakMap();
  ctx.on('agent/pre-step', ({ agent }, next) => {
    const count = (steps.get(agent) ?? 0) + 1;
    steps.set(agent, count);
    if (count > config.maxSteps) throw new Error('已达到本次问答的模型请求步数上限');
    return next();
  });
}

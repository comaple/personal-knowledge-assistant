# Personal Knowledge Assistant｜个人资料问答助手

本地、单人、教学用途。只用 Python 标准库，无须 pip 安装依赖。

功能：导入 UTF-8 TXT/Markdown；SQLite 保存原文、片段、会话和消息；FTS5 关键词检索；展开来源原文；可选调用模型生成带出处回答；删除文档和会话。

## 1. 启动

安装 Python 3.10 或更新版本（推荐 python.org 的当前稳定版，内置 SQLite 需支持 FTS5）。克隆或下载本仓库后，进入包含 app.py 的仓库根目录。

macOS / Linux：

```bash
python3 --version
cd personal-knowledge-assistant
python3 app.py
```

Windows（在仓库文件夹打开终端）：

```powershell
py --version
py app.py
```

若目录不在当前文件夹中，用 `cd` 切换到实际解压路径。浏览器打开：

http://127.0.0.1:8501

若端口被占用：`python3 app.py --port 8502`，并访问终端打印的新地址。

## 2. 不用 API Key，先验证本地链路

1. 点击“导入示例资料”。两份摄影课程文件均为虚构教学资料。
2. 提问“摄影课退款需要提前多久申请？”
3. 默认仅检索，展开来源，应看到“至少 72 小时”。此时没有模型生成答案，也不会请求模型服务。
4. 提问“量子隧穿的实验数据是多少？”，应提示未检索到资料。
5. 重复导入同一资料，文档数不应增加。
6. 按 Ctrl+C 停止服务，再用相同命令启动。文档、会话、消息仍在。
7. 删除课程说明，再查“退款”，该文档不应参与新检索。历史消息中的证据快照仍保留；若也要清除这些快照，删除对应会话。

## 3. 再接模型（可选，会产生提供商的 API 费用）

把 `.env.example` 复制为 `.env`，使用文本编辑器填写自己的 Key，然后重启应用。

macOS / Linux：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

默认配置（模型名与调用方式依据 2026-11-07 官方文档；若后续变动，以提供商文档为准）：

```dotenv
LLM_API_KEY=填入你自己的Key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
```

默认 DeepSeek 请求显式关闭思考模式，用于短篇资料问答，避免很小的输出预算先消耗在推理过程。也可改为支持 `/chat/completions` 的其他服务地址与对应模型名；其他服务的特殊参数需按其文档调整。

在页面勾选“使用模型生成带出处的回答”，再提交问题。应用只发送本次问题和最多 4 个命中片段，不发送整份资料库或历史聊天；Key 保留在服务进程。没有命中片段时不会调用模型。

注意：引用编号有效，只证明编号存在，不能自动证明回答确实被原文支持。检查展开的原文。外部模型调用未在交付时使用真实付费 Key 验证；已通过本地模拟服务检查请求格式、正常回复、无效出处和 HTTP 错误处理。

## 4. 文件与层次

- `index.html`：浏览器界面，文件上传、聊天和来源展示。
- `app.py`：HTTP 路由，导入、提问、会话管理与流程编排。
- `store.py`：SQLite 事务、持久化、删除与查询。
- `knowledge.py`：文件校验、切块、中文双字切分、FTS5 检索。
- `model_client.py`：模型接口调用、超时与出处编号校验。
- `sample_docs/`：两份虚构练习资料。
- `test_project.py`：离线验收测试。
- `inspect_db.py`：查看本地数据库的表记录数。
- `docs/BUILD_PROMPT.txt`：交给 AI 编码助手的完整项目任务书。
- `.env.example`：模型配置模板。
- 运行后生成 `data/knowledge.db`：默认数据库，停止应用后可备份整个 data 目录。

## 5. 四张业务表 + 一个检索索引

| 表 | 保存什么 |
| --- | --- |
| documents | 文件名、原文、内容摘要值、导入时间 |
| chunks | 文档 ID、片段序号、片段正文 |
| sessions | 会话 ID、标题、创建时间 |
| messages | 问题、回答、来源快照、运行状态和时间 |
| chunk_search | FTS5 虚拟表，保存片段的检索词，以片段 ID 对齐 |

数据库层和知识库层是职责分层，第一版物理上共用同一个 SQLite 文件。入库事务同时保存文档、片段和索引；发生异常时回滚。删除文档同时移除片段和检索索引。

切块规则：按字符每 800 字一个片段，相邻重叠 120 字。中文连续文字按相邻双字形成检索词，英文按单词处理；使用 OR 查询和 SQLite BM25 排序，返回前 4 个候选片段。

这是关键词检索，不是向量或语义检索。相同字词可能误匹配，同义表达可能漏检。每次仅根据当前问题检索；历史聊天持久化用于回看，不代表模型拥有跨轮推理记忆。扩展时再考虑语义改写、向量索引和上下文选择。

## 6. 验收命令

在仓库根目录运行：

```bash
python3 -m unittest -v test_project.py
```

Windows 把 `python3` 换成 `py`。测试不连接外部模型，不读取你的 .env，也不修改正式数据库；数据库位于临时目录。

## 7. 教学边界

只监听 127.0.0.1，不作为对外生产服务部署。第一版无账号、租户隔离或扫描 PDF/OCR，不处理网页抓取。允许单个文件最大 1 MB，问题最多 1000 字；一次最多 4 个片段；模型请求超时 45 秒，输出上限 900 tokens，不自动重试。这些是项目设计值，不是最优参数或费用保证。

## 官方参考

- https://docs.python.org/3/library/sqlite3.html
- https://www.sqlite.org/fts5.html
- https://api-docs.deepseek.com/
- https://api-docs.deepseek.com/api/create-chat-completion/


## 从零动手的顺序

1. 先运行项目，导入示例并查看来源。
2. 打开 `store.py`，对照四张业务表理解数据如何保存。
3. 用自己的小文档练习切块和检索，观察不同问法返回的候选片段。
4. 重启服务验证持久化，再删除文档验证索引同步。
5. 最后再配置模型；先验证检索材料，再核对生成答案。
6. 想从空目录重做，可把 `docs/BUILD_PROMPT.txt` 交给编码助手，逐层实现并运行验收测试。

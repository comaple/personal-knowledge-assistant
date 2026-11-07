# 从零搭建：DSH + SQLite 个人资料助手

**8 个步骤 · 阅读约 6 分钟**

项目：https://github.com/comaple/personal-knowledge-assistant

目标：导入笔记，问出有原文出处的答案；重启后资料和消息仍在；还可以回看智能体实际用了哪些检索词。

## 01｜确定一个能验收的需求

用两份虚构摄影课程资料练习。输入“摄影课退款需要提前多久申请？”，原文应能支持“至少提前 72 小时”。资料没有的事情，不能靠模型猜。

> **先让资料可保存、可检索，再让模型使用工具。**

## 02｜看清分层

![DSH 实际架构](assets/dsh-architecture.svg)

页面负责交互，Python 接口负责业务，DSH 负责模型与工具循环，知识库负责切块检索，SQLite 负责长期保存。

数据库与知识库在逻辑上分工，本版共用一个 SQLite 文件。无需部署外部数据库或向量服务。

## 03｜安装并启动

需要 Python 3.10+；完整 DSH 模式还需 Node.js ≥24.9.0。

```bash
git clone https://github.com/comaple/personal-knowledge-assistant.git
cd personal-knowledge-assistant
npm ci
python3 app.py
```

Windows 将 `python3` 换为 `py`。打开 http://127.0.0.1:8501 。默认仅检索，不使用 API Key；只练本地检索时也可跳过 npm 安装。

## 04｜导入并理解持久化

点击“导入示例资料”，问退款问题。按 Ctrl+C 停止后再启动，文档与聊天仍在。

`data/knowledge.db` 保存五张业务表：documents、chunks、sessions、messages、harness_runs；另有 chunk_search 全文索引。执行 `python3 inspect_db.py` 可查看记录数。

文档导入同时保存原文、片段与索引，发生异常一起回滚。问答与已返回的 DSH 事件轨迹也一起入库。

## 05｜理解小型知识库

文件限定 UTF-8 TXT/Markdown，单份不超过 1 MB，按内容去重。每段最多 800 字，重叠 120 字。

中文用相邻双字、英文用单词建 FTS5 索引；OR 匹配、BM25 排序，每次最多四个候选片段。

**这是关键词检索。**同义表达可能漏检，相同字词也可能误匹配。试着换两种问法，先比较原文片段，再评价答案。

## 06｜接上 DSH 工具循环

复制 `.env.example` 为 `.env`，用编辑器填写自己的 Key，再重启：

```dotenv
LLM_API_KEY=你的DeepSeekKey
DSH_MODEL=deepseek-flash
DSH_BASE_URL=https://api.deepseek.com/anthropic
```

勾选“使用 DSH 智能体检索并回答”。模型可用 `kb_search` 检索，可用 `kb_list_documents` 查看文档名；DSH 把结果交回模型，支持在同一轮换词重查。

**本版使用 DSH 官方 Messages 适配器。**不是旧的 `/chat/completions` 客户端。最多五次模型请求，每次最多 1200 tokens，整轮 150 秒边界；无命中时也可能已经调用模型。

调用会把本次问题、工具返回的文档名和片段发送给提供商，先用公开或虚构资料练习。引用编号有效仍不等于事实正确。

完整接入代码、profile 补丁和实际运行记录见：[DSH 实战教程](dsh-harness-tutorial.md)。

## 07｜查看过程与删除数据

页面可以展开“智能体检索过程”，查看工具名和检索词。完整 SDK Session 事件保存在 harness_runs。

每个问题是新的 DSH 会话；旧聊天用于保存和展示，不自动带入下一轮。模型在同一轮内部会看到先前工具结果。

删除文档时，原文、片段、索引一起删除，历史消息和 DSH 轨迹中的证据快照仍在。删除对应会话，会同时删除消息、快照和轨迹。

## 08｜验收后再扩展

```bash
python3 -m unittest -v test_project.py
npm run test:harness
```

共 9 项 Python 测试与 4 项真实 DSH 集成测试。模型端使用本地模拟服务，没有使用真实付费 Key；测到了运行时和工具链路，尚未评价真实模型质量。

亲手检查：同内容不重复导入、退款有出处、无资料不编答案、重启可恢复、删文档不再命中、异常不伪装成功。

想从空目录重做，可把 [BUILD_PROMPT.txt](BUILD_PROMPT.txt) 交给编码助手。下一步再按实际需要增加 PDF、语义检索或多轮上下文，每增加一项功能就增加一条验收条件。

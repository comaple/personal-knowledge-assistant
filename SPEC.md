# 个人知识管理助手｜技术规格（Spec）

| 项目 | 内容 |
| --- | --- |
| 产品名称 | 个人知识管理助手 |
| 英文名称 | Personal Knowledge Assistant |
| 产品定位 | 个人知识管理系统 |
| 版本 / 日期 | 0.2 / 2026-11-07 |
| 状态 | 待评审的 SDD 技术草案；不是实现完成报告 |
| 上游需求 | [PRD.md](PRD.md)，需求编号 FR-01—FR-15、NFR-01—NFR-06；FR-13—FR-15 为新增候选需求 |
| 已发布参照 | `4a571afbf426f75e9ff3bdf8a9b2970e2d3ad1da` |
| 当前代码状态 | DSH/FTS5 已有发布基线；图谱和多路召回为本地未提交原型 |
| 本轮工作边界 | 仅文档；不继续实现、运行功能验收或提交未完成原型 |

本文中的“必须”是建议冻结的验收约束，“候选”需要评审。原型已有同名接口不意味着完全符合本文；差距见 §13。PRD 定义产品目标，本文件定义满足目标的行为、边界、数据和验证方式；发生冲突时先修订文档，不能自行以代码覆盖需求。

系统服务于个人知识的持续积累、组织、关联、查找和使用。以下技术选择按这些功能、维护成本和使用体验评估；问答只是系统的一部分。

## 01｜决策与技术选型

### 1.1 决策登记

| 决策 | 状态 | 内容与依据 |
| --- | --- | --- |
| ADR-01 | 用户已确认 | DSH 是 Harness，负责 Agent 工具循环；Python 负责业务和持久化 |
| ADR-02 | 用户已确认 | 知识库包含知识图谱，RAG 必须多路召回 |
| ADR-03 | 候选 | 本地单人 Web + Python + SQLite；管理个人资料和笔记，先验证小规模可靠性 |
| ADR-04 | 候选 | 以 LightRAG 构建实体关系及向量，以 FTS5 提供独立词法召回 |
| ADR-05 | 候选 | 复杂问题使用图谱探索、受限计划执行、结果检查与预算内修正；按跨文档问题能否检索到所需证据验收 |
| ADR-06 | 候选 | 三路候选按通道轮流收集，基于原文做内容重排；以证据覆盖、延时和成本决定后续优化 |
| ADR-07 | 候选 | 不同代索引不可变，SQLite 仅原子切换已完成代的指针；避免将多个文件数据库描述成跨库事务 |
| ADR-08 | 新增候选 | 原始资料和用户笔记统一管理；标签负责用户组织，自动实体关系负责知识关联，保留两者不同含义 |

首版交互及 FR-13—FR-15 范围确定后再拆分实现；不能用已有原型缺少某能力作为删除产品需求的依据。

### 1.2 产品能力与技术对应

| 产品能力 | 候选技术 | 验收关注 |
| --- | --- | --- |
| 保存、编辑和组织资料 | SQLite 原文/笔记、标签关系、事务 | 保存可靠，筛选准确，失败不破坏已有资料 |
| 关键词和语义查找 | FTS5、文本向量 | 能找到有用原文，缺服务时可解释降级 |
| 发现和查询知识关联 | LightRAG 实体关系图谱、只读多跳计划 | 关系可回到原文，更新后不使用旧内容 |
| 基于资料回答 | DSH 工具循环、来源校验 | 实际使用资料，引用可核对，执行有边界 |
| 知识可迁移 | Markdown 原文与 JSON 元数据导出 | 导出可读、完整，不依赖模型服务 |

组件仅承担实现职责，最终以本系统的资料管理、查找和使用效果验收。

### 1.3 候选运行环境

- Python 推荐 3.12，最低 3.10，SQLite 必须支持 FTS5。
- Node.js ≥24.9.0；DSH 及其 sdk-client/tools 暂固定 `0.2.2-rc.1`。
- `lightrag-hku==1.5.7`；NetworkX、NanoVectorDB 使用其对应后端。正式发布需记录可复现安装结果，当前 Python 传递依赖尚未完整锁定。
- 最小本地关键词模式仅需 Python；DSH 模式增加 Node 依赖；图谱模式增加 LightRAG 与模型/Embedding 服务。
- 现有配置以 DeepSeek Messages、Chat Completions 和 Ollama BGE-M3 为示例；真实可用性、模型别名、资源与费用待验证，不保证任意服务兼容。

## 02｜架构与责任边界

```text
浏览器：资料列表 / 笔记编辑 / 标签 / 导出 / 搜索 / 问答 / 图谱
  │ HTTP，localhost，JSON
  ▼
Python 应用 app.py
  ├─ Store / knowledge ── SQLite：原文/笔记、标签、片段、FTS5、会话、消息、轨迹、索引指针
  ├─ 人工资料维护 / 导出（候选新增，不开放给模型工具）
  ├─ IndexJobs ── 独立构建进程 ── 抽取模型 + Embedding
  │                              └─ LightRAG 代目录：实体关系、向量、来源映射
  ├─ 仅检索 ── retrieval：BM25 + text vector + graph → 去重 / 内容重排
  └─ 生成 ── model_client ── Node / DSH SDK
                              ├─ 模型 Messages API
                              └─ 白名单工具 ── Python bridge ── 检索 / 图谱只读计划
```

SQLite 是原文及应用状态的事实源。图谱、向量和 FTS 索引均为派生数据；生成模型不负责直接修改数据库。知识图谱中的实体关系是模型抽取结果，不是经核验的事实库。

| 模块 | 职责 | 禁止承担的职责 |
| --- | --- | --- |
| `app.py` / `index.html` | 资料列表、笔记编辑、标签、导出入口、页面状态、HTTP | 自写替代 DSH 的 Agent 循环；暴露任意本地文件 |
| `store.py` / `knowledge.py` | 原文、切块、去重、事务、FTS、版本 | 以模型输出直接拼接 SQL |
| `index_jobs.py` / `rag_backend.py` | 后台构建、配置指纹、快照与真实后端 | 构建未完成即发布；静默沿用不匹配索引 |
| `retrieval.py` | 三路检索、去重、内容重排与状态 | 伪造融合分数或隐瞒降级 |
| `graph_tools.py` | 真实 schema 读取、有界计划执行、路径回指 | 任意 Cypher/代码执行；宣称路径已证明答案 |
| `harness/` / `model_client.py` | DSH 生命周期、工具注册、预算、回答结构验收 | 将自写 HTTP 循环称为 DSH；以合法引用代替语义核验 |

## 03｜数据与持久化

### 3.1 逻辑数据模型

| 数据 | 主要字段 / 关系 | 不变量 |
| --- | --- | --- |
| documents | id、name、content、digest、created_at | digest 唯一；内容 SHA-256 去重；文件名不作为证据 |
| documents 扩展（待实现） | kind：imported/note；updated_at | 旧资料迁移为 imported；笔记和导入资料共用来源、切块和检索规则 |
| tags / document_tags（待实现） | tags(id, name 唯一)；document_tags(document_id, tag_id 联合唯一) | 显式标签与自动抽取实体分开；删除资料时级联移除关联 |
| chunks | id、document_id、ordinal、content | document_id 外键级联；同文档 ordinal 唯一 |
| chunk_search | FTS5 tokens；rowid 对应 chunk_id | 与 chunks 在同一导入/删除事务内更新 |
| sessions | UUID id、title、created_at | 应用会话与 DSH 临时会话不是同一 ID |
| messages | id、session_id、role、content、sources_json、status、created_at | 来源保存当时原文快照；删除应用会话级联删除 |
| harness_runs | id、session_id、harness_session_id、finish_reason、events_json、created_at | 保存已收到的运行轨迹；首版按会话顺序读取，不声称逐消息唯一关联 |
| rag_state | 单例 id=1、revision、generation、built_revision、profile、updated_at | 原文变化 revision 增长；只有匹配 revision/profile 的代可读 |

原文、片段和 FTS 更新必须在单个 SQLite 事务中提交；每轮用户消息、助手消息、来源和已返回轨迹也必须同事务写入。SQLite 启用外键、WAL 与有限锁等待，不提供多用户并发承诺。

### 3.2 来源结构

候选兼容结构如下；示例数值仅说明类型：

```json
{
  "chunk_id": 12, "document_id": 3, "ordinal": 1,
  "name": "教务服务说明.md", "content": "需在开课前至少72小时提交。",
  "source_id": "S12", "channels": ["bm25", "graph"],
  "channel_ranks": "{\"bm25\":2,\"graph\":1}",
  "rank_position": 1, "graph_paths": ["摄影课 — 青松教务 — 退课申请"]
}
```

- `channel_ranks` 暂保留为 JSON 字符串以兼容现有工具 schema；这是原始通道名次，不是置信度。
- `source_id` 用于本轮引用，必须由服务端产生；同轮多个工具命中同片段时合并通道和路径。
- 历史答案必须打开保存的原文快照，不能因当前数据库 ID 被复用而显示新文档。
- 目标要求：文档/片段 ID 不得在删除后与新资料混淆。现有 `INTEGER PRIMARY KEY` 可能复用 ID，需要在实施阶段确定永久 ID 或版本化身份方案；本轮不改数据库。

### 3.3 派生索引与生命周期

目录候选为 `data/knowledge-rag-index/<generation>/`，代名由服务端产生。每代包含 LightRAG 文件、`graph.json`、`manifest.json` 和来源映射。

manifest 至少记录：格式版本、corpus revision、配置 profile、片段/实体/关系数、LightRAG chunk ID→SQLite chunk ID 映射。图节点含 id/type/description/chunk_ids；边含 source/target/description/relation/chunk_ids。

配置 profile 包括后端版本、抽取模型及地址、Embedding 模型/地址/维度和处理流程版本，不含 Key。改 Key 本身不应使索引失效；同一模型别名背后换权重无法可靠检测，需要显式重建并在评估中记录。

删除资料意味着停止当前检索使用，不等于物理擦除全部副本。历史来源、轨迹、旧代文件可能仍有原文；首版教程提供停服后清理派生索引的方法。物理彻底清除、备份和日志保留策略不在首版承诺内。

### 3.4 笔记、编辑和标签（新增候选，未实现）

- 笔记为用户在系统中创建的纯文本/Markdown，使用 name 作为显示标题，持久化 kind=note；导入资料的 kind=imported。两者均支持人工修改标题和正文，不提供富文本编辑及完整版本历史。
- 新建笔记遵循正文大小和非空规则；标题去掉首尾空白后为 1—120 字符。标题用于显示，导出时另外生成安全文件名。
- 编辑已有资料保留 document_id；正文变化时，在同一事务内替换片段与 FTS、更新 digest/updated_at、增加 revision；旧片段身份不能与新片段混淆。正文与另一份资料重复时返回冲突并保留原状态。
- 仅改标题或标签不触发图谱重建；当前来源展示读最新标题，历史答案继续展示当时快照。正文未变时不增加语料 revision。
- 标签首版为去首尾空白后的非空字符串，建议单个 ≤40 字符、每份资料 ≤20 个，大小写敏感、精确筛选；不支持嵌套。标签名全局唯一，资料可有多个标签。
- 替换标签集合是单个事务；无效标签整次拒绝，不留下半组关联。删除资料移除关联，不影响其他资料的同名标签。
- 标签筛选用于资料列表；首版不作为问答检索范围或访问权限，以免界面筛选与回答范围含混。自动打标签、文件夹和复杂分类后续另立需求。

### 3.5 知识导出（新增候选，未实现）

用户可导出全部当前资料；首版不设计部分导出或自动导入恢复。服务端以一个 SQLite 读事务读取原文与标签快照，生成 ZIP：

- `documents/<document_id>.md`：UTF-8 正文，文件名由服务端生成，保留内容本身。
- `metadata.json`：schema_version、exported_at、revision 及每份资料的 id、name、kind、tags、digest、created_at、updated_at、导出文件相对路径。

导出不调用模型，不写业务数据，不含 Key、会话、工具轨迹和派生索引。ZIP 采用流式或临时文件输出，失败清理临时资源。知识导出用于可读与迁移；完整应用备份另按 §10 进行，不能把仅原文导出宣称为完整备份。

## 04｜导入、构建与一致性协议

### 4.1 文档导入

顺序：校验 UTF-8 文本、扩展名与字节大小 → 计算 digest → 去重 → 按字符切块 → 同事务写入文档/片段/FTS，并递增 revision。新增笔记和正文编辑共用规范化、切块及派生索引失效规则。

- 每份文档 ≤1,048,576 字节（界面简称 1 MB），非空，无 NUL，类型 TXT/MD。先校验上传大小，再将 CRLF 归一为 LF、去除首尾空白；digest 对归一后的内容计算。文件名仅保留最后一段，最长 120 字符，不作为磁盘路径使用。
- 候选切块：800 字符、120 字符重叠；序号从 1 开始，尾段不得丢失。
- 中文双字及英文单词做 FTS 分词；用户查询经受控转义，不直接当 FTS 表达式或 SQL。
- 重复内容不新增文档、不递增 revision；真正删除或新增才递增。

### 4.2 构建协议

1. 单个应用进程只允许一个后台构建任务，先验证依赖、Key、资料和规模。
2. 在一致的 SQLite 读事务中获得 revision=R 与对应全部片段。
3. 创建新的独立 generation=G，逐片段送入 LightRAG，使用可回指 SQLite 的输入标识。
4. 校验所有文档处理完成、向量维度正确、来源映射和图文件可读；关闭/刷写后端存储，写完 manifest。
5. SQLite 执行条件发布：仅当当前 revision 仍为 R 时写入 G、R、profile。
6. 发布失败丢弃未发布代并报“构建期间资料变化”；任一步骤失败都不能切换指针。

这不是跨数据库原子事务：文件先完成，SQLite 指针后提交。硬终止可能留下未发布目录；应用不得自动将这些目录当作有效索引。

### 4.3 状态与可读条件

索引状态与构建任务状态分开返回，不能混成一个“成功”字段。

| 索引状态 | 意义 / 使用方式 |
| --- | --- |
| disabled | 配置关闭；仅本地关键词 |
| config_error | 配置非法；给出可操作说明 |
| not_built | 没有发布过有效代 |
| missing | 指针对应的文件缺失/不可读，需重建或修复 |
| unavailable | 有索引但当前缺依赖/必要服务配置 |
| stale | revision 或 profile 不匹配；禁止读取旧图谱与向量 |
| ready | 结构和版本满足读取条件；不等于外部 API 已健康或内容准确 |

任务状态：`idle → running → completed | error`。重启后首版不自动恢复任务；索引有效性从持久化元数据判断，不能由内存任务状态推导。

### 4.4 查询版本一致性

查询开始固定 revision 和 generation。图谱、向量读取不可变代；候选图谱查询可将小索引复制到临时目录，以免后端查询缓存写入已发布文件。

返回/落库前再次验证 revision；若资料变化，丢弃旧证据，至多重新执行一次当前检索，仍有竞争则返回可重试错误。问答生成期间也可能发生删除，最终来源不能悄悄继续当作当前证据使用。

**目标边界：**保证一个响应/答案属于单一语料版本。现有原型仅有部分前后检查，HTTP 回答、最终落库及跨工具查询尚需对齐；这是待实现差距，不宣称已解决并发竞态。

## 05｜多路召回与重排契约

### 5.1 路由与候选收集

| 路由 | 实际能力 | 候选上限 |
| --- | --- | --- |
| bm25 | 当前 SQLite FTS5 关键词检索 | 12 |
| vector | LightRAG naive 文本向量检索 | 12 |
| graph | LightRAG hybrid 实体/关系相关片段检索 | 12 |

索引 ready 时执行三路；否则只执行当前 BM25 并返回缺失能力原因。按通道轮流收集，依据来源身份去重，合并通道名次，池上限 24。该顺序用于控制预算，不称为相关性融合分数。

有至少两路可用且有至少两个候选时，可使用候选的零样本原文重排：输入问题和受控原文，模型只返回完整候选 ID 排列及说明。必须校验集合相等、无新增/缺失/重复；未知 ID、截断或非法响应均视为失败。

成功后输出前 4 个片段；重排失败回退候选收集顺序并说明未完成重排。不用伪造的分数填充响应，不把模型自报分数当作概率。

### 5.2 检索响应

```json
{
  "hits": [],
  "retrieval": {
    "mode": "lexical_fallback",
    "message": "图谱尚未构建，当前仅关键词检索。",
    "ranking": {"method": "candidate_pool", "status": "not_run", "reason": "未启用内容重排"},
    "routes": [
      {"name": "bm25", "status": "ok", "candidates": 0},
      {"name": "vector", "status": "not_built", "candidates": 0},
      {"name": "graph", "status": "not_built", "candidates": 0}
    ]
  }
}
```

模式：`hybrid` 表示三路执行成功，`partial` 表示部分派生通道可用，`lexical_fallback` 表示仅词法可用。通道成功可以返回零候选；零候选不等于执行失败。通道状态使用 `ok/error` 或具体不可用原因，数量为非负整数。

重排 `method` 为 `llm_evidence` 或 `candidate_pool`，`status` 为 `ok/error/not_run`。原型接口尚未携带统一语料版本字段；为满足 §4.4，实施时需为返回结果增加 revision/generation 元数据并同步 schema。

### 5.3 降级规则

- 一个通道失败：可使用其余通道，但必须在工具、接口和页面显示降级。
- 所有已执行通道成功但无候选：显示“未检索到”，不是证明知识库不存在答案。
- 服务失败且无候选：显示“检索未完成/服务不可用”，不得落为普通 `no_evidence`。
- 无最终答案生成的检索，也可能调用 Embedding、检索词提取和重排；只有无 Key 的本地词法路径承诺不调用模型。

## 06｜图谱探索与只读计划

工具先读取实际图谱接口，返回问题相关锚点、真实实体类型、关系描述和属性键；不能要求模型预先猜固定本体。

候选计划格式：

```json
{
  "anchors": ["摄影课"],
  "hops": [
    {"relation": "", "target_type": ""},
    {"relation": "", "target_type": ""}
  ]
}
```

示例是无关系过滤的两跳计划，不代表图谱必然存在路径。锚点和过滤条件应来自实际 schema 结果。

- 锚点 1—3 个；跳数 1—3；单字符串 ≤160 字符。
- 每跳仅允许 `relation`、`target_type`；空串表示不限。关系按实际描述的大小写无关子串匹配，类型按大小写无关完全匹配。
- 图遍历候选先按无向边实现，不赋予其因果/动作方向；原型不支持方向表达、任意条件、聚合或 Cypher。
- 不访问重复节点，前沿最多 30 个，返回最多 6 条路径和 8 个原文片段。
- 必须走完整计划才能返回 completed；部分路径不得冒充成功。
- 无完整路径返回 `empty` 和诊断，允许 DSH 在全局预算内修正；索引失效返回对应状态。
- witness 为 `nodes[]`、`relations[]`、`chunk_ids[]`。片段回指可检查路径来源，但答案事实仍要核对原文。

局部 schema 候选上限为 8 个锚点、40 个类型、20 个邻近关系描述。受限查询提高可控性，但不提供一般图查询完备性。

## 07｜DSH Harness 与答案验收

### 7.1 工具契约

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| kb_search | query：1—1000 字符 | §5 检索包，来源采用 §3.2 结构 |
| kb_list_documents | 空对象 | 最多 100 项 name/chunk_count；仅辅助找词 |
| kb_graph_schema | query：1—1000 字符 | status/message、anchors、entity_types、relation_descriptors、node_properties、edge_properties |
| kb_graph_query | anchors/hops：§6 | hits 与 graph_result：status、diagnostic、witnesses |

工具参数不能包含用户指定的数据库路径、文件路径、SQL 或命令。插件内部通过固定 Python 可执行文件、桥接脚本和服务端数据库路径调用；stdin/stdout 只传 JSON。

### 7.2 生命周期

每问新建隔离 DSH home 和 SDK 会话 → 使用 `sdk-minimal` 配置、关闭默认文件/终端工具并限制注册表 → 首先检索 → 按需探索/执行/修正图谱计划 → 生成最终回答 → 关闭 SDK → 删除临时 home → SQLite 保存已返回的轨迹和本轮消息。

旧聊天不自动加入本轮上下文；同轮工具历史由 DSH 管理。应用会话持久化不等于长期模型记忆。默认 DSH Messages 适配器和图谱 Chat Completions 适配器必须分开配置。

### 7.3 回答结构验收

1. DSH 运行正常完成，存在非空回答。
2. 已发生 `kb_search` 调用，且对应工具结果成功；只出现调用事件不算完成。
3. 有来源时，回答至少包含一个 `[S编号]`，全部引用均属于本轮实际返回的来源。
4. 无来源且检索正常，返回明确的未检索到资料说明；检索故障按 §5.3 处理。
5. 返回并落库前检查语料版本；超预算、引用非法、工具未完成或版本冲突不得标为 generated。

结构验收不能证明每句话得到原文支持，真实质量验收需要人工核对。文档里的提示性指令视为资料，工具白名单仍然有效；不承诺完全免疫提示注入。

## 08｜HTTP、页面与错误契约

### 8.1 现有接口的候选兼容契约

| 方法 / 路径 | 请求 | 成功结果 |
| --- | --- | --- |
| GET / | 无 | 本地页面，200 |
| GET /api/config | 无 | model_ready、harness；不返回密钥 |
| GET /api/documents | 候选扩展：可选 tag 精确筛选 | 文档元数据数组；无参数保持返回全部资料 |
| POST /api/documents | name、content，均为字符串 | document_id：整数、created：布尔、chunk_count：整数 |
| POST /api/demo | 空对象 | 导入虚构示例的结果数组 |
| DELETE /api/documents/{id} | 空对象 | `{ "ok": true }`；删除不存在项按幂等成功 |
| GET /api/index | 无 | 索引状态、可用性、数量和 job 状态 |
| POST /api/index | 空对象 | 202，running 任务状态；不等待构建完成 |
| GET /api/graph | 无 | 索引状态及有界 nodes/edges；无有效代时无图数据 |
| GET /api/chunks?ids=1,2 | 最多 12 个十进制片段 ID | 当前原文数组；缺失项不伪造内容 |
| GET /api/sessions | 无 | 应用会话数组 |
| POST /api/sessions | 空对象 | session_id |
| DELETE /api/sessions/{id} | 空对象 | `{ "ok": true }`，级联消息和轨迹 |
| GET /api/messages?session_id=... | 应用会话 ID | 消息、status、sources 数组 |
| GET /api/runs?session_id=... | 应用会话 ID | 已保存运行和 events 数组 |
| POST /api/ask | session_id、question、mode | answer、sources、status、retrieval；生成模式当前 retrieval 可为 null，降级信息仍须能在答案区查看 |

下列为 FR-13—FR-15 的**新增候选接口，尚未实现**；最终任务必须补充输入、事务与失败场景验证。

| 方法 / 路径 | 请求 | 结果与约束 |
| --- | --- | --- |
| POST /api/notes | name、content | 新建 201，重复 200；document_id/created/chunk_count；内容重复按导入去重策略，返回已有记录且不修改其 kind |
| GET /api/documents/{id} | 无 | 原文、name、kind、tags、时间等；不存在返回 404，供编辑器读取 |
| PATCH /api/documents/{id} | name/content 至少一项 | 200，document_id/changed/chunk_count；未变更时 changed=false；不存在 404、与其他资料正文重复 409；并发编辑控制待定 |
| GET /api/tags | 无 | name/document_count 数组；用于标签筛选入口 |
| PUT /api/documents/{id}/tags | tags：字符串数组 | 200，document_id/tags；整体替换集合，按规范化后的名称去重；不存在资料 404 |
| GET /api/export | 无 | 200，application/zip；Content-Disposition 下载文件名；失败不返回伪完整 ZIP |

资料列表候选增加 kind/updated_at/tags 字段，按标签精确筛选；其余现有字段保留。新增元数据表和字段需迁移验证。新接口只供用户操作，DSH 工具仍保持只读。

`mode` 为 `retrieve` 或 `generate`；问题 1—1000 字符，未知会话拒绝，首版同时只处理一轮问答。消息状态为 `retrieval_only/no_evidence/generated/model_error`；状态字段不能静默吞掉通道错误。

返回对象的最低字段约定如下；时间值沿用 SQLite 时间字符串，客户端不应假定包含时区偏移。数组列表为空时返回 `[]`，不返回 null。

| 对象 | 字段 / 类型 |
| --- | --- |
| 文档元数据 | id：整数；name、created_at：字符串；chunk_count：整数 |
| 会话 | id、title、created_at：字符串 |
| 消息 | id：整数；session_id、role、content、status、created_at：字符串；sources：来源数组 |
| 运行记录 | id：整数；session_id、harness_session_id、created_at：字符串；finish_reason：字符串或 null；events：事件数组 |
| 当前片段 | chunk_id、document_id、ordinal：整数；name、content：字符串 |
| 索引概况 | status、message：字符串；正常配置下含 revision：整数，dependency_ready、llm_ready：布尔；ready 时增加 generation、built_at：字符串和 chunks、entities、relations：整数；job 含 status、message |
| 图谱 | status、message：字符串；nodes、edges：数组，节点/边结构见 §3.3；原型未统一返回版本及裁剪信息，实施时补齐 |
| 问答 | answer：字符串；sources：来源数组；status：消息状态；retrieval：§5 检索元数据或 null |

DSH 事件保留 SDK 返回的事件结构，包含 type/data；工具结果按 callId/toolCallId 配对。前端不得根据事件中的任意路径读取文件，也不应假定 SDK 事件等同于稳定的公开产品 API。

POST/DELETE 要求 JSON 对象和 `application/json`，请求体上限 1,500,000 字节；文档另按 UTF-8 1 MB 限制。存在 Origin 时仅接受同端口 localhost/127.0.0.1；无跨域开放。仅监听 loopback 不是身份认证方案。

### 8.2 错误与展示

- HTTP 400：输入、配置或当前前置条件错误；404：未知路由；409：已有问答处理中；500：本地数据库错误。候选兼容约定保留业务模型失败用 200 + `status=model_error`，不能只看 HTTP 200 判断成功。
- 构建运行中重复提交，现有原型使用 400；若调整为 409，须同步接口文档及调用方，不由文档假称已经调整。
- 错误体采用 `{ "error": "可操作中文说明" }`；业务问答错误通过 answer/status 展示，不泄露 Key 或外部服务原始敏感响应。
- 当前没有统一 machine-readable error code，列为后续接口改进，首版不得要求前端解析中文子串决定行为。
- 图谱 API 候选最多 150 节点、300 边，UI 最多绘制 24 节点、50 条连接；必须说明为局部展示，不将裁剪数量当全图数量。
- 原文、答案、文件名和轨迹均按文本渲染；界面展示来源、真实降级状态、构建等待和重试入口。

## 09｜预算、并发与配置

### 9.1 候选硬边界

| 边界 | 候选值 | 含义 |
| --- | --- | --- |
| 构建规模 | 500 个 SQLite 片段 | 超限拒绝并提示，不静默截断资料 |
| 构建任务 | 1 个 / 30 分钟超时 | 后台执行；首版任务状态不持久化 |
| 问答并发 | 1 轮 | 忙时明确拒绝，不无界排队 |
| DSH Agent 模型请求 | 最多 5 次 | 包含最终回答请求；不是总工具调用或费用上限 |
| DSH 每步输出 | 1200 tokens | 截断或未完成不得验收为正常回答 |
| SDK 初始化 / 请求 | 30 秒 / 120 秒 | 外层整轮预算仍优先 |
| 整轮 DSH 进程 | 150 秒 | 超时终止进程树；POSIX/Windows 分别实现 |
| 检索子进程 / 重排请求 | 45 秒 / 20 秒 | 是局部上限；嵌套过程可能被更早的外层预算终止 |
| 工具桥进程 | 75 秒；stdout ≤100,000 字节 | 超时或超长按工具失败，不接收截断 JSON |

图谱抽取、检索关键词提取和重排有额外 API 请求。首版记录实际耗时和能取得的用量；暂不承诺严格金额上限。小规模索引复制和单机文件后端不适合扩展为生产并发服务。

### 9.2 配置契约

| 配置组 | 字段 | 说明 |
| --- | --- | --- |
| DSH | LLM_API_KEY / DEEPSEEK_API_KEY、DSH_MODEL、DSH_BASE_URL | Key 前者优先；Messages 协议；不得误配 Chat Completions 地址 |
| 图谱与重排 | RAG_ENABLED、RAG_LLM_BASE_URL、RAG_LLM_MODEL、RAG_LLM_API_KEY | RAG Key 空时按实现复用主 Key；Chat Completions |
| Embedding | EMBEDDING_BASE_URL、EMBEDDING_MODEL、EMBEDDING_DIM、EMBEDDING_API_KEY | 维度必须与服务响应一致；校验向量非零、有限、数量/序号完整 |

`.env` 只在服务端读取，修改后重启；`.env.example` 只放占位配置。密钥、数据库、node_modules、虚拟环境和派生索引不进入 Git。云模型接收什么数据必须在教程和操作入口说清楚。

## 10｜恢复、升级与运行边界

- 首次启动自动创建 SQLite 表；旧发布基线升级时新增 rag_state 等结构，需在副本上验证迁移，失败保留原数据库。
- 更新模型/维度或处理流程后索引过期；不尝试跨版本解释不兼容向量。
- 构建失败可修复配置后手动重试；进程硬终止不保证保存最后一段未返回轨迹，也不自动续建。
- 临时查询副本正常结束后清理；未发布代可能残留，不能被自动读取。旧代自动回收在首版范围外。
- 备份/恢复以停服后复制完整数据库及所需索引为候选操作；不能在线随意复制一个 WAL 模式主文件并宣称备份完整。
- 公开 GitHub 教程必须与交付的实际提交一致；本地未提交文档或截图不能冒充仓库当前功能。

## 11｜验收计划与需求追溯

本轮不执行应用测试。下表是后续验证入口；“已有”表示存在对应测试素材，不代表当前工作树已被重新验证。脚本模型与低维模拟向量只能验证 L2 链路。

| 需求 | Spec 章节 | 验收编号与关键断言 | 已有入口 / 待补 |
| --- | --- | --- | --- |
| FR-01 | §3—4 | AC-01 合法导入、重复不增、坏输入无部分写入、切块覆盖 | test_project：test_import_deduplicate_restart_and_retrieve、test_atomic_import_failure、test_chunk_coverage_and_overlap、test_text_validation_and_fts_input |
| FR-02 | §3、§10 | AC-02 重启持久化、问答事务完整、已返回轨迹可读 | test_project：test_import_deduplicate_restart_and_retrieve、test_dsh_trace_persist_and_session_delete；待补问答写入中途失败回滚 |
| FR-03 | §4—5 | AC-03 无 Key 不启动模型进程，关键词返回来源 | test_project：test_http_import_ask_persistence_and_no_unnecessary_model_call、test_dsh_missing_key_does_not_start_runtime |
| FR-04 | §4、§8 | AC-04 后台构建、失败不切换、资料变化阻止发布 | test_rag：test_http_background_build_and_graph_sources、test_failed_rebuild_cannot_replace_published_generation、test_edit_during_build_cannot_publish_a_stale_graph |
| FR-05 | §5 | AC-05 三路确实运行、池去重、非法重排 ID 拒绝、降级可见 | test_rag：test_real_graph_vector_recall_and_persistent_provenance、test_candidate_collection_is_balanced_and_deduplicated、test_rerank_rejects_invented_evidence_ids、test_provider_failure_is_visible_and_falls_back |
| FR-06 | §6—7 | AC-06 schema→错误计划 empty→修正两跳→原文 | test_rag：test_schema_plan_execution_and_empty_plan_repair、test_real_dsh_explores_executes_repairs_and_persists_witness |
| FR-07 | §7 | AC-07 真工具成功且引用合法；无检索/假引用/超限不得 generated | test_project：test_dsh_answer_acceptance；harness/integration.test.mjs 的检索改写、空结果、五步上限测试；待补版本变化拒绝与语义人工核对 |
| FR-08 | §3、§6、§8 | AC-08 图谱点击能到原文，裁剪标识明确 | test_rag：test_http_background_build_and_graph_sources；scripts/verify-rag-ui.mjs；待人工核对裁剪及错误提示 |
| FR-09 | §3、§7—8 | AC-09 会话消息轨迹持久化并级联删除 | test_project：test_dsh_trace_persist_and_session_delete；Node HTTP→DSH→SQLite 集成测试 |
| FR-10 | §3—4 | AC-10 删除停止当前检索、历史保持快照、来源身份不混淆 | test_project：test_delete_removes_index_but_history_snapshot_survives；test_rag：test_delete_invalidates_both_graph_and_vectors；待补 ID 复用和删除/生成竞态 |
| FR-11 | §5、§7—9 | AC-11 错误与空结果区分，工具/整轮超时可恢复 | 已有 provider failure 与 DSH acceptance 测试部分覆盖；待补仅检索错误 UI、进程树超时清理、错误后下一轮 |
| FR-12 | §1、§10—12 | AC-12 干净检出可照说明使用和维护，数据备份方法明确 | 待干净环境运行及真实服务冒烟；以最终提交记录，不以开发环境代替 |
| FR-13 | §3.4、§4、§8 | AC-16 新建笔记、编辑标题/正文、重启后读取；正文修改使旧索引失效，重复/失败不破坏原记录，并发不静默覆盖 | 新增候选，待实现及必要验证；包含来源身份、事务回滚及迁移 |
| FR-14 | §3.4、§8 | AC-17 标签多对多、精确筛选、重复归一、无标签资料可用；标签变化不重建全文索引 | 新增候选，待实现及必要验证；标签筛选不改变问答范围 |
| FR-15 | §3.5、§8、§10 | AC-18 ZIP 可独立解压，正文/标题/标签与快照一致；无 Key/轨迹/索引文件，失败不改数据 | 新增候选，待实现及必要验证；不将知识导出等同于应用完整备份 |
| NFR-01—02 | §2、§7—9 | AC-13 loopback、Origin、文本渲染、工具白名单、无密钥泄露 | 已有 HTTP/DSH 测试部分覆盖；待补越权工具和提示注入回归、接口/轨迹密钥检查 |
| NFR-03—04 | §3—6、§9 | AC-14 规模/预算有界、无过期证据与半发布 | 已有预算、CAS、删除测试部分覆盖；待补边界及跨层并发检查 |
| NFR-05—06 | §1、§8、§12 | AC-15 干净环境记录、跳过项不计成功、桌面/390px 操作 | 当前有 UI 检查脚本；待最终版本环境记录与人工操作 |

### 11.1 真实质量评估（L3/L4，待执行）

1. 确定服务、版本和费用预算；仅用公开或虚构资料。
2. 冻结至少 20 题和标准证据，含直接关键词、语义改述、两跳关联、无答案、冲突信息；题目不得由最终系统输出反推答案。
3. 比较 BM25、三路候选以及三路加图谱工具流程，在相同语料/模型/预算下报告所需证据召回、人工判断的答案支持情况、错误/拒答、延时及费用。
4. 记录失败案例和小样本局限，按个人资料的查找与使用效果评估，不从单一演示推出整体优越性。
5. 质量阈值在试测后评审确定；未确定前只报告测量值，不声称完成质量验收。

## 12｜SDD 执行与变更规则

当前产物是 PRD + Spec 草案。下一步先评审个人知识管理的首版范围及新增 FR-13—FR-15，再建立 `TASKS.md`；每个任务须列需求 ID、Spec 章节、文件范围、验收编号和结果。未确认的模型/预算不得假定为已授权付费执行。

实施顺序建议：盘点并隔离现有原型 → 对齐资料/笔记/标签及导出模型 → 对齐数据身份与一致性 → 对齐召回/错误契约 → 对齐 DSH 工具及界面 → 补必要验证 → 真实服务试测 → 更新使用维护文档与仓库交付。这个顺序是后续计划，本轮不执行。

需求变化先更新 PRD，方案变化先更新 Spec，再调整任务；无法满足规格时记录偏差并评审，不能只改代码。现有测试绿灯只证明其覆盖的断言，不代表未覆盖需求自动满足。

发布条件：确认范围内 P0 验收通过；L1/L2 与 L3/L4 分开列出；未验证能力明确标注；无未解释的规格偏差；文档链接、截图与发布提交一致。只交付工程演示时，必须明确真实质量尚未验收。

## 13｜与现有原型的差距及未决事项

| 编号 | 差距 / 风险 | 后续处理与对应验收 |
| --- | --- | --- |
| GAP-01 | 项目定位更新后，笔记编辑、标签和导出尚无实现 | 先评审 FR-13—FR-15；按 §3.4—§3.5、§8 拆分任务，不将现有问答原型视为完整知识管理系统 |
| GAP-02 | 查询前后部分 revision 检查不能覆盖生成和落库竞态 | 统一版本上下文及最终检查；AC-07、AC-10、AC-14 |
| GAP-03 | SQLite 数字 ID 可能复用，历史来源不能依赖当前行解释 | 决定永久身份/版本化来源方案、验证迁移与快照读取；AC-02、AC-10 |
| GAP-04 | 仅检索 HTTP 路径当前可能把通道故障且无命中显示为普通无资料；生成端降级提示也需统一 | 对齐 §5.3 与界面；AC-05、AC-11 |
| GAP-05 | 部分工具字段的完整边界校验和响应版本字段尚未统一 | 贯穿 HTTP→DSH→Python bridge；AC-06、AC-13、AC-14 |
| GAP-06 | 真实模型 Key、实际 Embedding 权重、质量/延时/费用未测 | L3/L4 单独执行，不能以模拟测试宣告完成 |
| GAP-07 | Python 传递依赖、干净环境与旧库迁移尚未形成最终复现记录 | 最终方案后固定环境并验收；AC-12、AC-15 |
| GAP-08 | 当前 README/教程等本地文件描述未提交原型，与公开基线不同 | 后续发布前统一文档及对应提交；本轮保留原样避免继续实现 |
| GAP-09 | 旧代物理数据、未返回轨迹与内存任务不具备完整清理/恢复能力 | 首版明确边界；若要求彻底清除或续建，先扩展需求 |
| GAP-10 | 新增编辑接口的并发策略及正文规范化需冻结 | 首版考虑单人单写锁或显式冲突检查；不得在并发保存时静默覆盖，AC-16 验收 |

## 14｜变更记录

| 版本 | 日期 | 内容 |
| --- | --- | --- |
| 0.1 | 2026-11-07 | 建立技术草案；补齐架构、数据、索引协议、召回、图计划、DSH、接口、验收追溯与原型差距；未修改实现 |
| 0.2 | 2026-11-07 | 按个人知识管理目标调整规格，删除研究选型和复现任务，补充笔记、标签、导出候选契约；未修改实现 |

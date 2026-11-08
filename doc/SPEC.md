# 个人知识管理助手｜技术规格（Spec）

| 项目 | 内容 |
| --- | --- |
| 产品 / 版本 | Personal Knowledge Assistant / 1.0 |
| 日期 / 状态 | 2026-11-07 / 开发规格基线，未声明实现完成 |
| 需求与验收 | [PRD](PRD.md) · [Verify](VERIFY.md) · [任务](TASKS.md) |
| 机器可读契约 | [OpenAPI 3.1](openapi.json) |

本文定义目标行为。现有代码仅供复用评估，不是接口或一致性的判定依据。接口字段以本文件语义和 OpenAPI 共同约束；二者冲突时先修正文档再实施。全部命令和文件职责为目标版本约定，不能当作当前仓库已有功能。

## 01｜架构与固定决策

| 决策 | 1.0 选择 |
| --- | --- |
| 应用 | Python 3.12 参考环境、标准库 HTTP 服务；浏览器 HTML/CSS/JS；只监听 127.0.0.1 |
| 原文与应用状态 | SQLite，FTS5、WAL、foreign_keys=ON、10 秒 busy timeout |
| Harness | 真正使用 DSH SDK；DSH/sdk-client/tools 均固定 0.2.2-rc.1，Node ≥24.9.0 |
| 图谱与向量 | lightrag-hku==1.5.7，JsonKVStorage、NetworkXStorage、NanoVectorDBStorage |
| 检索 | BM25 + 文本向量 + 实体关系召回；候选去重后正文重排 |
| 图查询 | 只读 JSON 计划，最多 3 跳；无通用 SQL/Cypher |
| 编辑 | documents.version 乐观并发控制；片段 ID 永不复用 |
| 一致性 | 固定语料 revision；最终返回/落库前在写事务内校验；并发变化时拒绝旧结果 |
| 范围 | 笔记、标签、导出均为 P0；资料最多 500 片段；只支持一实例访问同一 DB |

```text
浏览器：资料库 / 笔记 / 标签 / 搜索问答 / 图谱 / 导出
  └─ Python HTTP 与业务服务
       ├─ SQLite：原文、标签、片段、FTS、会话、消息、轨迹、索引指针
       ├─ 索引任务 → 独立代目录 → 抽取模型 / Embedding → LightRAG
       ├─ 本地关键词 / 三路检索 → 去重与重排 → 原文来源
       └─ Python 适配器 → DSH SDK → 白名单 Python 工具桥
                                      └─ 检索 / schema / 图计划
```

| 模块 | 责任 |
| --- | --- |
| app.py / index.html | HTTP、资料编辑与列表、状态展示、来源阅读，不自行实现 Agent 循环 |
| store.py / knowledge.py | 事务、迁移、版本、切块、FTS、标签、会话与历史 |
| rag_backend.py / index_jobs.py | LightRAG 生命周期、版本配置、隔离构建、后台任务 |
| retrieval.py / graph_tools.py | 多路候选、重排、只读图计划与回指 |
| model_client.py / harness/ | DSH 会话、工具预算、结果验收、进程树清理 |
| maintain.py（新增） | 停服备份、恢复、派生索引清理；不作为模型工具 |

## 02｜输入、身份与版本规则

- 文档导入接受 name/content 字符串，name 原始长度 1—120 Unicode 字符，取最后一段路径并去首尾空白后仍须非空，扩展名大小写无关 .txt/.md。笔记标题同样限制原始长度并去首尾空白，不要求扩展名。
- 正文上传前字节长度 ≤1,048,576，UTF-8、无 NUL；CRLF/CR 转 LF，去首尾空白，结果不得空。digest=SHA-256(规范化正文 UTF-8)。所有入口采用同一规则。
- 按 800 字符切块、重叠 120，ordinal 从 1 开始。每段正文保留精确字符串，不由模型重写。中文双字/单字、英文数字单词做 FTS token，查询最多 48 个去重 token，受控 OR，不暴露 FTS 表达式。
- 全库新建/编辑后必须 ≤500 片段；超限在写事务中拒绝，不截断、不部分导入。重复正文返回已有记录；编辑为另一资料的正文返回冲突。
- document_id、chunk_id 为正整数 AUTOINCREMENT，删除后不得复用。source_id=`S{chunk_id}`，对应唯一片段；本轮合并相同来源的通道、名次和路径。
- documents.version 初始 1，标题/正文/标签实际变化均 +1；无实际变化不递增。PATCH/标签替换/删除必须提交 expected_version，过期返回 409，不能静默覆盖。
- rag_state.revision 仅在新增/删除/正文变化时递增；catalog_revision 在任何资料或标签关联实际变化时递增。标题/标签不触发图谱重建。索引建立在 revision 上，导出用 catalog_revision 标识元数据快照。
- 所有新时间使用 UTC RFC 3339；输入长度按 Unicode code point 衡量，字节限制单独计算，不能用前端 UTF-16 length 代替服务端校验。

## 03｜持久化模型、事务与迁移

### 3.1 目标 SQLite schema

以下 DDL 是 schema_version=2 的规范，迁移实现可分步骤，但最终约束必须一致。messages.sources_json 与 events_json 保存有界 JSON；新运行的 turn_id 将两条消息和一份轨迹关联起来，旧记录允许 null。

```sql
PRAGMA foreign_keys=ON;
CREATE TABLE documents (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('imported','note')),
 content TEXT NOT NULL,
 digest TEXT NOT NULL UNIQUE,
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE chunks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL CHECK(ordinal>0),
 content TEXT NOT NULL,
 UNIQUE(document_id,ordinal)
);
CREATE VIRTUAL TABLE chunk_search USING fts5(tokens,tokenize='unicode61');
CREATE TABLE tags (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL UNIQUE COLLATE BINARY
);
CREATE TABLE document_tags (
 document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
 PRIMARY KEY(document_id,tag_id)
);
CREATE INDEX document_tags_by_tag ON document_tags(tag_id,document_id);
CREATE TABLE sessions (
 id TEXT PRIMARY KEY,
 title TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 turn_id TEXT,
 role TEXT NOT NULL CHECK(role IN ('user','assistant')),
 content TEXT NOT NULL,
 sources_json TEXT NOT NULL DEFAULT '[]',
 status TEXT NOT NULL DEFAULT 'ok',
 retrieval_json TEXT,
 created_at TEXT NOT NULL,
 UNIQUE(turn_id,role)
);
CREATE TABLE harness_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 turn_id TEXT UNIQUE,
 harness_session_id TEXT NOT NULL,
 finish_reason TEXT,
 events_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE rag_state (
 id INTEGER PRIMARY KEY CHECK(id=1),
 revision INTEGER NOT NULL DEFAULT 0,
 catalog_revision INTEGER NOT NULL DEFAULT 0,
 generation TEXT,
 built_revision INTEGER,
 profile TEXT,
 updated_at TEXT
);
INSERT INTO rag_state(id) VALUES(1);
PRAGMA user_version=2;
```

来源快照必需字段：chunk_id/document_id/ordinal、source_id、name、content、document_version、revision、generation（可 null）、channels[]、channel_ranks（对象）、rank_position、graph_paths[]。原型的 channel_ranks JSON 字符串形式在 1.0 改为对象。快照中的正文和标题在后续编辑/删除后不改变。

### 3.2 事务边界

- 导入：容量及 digest 校验 → 写 documents/chunks/FTS → 递增 revision/catalog_revision，同事务提交。
- 编辑：BEGIN IMMEDIATE → 校验 expected_version/容量/重复 → 正文变更时替换片段和 FTS → 递增对应版本 → 提交。标题独改保留原片段。
- 标签：请求条目最多 20 个，每项原始长度不超过 40，去首尾空白后 1—40 字符，大小写敏感，去重后保存；BEGIN IMMEDIATE 校验版本并整体替换关联、更新版本/catalog_revision；不改 revision。未使用的标签不出现在列表，后台可保留行。
- 删除：事务内校验版本并删除当前 FTS、资料/片段/标签关联，递增两类 revision；不存在返回 404，版本过期返回 409。历史消息/快照保留。
- 问答：用户消息、助手结果、检索元数据及已收到的 DSH 轨迹同事务保存。turn_id 是服务端 UUID。DB 写失败不留下半轮，返回 500；没有到达父进程的崩溃轨迹不保证保留。

### 3.3 迁移和恢复

首次创建使用 schema 2；旧 user_version=0 的发布数据库通过停服备份后迁移，新建临时 schema 2 库并复制原始记录/ID、重建 FTS，完成外键和完整性检查后原子替换。旧资料 kind=imported、version=1、updated_at=created_at；旧 UTC 时间转为带 Z 的时间。旧历史 JSON 原样保留，读取标为 legacy，不用当前片段内容重写历史。新库的 ID 序列至少高于存活 ID 与历史快照可识别的 chunk_id 最大值。

迁移后清空派生索引指针并提示重建；不得直接沿用旧格式代。迁移失败保留原库与备份，未知较新 schema 拒绝启动。替换前关闭连接并处理 WAL，不在应用运行中复制主文件冒充完整备份。

## 04｜索引构建、状态与一致性

### 4.1 构建协议

单实例使用 OS 文件锁保护同一 DB（POSIX flock / Windows msvcrt.locking，进程退出释放），由 HTTP 应用或维护命令持有，内部受控工作进程不重复获取实例锁。问答互斥锁仅限制同时一轮，不阻止资料编辑；构建另有单任务锁。任何 SQLite 事务不得跨模型或 Embedding 网络请求。

1. 主动 POST 构建，验证 RAG 开关、依赖、模型配置、非空资料和容量；返回 202/job_id。重复运行返回 409。
2. SQLite 一致读快照获取 revision=R 和全部片段；读取启动时固定的配置 profile=P。
3. 创建 UUID generation=G 独立目录。以 SQLite 片段构建 LightRAG 输入；按后端规范化后的相同正文合并输入，保存内部 chunk→全部对应 SQLite chunk 的一对多映射，禁止因后端去重丢失出处。
4. 确认全部处理完成、向量有效、来源映射和图文件可读；finalize/刷写完成后写 manifest，再 CAS 更新指针：仅 current revision=R 时发布 G/R/P。
5. 失败不切换旧指针，未发布代正常清理；硬终止残留代不自动加载。文件先完成、SQLite 后发布，不声称跨库事务。

profile 是后端版本、模型/地址、Embedding 模型/地址/维度、规范化/切块/检索格式版本的 SHA-256，不含 Key。同一别名背后的权重变化不能自动检测，需人工重建。

目录 `<db-stem>-rag-index/<G>/` 包含后端文件、graph.json 和 manifest.json；manifest 至少包含 format_version=2、revision、profile、counts、source_map。图节点包含 id/type/description/chunk_ids，边包含 source/target/description/relation/chunk_ids，所有回指均须有效。

### 4.2 状态机

| 索引状态 | 含义 |
| --- | --- |
| disabled | RAG 关闭，仅词法 |
| config_error | 配置解析失败 |
| not_built | 未发布索引 |
| unavailable | 依赖/必要配置缺失 |
| stale | 语料 revision 或 profile 不匹配 |
| missing | manifest 或必需文件缺失、格式或来源校验失败 |
| ready | 结构与版本允许读取；不保证外部 API 健康或图谱事实正确 |

检查优先级依次为 disabled/config_error、not_built、unavailable、stale、missing、ready。构建任务独立状态 idle/running/completed/error，保存内存中的 job_id、时间、message；重启不续建、不保留任务历史，按指针判断当前索引。

### 4.3 查询与资料修改竞争

查询开始的可信上下文包括 R、G、P，来自服务端而非模型参数。SQLite 候选、LightRAG 查询和所有 DSH 工具都绑定该上下文；工具执行前后检查版本。LightRAG 查询使用临时代副本，避免缓存写入发布快照。

在响应提交点，执行 BEGIN IMMEDIATE，核对 current revision=R；版本一致才保存消息/结果并提交。此后发生的修改属于下一语料版本，已返回来源作为时点快照展示。若版本不符，不自动重复收费生成：保存本轮 CORPUS_CHANGED 错误及已返回轨迹，sources=[]，用户可主动重试。仅检索同样处理，不能混合新旧结果。图谱读取和索引状态读取使用一致快照，不把旧图拼接新片段。

正文编辑/删除不会等待最长 150 秒的模型问答；相应问答可能因此被版本检查拒绝。若用户删除正在执行的应用会话，DELETE 返回 409 SESSION_BUSY，待本轮结束后重试。

## 05｜检索与图谱契约

### 5.1 三种模式

| mode | 行为 |
| --- | --- |
| lexical（默认） | 只执行当前 SQLite 关键词搜索；绝不调用模型/Embedding |
| retrieve | 索引 ready 则三路，否则词法降级；可能调用提词、Embedding 和重排服务 |
| generate | DSH 工具循环；kb_search 内采用 retrieve，回答必须依托原文 |

每路最多 12 个候选；轮流收集并按 chunk_id 去重，池最多 24。至少两路可用且有至少两个候选时调用正文重排，要求返回完整候选 ID 排列及 reason；必须检查未知/遗漏/重复 ID、截断、非法 JSON。成功选前 4 段；失败保留收集顺序并标记 ranking.error，不虚构分数。LightRAG naive 用于文本向量，hybrid 用于实体/关系；top_k=8、chunk_top_k=12、后端内部 rerank 关闭。

retrieval 固定包含 revision、generation、mode（lexical/hybrid/partial/lexical_fallback）、message、routes[]、ranking。三条 route 按 bm25/vector/graph 顺序各出现一次，含 name、status（ok/error/unavailable/stale/not_built/disabled/not_run）、candidates、reason。三路均 ok 为 hybrid，有部分派生路可用为 partial，只有词法为 lexical_fallback；lexical 模式中未执行通道为 not_run。成功零候选不等于执行错误。ranking 含 method（llm_evidence/candidate_pool）、status（ok/error/not_run）、reason。

有命中且部分路由失败：可继续，提示降级。所有执行成功且无命中：no_evidence。任一路由错误且最终零命中：retrieval_error，不宣称确定无资料。只有 lexical 模式承诺完全本地。

### 5.2 图探索和计划

schema 返回实际 anchors≤8、entity_types≤40、relation_descriptors≤20、node_properties、edge_properties、revision/generation/status/message。锚点按问题名称命中及分词重叠确定，同分按名称排序；无匹配返回空数组。

计划 anchors 为 1—3 个实际实体名，每字符串 1—160 字符；hops 为 1—3 项，每项只有 relation 和 target_type，字符串 ≤160，可为空表示不限。relation 按实际描述大小写无关子串匹配，target_type 大小写无关精确匹配。无向遍历，不重复节点；按节点/边稳定顺序展开，每层前沿≤30。

必须完成所有跳数才返回 completed；无完整路径返回 empty 和诊断。最多 6 条 witnesses，每条含 nodes/relations/chunk_ids；最多 8 段来源。路径只用于检索回指，不赋予因果、动作方向或自动事实核验含义。

图谱 API 最多 150 节点/300 边，UI 最多 24 节点/50 边，返回 total_nodes/total_edges/truncated；页面再次裁剪时单独标注。原文和路径数据均按文本显示。

## 06｜DSH、权限与回答验收

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| kb_search | query：1—1000 字符 | hits + retrieval |
| kb_list_documents | 空对象 | 最多 100 项 name/chunk_count，非事实证据 |
| kb_graph_schema | query：1—1000 字符 | §5.2 接口 |
| kb_graph_query | anchors/hops | hits + graph_result（status/diagnostic/witnesses/revision/generation） |

工具全为只读，桥接脚本和 DB 路径由服务端注入，不接受模型指定路径、命令或 SQL。工具桥使用 SQLite mode=ro，不执行建表或迁移；查询缓存只写临时代副本。注册表白名单与默认终端/文件工具禁用同时生效。文档内指令按资料处理，不改变工具权限。

OpenAPI 的 x-dsh-tools 定义业务输入输出契约；插件按固定 SDK 的工具描述方式适配，不假设标准 JSON Schema 的 $ref 可以直接作为 SDK 参数传入。业务桥仍独立校验参数，不能只依赖模型或 SDK 校验。

每问创建隔离临时 DSH home，实际processCwd也置于该home以避免DSH CLI重复解析应用.env；逻辑cwd和插件绝对路径保留项目根，模型配置由Python白名单加载后通过环境继承。使用 sdk-minimal 和官方 Messages 适配器；先 kb_search，复杂关联按需探索/执行/修正；同轮历史交 DSH，旧聊天不注入。SDK 结束后 close，再清理临时目录。最终返回不超过 64 个去重来源，工具调用和结果通过 callId/toolCallId 对应；turn_id 保存实际运行。

生成响应的 retrieval 保留最后一次 kb_search 的结构化状态，message 同时汇总本轮此前通道/工具失败或降级；完整详情在同 turn_id 轨迹中。不能因最后一次调用成功而隐藏前面的失败。所有来源来自绑定 R/G/P 的本轮成功工具结果，按首次出现顺序合并 source_id，保留通道及路径并集；同一路 channel_ranks 取最小名次，rank_position 按最终输出位置重新编号。

回答通过结构验收必须：finish_reason=completed，非空最终文本，kb_search 有成功配对结果；有证据的事实带 [S数字]，且所有 ID 属于本轮来源。资料冲突时并列说明并引用双方；不足时说明不足，不以模型常识补齐。无命中时统一 no_evidence 文案；工具失败或版本变化不能转换成正常回答。合法引用不等于语义正确，仍需 Verify 的人工原文核对。

## 07｜HTTP 与错误契约

OpenAPI 是字段/类型/必填/枚举/响应码的机器可读来源。以下是操作摘要；所有 JSON 对象拒绝未声明输入字段，不把错误类型自动转为字符串。

| 操作 | 契约要点 |
| --- | --- |
| GET /api/config | 仅 model_ready、rag_enabled、harness；Key 存在不代表服务健康 |
| GET /api/documents | page=1/page_size=20（≤100），可选 tag 精确筛选；返回 items/total/page/page_size |
| POST /api/documents | name/content；新建 201、重复 200；document_id/created/chunk_count/version |
| POST /api/notes | name/content；同上；重复返回已有资料，不改变原 kind |
| GET /api/documents/{id} | 正文、kind、version、tags、时间；404 不存在 |
| PATCH /api/documents/{id} | expected_version 必需，name/content 至少一项；返回 document_id/changed/chunk_count/version |
| PUT /api/documents/{id}/tags | expected_version/tags；整体替换；返回 document_id/tags/version/changed |
| DELETE /api/documents/{id} | JSON expected_version；200 ok，404 不存在，409 版本冲突 |
| GET /api/tags | name/document_count 数组，仅显示至少关联一份资料的标签 |
| GET /api/export | ZIP 下载全部当前原文和元数据；无 Key、无模型调用 |
| GET /api/index、POST /api/index | 查询状态；启动返回 202 的 Job，运行中返回 409 |
| GET /api/graph | 当前图及裁剪信息；不可用时空数组和真实状态 |
| GET /api/chunks?ids=... | 1—12 个正整数；当前片段，不作为历史快照入口 |
| POST /api/demo | 导入固定演示资料；各文档按独立事务，返回逐项结果 |
| GET/POST /api/sessions | 分页列表；创建返回 201 session_id |
| DELETE /api/sessions/{id} | 200 ok；404 不存在，409 当前有问答 |
| GET /api/messages、GET /api/runs | session_id 必填，分页；按 id 升序；未知会话 404 |
| POST /api/ask | session_id/question/mode（默认 lexical）；200 返回已处理的本轮业务结果 |

列表资料按 updated_at DESC/id DESC，会话按 created_at DESC/id DESC，标签按名称排序。时间为 RFC 3339 UTC。所有列表有 total，删除导致的翻页漂移允许，不能据此混用检索语料版本。

问答响应包含 turn_id、answer、sources、status、retrieval、error（可 null）。status 为 retrieval_only/no_evidence/generated/retrieval_error/model_error；业务失败保存本轮后仍 HTTP 200，但 error.code 必须非空。校验失败 4xx 不产生问答记录，DB 保存失败 500 不产生半轮。用户消息 status=ok。

lexical/retrieve 的执行失败为 retrieval_error，generate 的执行失败为 model_error；语料变化分别使用上述状态并附 CORPUS_CHANGED。有候选但缺少足够事实时允许生成明确的资料不足说明，不把候选命中视为答案正确；是否正确拒答另由固定题集评估。正常结果 error=null。

错误统一为 `{"error":{"code":"VERSION_CONFLICT","message":"资料已被修改，请刷新后重试。","details":{"current_version":2}}}`。409 VERSION_CONFLICT 的 details 包含 current_version。业务错误用同一 ErrorDetail 嵌入问答响应。页面以 code 分支，不解析中文子串。

| HTTP / 场景 | code |
| --- | --- |
| 400 参数/未知字段/错误 JSON | INVALID_INPUT |
| 403 非同源写请求或非法 Host | ORIGIN_REJECTED |
| 404 资料/会话/路由不存在 | NOT_FOUND |
| 409 版本/重复正文/忙 | VERSION_CONFLICT / DUPLICATE_CONTENT / TURN_BUSY / SESSION_BUSY / INDEX_BUSY |
| 409 非空资料前置条件不满足 | EMPTY_CORPUS |
| 413 体积超限；415 非 JSON | PAYLOAD_TOO_LARGE / UNSUPPORTED_MEDIA_TYPE |
| 422 容量/配置/依赖 | CAPACITY_EXCEEDED / CONFIG_INVALID / CONFIG_REQUIRED / DEPENDENCY_MISSING |
| 500 DB 或内部异常 | STORAGE_ERROR / INTERNAL_ERROR |
| 问答业务错误 | CORPUS_CHANGED / RETRIEVAL_FAILED / MODEL_UNAVAILABLE / MODEL_FAILED / TIMEOUT / INVALID_CITATION / BUDGET_EXCEEDED |

写请求仅接收 application/json 对象，最大 6,400,000 字节（容纳 1 MB 正文的 JSON 转义开销；正文仍单独校验字节限制）；无正文的操作传 {}，DELETE 会话可空 JSON 对象。Origin 存在则必须同端口 localhost 或 127.0.0.1；只接受这些 Host，拒绝跨来源写和 DNS rebinding 的其他 Host。不设置跨域允许头；无 Origin 不构成身份认证。GET 导出使用 application/zip 和 Content-Disposition，其余 API JSON、页面 HTML；均 no-store/nosniff。

demo 按名称顺序导入目标提交中 sample_docs/ 下的 Markdown 文件；按文档分别提交，后续一份失败不回滚已导入的其他份。错误 details.completed 必须给出本次已完成项，用户重试通过 digest 去重，不隐瞒部分完成。固定质量题集另使用 doc/fixtures/，不依赖演示目录。

## 08｜预算、配置与故障恢复

| 项目 | 固定上限 / 行为 |
| --- | --- |
| 问答并发 | 一轮；其他问答 409，不无界排队 |
| DSH | 最多 5 次 Agent 模型请求、8 次知识工具调用、每步输出 1200 tokens |
| 超时 | SDK 初始化 30s、请求 120s、整轮 150s；外层截止优先；模型返回的答案、来源及轨迹（包括对象键）在返回/落库前移除配置的非空API Key，短Key也不例外 |
| 检索与工具 | 查询子进程 45s、重排请求 20s、工具桥 75s/100,000 字节 stdout；终止信号后1s内未退出则强制清理进程树 |
| 构建 | 单任务，1800s；超时清理进程/关闭资源，状态 error |
| 资源 | 所有外部 HTTP 有超时；向量非零、有限、维度/条数/序号一致；超长/非法响应拒绝 |

POSIX 杀进程组，Windows 清理整个进程树；仅终止父进程不算通过。模型预算不包含检索内部额外 API，不能作为总金额保证；不做隐式收费重试，用户主动触发才重试。

| 配置 | 默认 / 约定 |
| --- | --- |
| LLM_API_KEY / DEEPSEEK_API_KEY | 前者优先；不填仍可本地使用 |
| DSH_MODEL / DSH_BASE_URL | deepseek-flash / https://api.deepseek.com/anthropic，Messages |
| RAG_ENABLED | true；缺服务时明确不可用 |
| RAG_LLM_MODEL / RAG_LLM_BASE_URL | deepseek-flash / https://api.deepseek.com，Chat Completions |
| RAG_LLM_API_KEY | 回退主 Key |
| EMBEDDING_MODEL / EMBEDDING_BASE_URL | bge-m3 / http://127.0.0.1:11434/v1 |
| EMBEDDING_DIM / EMBEDDING_API_KEY | 1024 / ollama；维度限定 1—8192 |

环境变量优先于 .env，配置启动时读取且重启生效；页面不修改凭据。默认别名只是已有配置的工程起点，实际服务/模型版本必须在真实验收时记录；连通性未测不推迟字段及错误契约的实现。Python 传递依赖需在实施时锁定，npm ci 使用已有 lock。

## 09｜导出、备份与维护

导出用一个 SQLite 读事务读取原文与标签，不跨多个时点；读取后释放事务，先完整生成临时 ZIP，再发送 200 和文件。ZIP 文件 `documents/<document_id>.md` 只含正文；metadata.json 含 schema_version=1、exported_at、revision、catalog_revision 和 documents 数组，每项 id/name/kind/tags/digest/version/created_at/updated_at/file。文件名由服务端生成，不能包含用户提供路径；不导出聊天、Key、轨迹或索引。生成失败不能返回部分 ZIP 冒充成功，临时文件清理。

目标维护命令（待实施，不在本轮执行）：

```bash
python maintain.py backup --db data/knowledge.db --output backups/example
python maintain.py restore --backup backups/example --db data/restored.db
python maintain.py clean-index --db data/knowledge.db
```

维护命令必须获得同一 DB 的独占文件锁，应用运行则拒绝。backup 用 SQLite backup API 输出 knowledge.db 和 backup.json（schema、时间、SHA-256），不含 Key 或派生索引；目标非空拒绝。restore 校验摘要/完整性/schema，目标 DB 存在拒绝覆盖，恢复后清空索引指针并要求重建；原文、标签、聊天与快照保留。clean-index 在停服锁下删除该 DB 推导的代目录并重置指针，不能删除任意传入目录。备份覆盖应用持久化数据，知识 ZIP 只用于资料迁移，两者界限明确。

## 10｜需求追溯与实现差距

| 需求 | 本 Spec | Verify | 任务 |
| --- | --- | --- | --- |
| FR-01 | §2—3 | AC-01 | T02 |
| FR-02 | §3 | AC-02 | T01、T07 |
| FR-03 | §2、§5 | AC-03 | T04 |
| FR-04 | §4 | AC-04 | T05 |
| FR-05 | §5 | AC-05 | T06 |
| FR-06 | §5—6 | AC-06 | T06、T07 |
| FR-07 | §6—8 | AC-07 | T07 |
| FR-08 | §5、§7 | AC-08 | T08 |
| FR-09 | §3、§6—7 | AC-09 | T07、T08 |
| FR-10 | §2—4 | AC-10 | T02、T07 |
| FR-11 | §4—8 | AC-11 | T04、T07 |
| FR-12 | §8—9 | AC-12 | T09、T10 |
| FR-13 | §2—3、§7 | AC-16 | T02 |
| FR-14 | §3、§7 | AC-17 | T03 |
| FR-15 | §9 | AC-18 | T09 |
| NFR-01、NFR-02 | §6—8 | AC-13 | T07、T08 |
| NFR-03、NFR-04 | §2—8 | AC-14 | T01、T05、T07 |
| NFR-05、NFR-06 | §7—9 | AC-15 | T08、T10 |

现有原型差距已转成明确任务：永久片段身份、schema 迁移与 turn_id（T01）；笔记与冲突控制（T02）；标签（T03）；结构化错误和 lexical 模式（T04）；代校验（T05）；来源/ranking 对象和有界图查询（T06）；跨工具语料版本、最终事务校验和超时清理（T07）；产品 UI（T08）；导出与维护（T09）；真实服务和交付文档（T10）。不得仅把原型提交后称为按规格完成。

## 11｜开发入口与变更

开始实施时先按 T00 保存现有改动，之后按任务依赖推进。运行账号/Key、真实服务资源和测试用量是环境输入，缺少时 L3/L4 记录 BLOCKED；不影响基于模拟服务的业务开发。当前已按用户开发指令进入实现；实际完成情况和验证缺口维护在 Tasks / Verify。

变更顺序：PRD → Spec/OpenAPI/Verify/固定题集 → TASKS → 实现 → 证据与复验。引用 ID、HTTP 字段或持久化格式变化需同时更新迁移与契约。版本 1.0 是文档基线，不代表软件版本、功能验收或用户逐项审批签字。

## 12｜变更记录

| 版本 | 日期 | 内容 |
| --- | --- | --- |
| 0.3 | 2026-11-07 | SDD 草案和独立 Verify |
| 1.0 | 2026-11-07 | 冻结数据、版本/冲突、一致性、模式、接口、错误和维护协议；补齐迁移及开发追溯；未修改实现 |

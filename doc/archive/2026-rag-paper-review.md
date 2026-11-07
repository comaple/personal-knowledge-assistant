> 历史原型选型记录，不属于当前产品需求或开发依据。

# 2026 年 RAG 论文选型与实现边界

检索日期：2026-11-07。先核对 arXiv 提交日期、原文方法和作者代码，再决定适配；不以代码库最近提交日期冒充论文发表日期。使用 research-lit 工作流检索；本项目没有本地论文库或已连接的 Zotero/Obsidian，外部检索使用 arXiv API 与原始论文/作者仓库。

| 论文 | 首次提交 | 方法与结果范围 | 对本项目的选择 |
| --- | --- | --- | --- |
| [ArcRAG](https://arxiv.org/abs/2611.06481) | 2026-11-04 | 查询时探索图谱接口，规划、编译和修正多跳 Cypher；研究不同图谱构建之间的可迁移性 | 采用探索→规划→执行→检查/修正流程，明确做工程改编 |
| [RAGCompass](https://arxiv.org/abs/2611.05596) · [作者代码](https://github.com/Graph-COM/RAGCompass) | 2026-11-03 | 根据各路证据内容路由，训练 Qwen3-Reranker-0.6B 和比较头；论文提升主要来自训练后的评分器 | 仓库需 GPU 训练/缓存，未提供即用路由器权重；未在本项目复现，不把零样本提示词判断称为 RAGCompass |
| [Every Clause Counts / DMReranker](https://arxiv.org/abs/2611.03179) | 2026-11-02 | 查询拆成类型化条件，用决策模型返回的分布相乘；基于特定 judge/API 和 InfoSearch | 暂未接入对应决策模型；不把模型自报置信度冒充其概率分布 |
| [Glance](https://arxiv.org/abs/2610.40454) · [作者模型与代码](https://huggingface.co/airun-ai/GlanceRAG-MiMo-9B-Reranker) | 2026-10-29 | 共享 prefill、LoRA 和 pointer head，一套 9B 骨干同时重排与生成 | 作者部署依赖 GPU/特定骨干和适配器，不适合作为本机入门版默认；未复现 |
| [NexusRAG](https://arxiv.org/abs/2609.37661) · [作者代码](https://github.com/Jacob-biu/NexusRAG) | 2026-09-29 | 关系无关 Tri-Graph、语料邻域先验、双路传播；验证多跳证据召回 | 与当前显式实体关系图谱路线不同；未采用或声称复现 |

最新论文并不意味着所有基础模块都要重新发明。ArcRAG 的原文附录也使用 BM25、dense retrieval 与 RRF；其新贡献主要在查询局部接口诱导和可执行组合检索，而不是宣布旧排序算法失效。本项目遵循用户偏好，主流程不采用 RRF，保留 BM25 作为可检查的关键词召回基础。

本次选 ArcRAG 流程，是因为它能直接映射到 DSH 的工具循环：先观察实际图谱，提出有边界的计划，执行后检查结果，再修正。普通电脑可以管理图谱和数据库；需要的模型能力通过已有 API 接入，无需先训练新模型。

为了控制依赖和操作范围，我们将 Memgraph/Cypher 改为 LightRAG 生成的文件图谱加受限 JSON 查询计划。计划能执行连续的实体/关系约束并返回原文 witness，但不提供任意 Cypher、聚合查询或作者多 Agent 架构。本版没有检验 ArcRAG 的跨构建迁移性，也没有运行其公开 QA 基准。

证据重排则是独立的零样本工程扩展：读取三路候选原文，返回完整的候选 ID 排列，校验无新增或重复 ID。它不使用 RAGCompass 的训练评分器或比较头，不能继承论文效果数字。最终交付区分“方法来源”“工程改编”“链路测试”和“尚未验证的模型效果”。

## 实现对照

| 来源概念 | 具体文件/工具 | 与原论文的差异 |
| --- | --- | --- |
| 查询局部图谱接口 | `graph_tools.inspect_graph` / `kb_graph_schema` | 基于真实实体、关系和属性生成有界接口；未复现两个并行 Explorer |
| 有界查询计划 | `kb_graph_query(anchors, hops)` | JSON 计划，不是 Cypher；最多 3 跳，无任意程序执行 |
| 执行与修正 | DSH 工具结果为 empty 时根据实际接口修正 | 使用单个 DSH Agent 和最多 5 次请求，没有作者完整代理编排 |
| 可检查 witness | 节点序列、关系描述、原文片段 ID、Session 轨迹 | 路径来自模型抽取，仍可能有错；不宣称 claim-level attribution 已完成 |
| 图谱构建与存储 | LightRAG 1.5.7 / NetworkX / NanoVectorDB | 这是开源基础设施，不把其 2024/2025 年方法包装成 2026 新论文 |

详细配置、运行步骤与已执行测试见 [多路召回与图谱实战教程](../hybrid-graph-rag.md)。

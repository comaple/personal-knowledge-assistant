# 知识图谱与多路检索实现

对应1.0.0-rc.1。产品目标是管理与使用个人知识，技术依据以 [Spec](SPEC.md) 和 [OpenAPI](openapi.json) 为准。

## 构建

SQLite原文按800字符/120重叠切块，每块作为LightRAG唯一输入。真实后端为JsonKV、NetworkX、NanoVectorDB；模型抽取实体/关系，Embedding服务生成向量。内部chunk ID映射回SQLite永久片段ID。

构建使用新generation目录；所有存储finalize后写graph/manifest，记录语料版本、模型指纹、来源与文件摘要。校验通过且语料revision未变才切换SQLite指针；坏代不发布，旧代不可变。

## 检索

- lexical：只调用SQLite FTS5/BM25，不调用任何模型。
- retrieve：索引就绪时并行文本向量naive和实体/关系hybrid，并独立执行BM25；不可用时明确退回词法。
- 每路最多12候选，轮流收集并去重到24；依据原文重排并校验ID完整排列，最后返回4段。
- 重排失败保留候选收集顺序并标记error；通道失败且零命中不能说成正常无证据。

## 关联与更新

DSH先查实际schema，再运行最多3跳的JSON计划；无完整路径则empty，可在预算内修正。计划返回witness和原文来源，不自动证明图谱事实正确。

正文更新/删除增加revision，让图谱和向量同步失效；标题/标签只增加catalog_revision。整轮绑定语料版本，最终事务确认后才保存正常结果；历史答案保留当时原文快照。

## 验证

[test_rag.py](../test_rag.py) 使用真实LightRAG/NetworkX/向量库，以及本地脚本模型和8维向量服务，覆盖构建、三路、路径修正、DSH、CAS与降级。[浏览器报告](verification/ui.json) 和 [验收报告](verification/REPORT.md) 分开交代工程链路与真实质量。

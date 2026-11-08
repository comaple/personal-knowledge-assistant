# 个人知识管理助手｜RC 实现验收记录

**17项离线完整用例通过，AC-07及真实模型L3/L4仍为BLOCKED。** 工程实现已形成可运行RC，尚不能宣称产品真实问答质量验收完成。

- 目标代码：`41738caad9bcddeb7d352991c5d63cb8c9b78d34`。后续提交更新文档、截图、任务状态及证据；CI修复仅显式指定缓存依赖路径，不改变应用实现。
- 运行编号：`pka-openspec-clean-41738ca`，2026-11-08，Codex；独立detached worktree，安装前工作树干净。
- 环境：macOS26.3 Intel，12逻辑CPU、32GB、SSD；Python3.12.9 / SQLite3.47.1 / Node25.3.0；DSH0.2.2-rc.1 / LightRAG1.5.7 / OpenSpec1.16.1。
- 使用该检出内全新Python虚拟环境、两份requirements锁与`npm ci`。全部数据库、资料及模型凭据均为隔离测试数据；未修改日常资料库。
- [机器记录、用例状态及证据摘要](run.json) · [Python安装](python-install.txt) · [npm安装](npm-install.txt)。

## 实际执行

| 执行 | 结果 | 证据 |
| --- | --- | --- |
| `python -m unittest -v test_project.py test_rag test_acceptance` | 54通过、0失败、0跳过 | [完整日志](python-tests.txt)，覆盖SQLite/迁移/契约/事务/并发及真实LightRAG/向量/图后端 |
| `npm run test:harness` | 9通过、0失败、0跳过 | [完整日志](dsh-tests.txt)，真实DSH SDK、预算、独立轮次、越权拒绝、超时/超量输出及恢复 |
| `npm run spec:validate` | 3通过、严格校验无问题 | [校验记录](openspec-validation.txt)，1个change与2份主规格 |
| `python scripts/run_ui_check.py` | 7项检查PASS | [浏览器日志](browser-check.txt)、[结果](ui.json)；真实Chrome、1440/390px、图谱/轨迹/编辑/草稿/历史来源 |
| `python scripts/benchmark_local.py` | PASS | [500片段全部原始样本](performance.json)，各预热5次、测100次，列表/原文/lexical p95为16.912/15.901/27.633ms，均≤500ms |
| `evaluate.py --mode lexical` | 固定20题执行完成 | [逐题记录](lexical-evaluation.json)，固定8份虚构资料；仅证明本地检索执行，语义质量仍需人工判定 |
| Ruff F/I、差异空白、本地文档链接 | PASS | 静态检查无错误，检查范围含新增OpenSpec接入说明 |

L2的模型服务由本地脚本提供，Embedding为8维测试向量；它们不能证明真实模型权重的检索、拒答、冲突处理或事实支持效果。超时回归通过可信内部测试入口缩短截止时间，实际创建并清理进程，另断言生产默认预算；没有声称所有测试都等待了150/75秒。

## 用例对应

| 用例 | 状态 | 关键证据 |
| --- | --- | --- |
| AC-01 | PASS | StoreTest：导入/去重、无效输入、字节/容量边界、失败回滚 |
| AC-02 | PASS | PersistenceAcceptanceTest：跨进程重启保留资料/标签/来源/轨迹；第二消息和轨迹写入故障整轮回滚；MigrationTest保留旧库/历史 |
| AC-03 | PASS | lexical在无Key及ready+Key下禁止模型/子进程调用，模拟服务请求数不变 |
| AC-04 | PASS | 真后台构建202、重复POST409 INDEX_BUSY、job_id稳定；改维度stale/缺文件missing；失败与版本CAS不替换已发布代 |
| AC-05 | PASS | 真后端单路故障保留另一通道；真实候选及正文重排；遗漏/重复/布尔/伪造ID/截断/非JSON拒绝并可见降级；池≤24、最终≤4 |
| AC-06 | PASS | 真DSH schema→错误计划empty→修正两跳completed；完整路径与原文；参数范围及越权拒绝 |
| AC-07 | BLOCKED | 无工具/假引用/版本变化的工程边界已测；真实模型事实支持、冲突与拒答仍需L3/L4 |
| AC-08 | PASS | Chrome图谱点击回指原文、编辑/删除后历史快照仍显示且不请求当前chunk；151/301图API边界、UI24/50局部计数及手机布局 |
| AC-09 | PASS | 两会话隔离、分页/级联、运行中SESSION_BUSY；真实DSH下一轮请求不含前轮标记且run_id不同；旧turn_id=null消息/轨迹迁移后可读 |
| AC-10 | PASS | 删除使当前索引失效、历史快照保留、片段身份不复用、错误ID/版本拒绝 |
| AC-11 | PASS | Key/服务失败分类、工具与整轮截止、查询截止、超量输出、实际子孙进程清理；故障后可继续调用/问答/删除会话 |
| AC-12 | PASS | 干净检出安装；备份恢复/索引清理、忙时拒绝、拒绝覆盖、坏摘要拒绝及数据保留 |
| AC-13 | PASS（L1/L2） | Host/Origin、存储脚本文本、SQL/路径/写入拒绝；真实DSH请求shell也不能执行；长/短Key在答案/历史/轨迹键值均移除。真实模型面对注入时的语义表现不在此结论内 |
| AC-14 | PASS | 500/501、1000/1001、5次请求/8次工具/输出预算；真实并发写入与单实例锁；构建、编辑、问答并发时拒绝混版并保留运行记录 |
| AC-15 | PASS | 干净环境无依赖跳过、Chrome1440/390px、500片段性能 |
| AC-16 | PASS | 笔记增改删、并发版本冲突、标题/正文/no-op/重复正文、保存失败保留草稿 |
| AC-17 | PASS | 40/41字符、20/21标签、精确大小写/去重/清空、失败原子回滚、资料过滤不限制问答范围、不改语料revision |
| AC-18 | PASS | WAL一致快照：导出期间修改正文/标签仍得到同一旧快照；安全路径/摘要/排除聊天；写失败返回JSON错误，修复后重新导出成功 |

详细方法见[test_acceptance.py](../../test_acceptance.py)、[test_project.py](../../test_project.py)、[test_rag.py](../../test_rag.py)、[DSH回归](../../harness/integration.test.mjs)、[Chrome脚本](../../scripts/verify-ui.mjs)。

## 已修复的边界

1. **模型结果脱敏不完整。** 故障注入重现答案和轨迹对象键泄漏测试Key；现对完整结果递归处理所有字符串与键，取消原8字符门槛。长Key及两字符Key均通过HTTP/历史/轨迹回归。
2. **工具仅发终止信号可能留住进程。** 超时及stdout超限先终止，1秒后仍存活则按PID树强制清理；实际回归覆盖忽视SIGTERM、子孙另开session的情况及下一次调用恢复。

## OpenSpec与尚需配置的部分

[接入教程](../openspec-workflow.md)记录迁入时点；[当前change](../../openspec/changes/close-offline-acceptance-gaps/tasks.md)的10项任务已全部完成（[交付状态](openspec-delivery.json)），管理本轮离线范围，两份行为规格已同步到主规格。正式PRD/Spec/Verify仍在doc/，OpenSpec不替代DSH运行时。

再次检查环境：没有`.env`，生成/图谱Key及Embedding地址均未配置。真实模型完整冒烟和固定20题retrieve/generate、人工事实支持/冲突/拒答复核、真实用量与费用均未执行；unknown字段保持null。补齐配置后按README使用隔离空库运行evaluate.py，达到Verify门槛后才能关闭AC-07与最终交付。

[Verify](../VERIFY.md) · [Tasks](../TASKS.md) · [安装教程](../tutorial.md)

## Linux CI修复与复验

最初e45a083的GraphRAG作业在setup-python缓存步骤失败，未执行测试；默认缓存只查找requirements.txt/pyproject.toml。修复明确指定requirements-rag.lock和requirements-dev.lock，不改应用代码。

修复提交`49e7c820c292ed15e9d85a90c7055f7d9a252942`的5项作业均已成功：Python3.10、Python3.12、Python3.14、DSH及完整GraphRAG。原失败不计为通过；[远端CI记录](ci.json)记录修复后作业、时间及GitHub链接。OpenSpec任务1.2已在此次复验成功后重新关闭。

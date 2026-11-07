# 个人知识管理助手｜RC 实现验收记录

代码已形成可运行的 RC；工程自动化通过，完整产品验收尚未结项。

- 测试代码：`f22e847cef3f5377883e1084c79c81c3a48f6fde`。本记录后续提交只更新文档、截图与证据，不变更应用实现。
- 运行编号：`pka-sdd-clean-f22e847`，2026-11-07，Codex；独立 detached worktree，安装前工作树干净。
- 环境：macOS26.3 Intel，12逻辑CPU、32GB、SSD；Python3.12.9 / SQLite3.47.1 / Node25.3.0；DSH0.2.2-rc.1 / LightRAG1.5.7。使用全新Python虚拟环境和 `npm ci`，依赖来自两份 requirements lock 和 package-lock。
- [机器记录与证据摘要](run.json) · [Python安装](python-install.txt) · [npm安装](npm-install.txt)。测试全部使用临时数据库和虚构资料，未修改日常数据。

## 工程验证

| 实际执行 | 结果 | 证据与范围 |
| --- | --- | --- |
| `python -m unittest -v test_project.py test_rag` | 36通过，0失败，0跳过 | [完整日志](python-tests.txt)；数据/迁移/事务/契约、版本竞态、进程树、真实LightRAG/向量/图后端及DSH接入 |
| `npm run test:harness` | 5通过，0失败，0跳过 | [完整日志](dsh-tests.txt)；真实DSH、空结果改写、无证据、5次请求预算、拒绝第9次工具调用、HTTP→DSH→SQLite |
| `python scripts/run_ui_check.py` | PASS | [浏览器记录](browser-check.txt)、[结果](ui.json)；真实Chrome，图谱原文、来源快照/轨迹、笔记/标签、冲突保留草稿、脚本文本不执行、390px布局 |
| `python scripts/benchmark_local.py` | PASS | [500片段性能原始样本](performance.json)；每项预热5次、测100次，列表/原文/lexical的p95为21.230/20.935/30.381ms，均≤500ms |
| `evaluate.py --mode lexical` | 20题执行完成 | [逐题原始结果](lexical-evaluation.json)；空库导入固定8份资料，不调用模型，不计为L3/L4 |
| Ruff F/I、Git差异空白检查、本地Markdown链接 | PASS | 静态检查无错误，本地文档链接无失效目标；不作为模型效果证据 |

模型服务在L2由本地脚本模拟，Embedding为8维测试向量。使用真实DSH/LightRAG并不意味着测试使用了真实模型权重。费用和语义质量未知项保持null，没有填0或伪造通过率。

## 完整用例的关闭范围

自动测试通过数不等于18项复合用例全部通过。完整用例按Verify定义计数：7项PASS、10项NOT_RUN（部分子场景已验证）、1项BLOCKED。

| 用例 | 当前完整状态 | 已有证据或尚缺部分 |
| --- | --- | --- |
| AC-01 | PASS | 导入/去重、无效正文、字节及片段边界、失败回滚 |
| AC-02 | NOT_RUN | 已测事务、迁移及历史来源；跨重启后的标签/聊天/轨迹整体核对尚未单独闭合 |
| AC-03 | PASS | 缺Key及ready+Key两种lexical均禁止模型/子进程调用；服务计数不变 |
| AC-04 | NOT_RUN | 已测后台构建、失败/CAS拒绝、文件损坏；重复POST与配置维度变化的完整矩阵待补 |
| AC-05 | NOT_RUN | 已测三路/去重/正文重排/假ID/降级；重排遗漏、重复及截断场景未全部覆盖 |
| AC-06 | PASS | 真实DSH schema→错误计划empty→修正两跳completed、原文witness及越界拒绝 |
| AC-07 | BLOCKED | 版本竞态保留轨迹并拒绝旧来源、无工具/假引用已测；真实事实支持、冲突与拒答待L3/L4 |
| AC-08 | NOT_RUN | 已测图谱回查与历史快照；超过裁剪阈值的大图场景待补 |
| AC-09 | NOT_RUN | 已测消息契约、运行中SESSION_BUSY；双会话/级联/旧历史不注入的专项矩阵待补 |
| AC-10 | PASS | 删除失效、当前证据消失、历史保留、片段ID不复用、版本冲突/不存在ID |
| AC-11 | NOT_RUN | 已测服务失败/预算/子孙进程清理；工具和整轮真实截止后的恢复场景待补 |
| AC-12 | PASS | 干净检出安装、停服备份/恢复/清理、忙时拒绝、恢复拒绝覆盖、坏备份拒绝 |
| AC-13 | NOT_RUN | 已测Host/Origin、存储脚本文本、只读数据库/工具范围；提示注入与脱敏专项矩阵待补 |
| AC-14 | NOT_RUN | 已测容量、DSH预算、锁及CAS；所有边界与并发组合未逐项闭合 |
| AC-15 | PASS | 干净环境无跳过、1440/390px浏览器、500片段性能证据 |
| AC-16 | PASS | 笔记CRUD、版本冲突、标题/正文/no-op、重复正文、失败与冲突保留草稿 |
| AC-17 | NOT_RUN | 已测标签原子性、41字符拒绝、20/21上限与筛选；精确大小写/40字符完整边界待补 |
| AC-18 | NOT_RUN | 已测ZIP路径/正文/标签/摘要及排除聊天；并发导出与注入写失败待补 |

这些缺口是尚未验证的行为，不代表已经发现对应功能故障；任务保持待验收，不能自动结为DONE。L3/L4另被真实模型Key和Embedding配置阻塞，20题的retrieve/generate质量、冲突/拒答、真实延时、API用量及费用均未完成。

[正式验收计划](../VERIFY.md) · [任务](../TASKS.md) · [安装教程](../tutorial.md)

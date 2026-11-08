# 使用 OpenSpec 管理个人知识管理助手

OpenSpec 固定为 **1.16.1**，仅用于开发流程。应用运行时的 Harness 继续使用 DSH；安装后执行 `npm ci` 即可获得项目内CLI，无需修改全局工具。

参考：[OpenSpec官方仓库](https://github.com/Fission-AI/OpenSpec)、[既有项目接入](https://github.com/Fission-AI/OpenSpec/blob/main/docs/existing-projects.md)、[CLI](https://github.com/Fission-AI/OpenSpec/blob/main/docs/cli.md)。接入方式是从真实变更逐步积累规格，原有产品文档继续保留。

## 文件职责

| 位置 | 职责 |
| --- | --- |
| doc/PRD.md | 产品目标、范围与优先级 |
| doc/SPEC.md、doc/openapi.json | 完整实现基线、数据与接口约束 |
| doc/VERIFY.md、doc/verification/ | 验收标准、实际结果、脱敏证据与测试提交 |
| doc/TASKS.md | 首版任务总体状态 |
| openspec/config.yaml | 技术栈、中文规则、既有文档与证据要求 |
| openspec/changes/<change>/ | proposal、增量specs、design、tasks；记录本次改变 |
| openspec/specs/ | 经变更同步的当前行为规格，逐次积累 |
| .agents/skills/openspec-*/ | 官方生成的项目内Codex工作流 |

`doc/`保持统一文档目录，不恢复`docs/`；`openspec/`采用工具标准结构，存放开发工作流与增量规格。修改行为时先更新本change规格及相关doc契约，再实施并验证，避免两套描述相互矛盾。

## 常用命令

```bash
npm ci
npm run spec:status
npm run spec:validate
npm exec -- openspec status --change close-offline-acceptance-gaps --json
npm exec -- openspec instructions apply --change close-offline-acceptance-gaps --json
```

创建后续变更：

```bash
npm run spec:change -- add-document-collections
npm exec -- openspec instructions proposal --change add-document-collections --json
```

CLI提供模板和状态，不会自动替你生成需求或实现代码。Codex可调用项目内的`openspec-propose`、`openspec-apply-change`、`openspec-update-change`、`openspec-sync-specs`及`openspec-archive-change`技能。新需求先完成提案再进入实施；既有授权任务迁入时记录实际先后，不伪造“全部代码都在OpenSpec之后开发”的历史。

如需禁用CLI匿名遥测，可在运行环境设置`OPENSPEC_TELEMETRY=0`。CI已设置，不改变用户全局配置。

## 首轮接入记录（历史）

首轮change：`close-offline-acceptance-gaps`。

- 原因：RC的10项复合验收已有部分证据，需要补齐离线可验证场景。
- 迁入时点：已按原doc基线补写部分测试，发现模型结果脱敏缺口并实施修复之后，用户提出采用OpenSpec。
- 行为规格：模型输出安全、工具进程生命周期；其余沿用现有FR/NFR/AC，避免把纯补测伪装成新功能。
- 实施检查：重启/回滚、标签/并发、索引/重排、大图/历史引用、超时清理、会话隔离、只读权限及导出。
- 真实服务：Key与Embedding未配置，AC-07及L3/L4不会因为本change通过而被改为PASS。

详细任务见[当前change](../openspec/changes/close-offline-acceptance-gaps/tasks.md)，最终结果见[验收记录](verification/REPORT.md)。

## 什么才算完成

任务checkbox仅在实际实现、断言和证据均满足时勾选。`openspec status`的artifact完成表示文件存在，`openspec validate`验证格式和规则，二者都不能证明产品正确。CI同时执行OpenSpec严格校验、Python回归和真实DSH/模拟服务集成；浏览器证据另行运行。

完成后将增量规格同步到主规格，归档只针对已完成且范围明确的change。真实模型固定20题是独立验收门槛，缺配置就保留BLOCKED，不用脚本模型结果替代。

CI的pip缓存通过`cache-dependency-path`显式引用`requirements-rag.lock`与`requirements-dev.lock`，不依赖默认的requirements.txt查找规则。

## 真实服务验收变更

当前change为[verify-real-model-quality](../openspec/changes/verify-real-model-quality/tasks.md)。用户授权配置、真实测试与启动后，先记录配置/验证范围；实际发现DSH启动目录问题和未经支持的排他推断后，先补行为增量与doc/SPEC，再实施修复并同步主规格。失败与最终复验分别留存，固定20题达到门槛后才关闭AC-07、T07/T10。详见[真实报告](verification/REAL-REPORT.md)。Key只在本地，所有提交前扫描暂存内容；开发工具不会代替这一检查。

# Proposal

## Why

RC 已实现核心功能，但完整验收仍有10项仅覆盖部分场景。需要补齐可离线复现的证据，修复新增测试暴露的凭据脱敏与工具终止边界，并用 OpenSpec 管理后续迭代。

## What Changes

- 将当前已获授权的离线验收工作迁入 OpenSpec；保留 doc/PRD.md、SPEC.md、VERIFY.md 和 OpenAPI 的职责，新增面向 Codex 的工作流和开发说明。
- 修复模型结果脱敏：答案、来源、元数据及轨迹的字符串值和对象键在返回/落库前移除已配置的非空 API Key，包括短 Key。
- 修复工具生命周期：75秒或100,000字节 stdout 上限触发终止，收到终止信号后1秒仍未结束则强制清理子孙进程；保留5次模型请求/8次工具调用及整轮150秒预算。
- 增补重启、回滚、标签边界、并发构建/写入、异常重排、超时恢复、只读权限、会话隔离、导出快照/失败、大图及历史引用的验收。
- 如实记录迁入时点：用户提出 OpenSpec 前，已经按 doc/ 的既有 SDD 基线新增部分测试并修复上述两处边界；此次不把它们伪称为由 OpenSpec 从零发起的开发。

本次不更换应用 Harness，不变更 HTTP 接口，不转换整份 PRD，不执行缺少真实服务配置的 L3/L4，也不以脚本模型证明真实问答质量。

## Capabilities

### New Capabilities

以下为首次纳入 OpenSpec 的行为领域，承接现有 NFR，而非新增用户功能：

- `model-output-safety`：模型结果进入 HTTP/SQLite 前的凭据脱敏与证据边界，对应 NFR-02、AC-07/AC-13。
- `tool-process-lifecycle`：只读工具、工具与整轮预算、进程树清理及恢复，对应 NFR-01/03/04、AC-11/AC-14。

### Modified Capabilities

无既有 OpenSpec 主规格；当前主规格目录尚为空。

## Impact

- 实现：model_client.py、retrieval.py、harness/knowledge-plugin.mjs。
- 验收：test_acceptance.py、test_project.py、test_rag.py、harness/integration.test.mjs、浏览器脚本与 CI。
- 管理：OpenSpec 1.16.1 固定为开发依赖，生成项目内 Codex skills；不成为应用运行依赖。
- 产物：增量需求/设计/任务、doc/ 下教程和脱敏证据；真实服务及最终产品质量验收继续保留为待完成。

# Proposal

## Why
已有L1/L2工程验收通过，真实模型质量仍待验证。用户已授权配置DeepSeek、测试并启动应用，同时明确禁止将API Key提交GitHub。

## What Changes
- 沿用AC-07及L3/L4冻结门槛，连接真实DeepSeek与本地BGE-M3。
- 隔离数据库运行8份语料、20题，保存脱敏结果并逐题核对。
- 发现实现缺陷时先记录契约影响，再修复、复验；不降低门槛。
- 完成后启动日常应用，提交前扫描暂存区，凭据仅存本地。

## Capabilities
### New Capabilities
无。配置、既有验收执行与报告，不引入新需求。
### Modified Capabilities
- tool-process-lifecycle：隔离DSH进程启动目录，由Python显式加载并继承应用配置，避免SDK重新读取项目.env。

## Impact
本地.env、Ollama缓存、隔离测试库不提交；报告在doc/verification，状态同步doc/VERIFY与TASKS。不创建正式Release。迁入前已完成本地凭据保存、接口连通和Embedding下载，未虚构事前审批。

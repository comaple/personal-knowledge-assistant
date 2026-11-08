# tool-process-lifecycle Specification

## Purpose

约束个人知识助手可调用工具的权限与执行生命周期，使无响应、超量输出或忽视终止信号的子进程能够被结束，避免占住会话锁或留下后台进程，并确保故障后仍能继续检索和管理资料。

## Requirements

### Requirement: Expose only bounded read-only knowledge tools
The system SHALL 仅暴露四项既有知识工具，不接受模型提供的SQL、终端命令、任意数据库路径或写入操作；每轮最多5次模型请求和8次知识工具执行。关联 NFR-01、AC-13/AC-14。

#### Scenario: Model requests a shell command after retrieving injected instructions
- **WHEN** 模型尝试调用白名单外的shell工具，或向知识工具注入SQL/路径/写入参数
- **THEN** 调用被拒绝且不执行命令、不修改资料

#### Scenario: Agent exceeds request or tool budgets
- **WHEN** 本轮将超过5次模型请求或8次知识工具执行
- **THEN** 额外请求或执行被拒绝，并报告预算耗尽，不伪称任务完成

### Requirement: Terminate expired or oversized tool processes
The system SHALL 对工具桥施加75秒及100,000字节stdout限制，触发限制即终止；1秒后仍未退出则强制清理其进程树，包括创建了新进程会话的子孙进程。关联 NFR-03/04、AC-11/AC-14。

#### Scenario: Tool ignores graceful termination
- **WHEN** 工具达到截止时间且忽视终止信号，子孙进程处于独立进程会话
- **THEN** 系统强制清理整棵进程树，调用返回失败并释放执行资源

#### Scenario: Tool produces oversized output
- **WHEN** 工具stdout超过100,000字节
- **THEN** 系统拒绝该输出并终止工具，而不是将不完整JSON当作成功结果

### Requirement: Enforce outer deadline and recover after failure
The system SHALL 在整轮150秒外层截止后清理DSH及其子孙进程，将失败落库并释放问答锁；检索子进程保留45秒截止。关联 FR-11、AC-11/AC-14。

#### Scenario: Entire turn expires and a later turn runs
- **WHEN** 本轮达到外层截止时间
- **THEN** HTTP返回结构化TIMEOUT，历史保存失败状态，进程树结束
- **AND** 后续本地检索及会话删除可正常执行

#### Scenario: Query subprocess expires
- **WHEN** 图谱或向量查询子进程超过截止时间
- **THEN** 系统清理查询进程并明确降级状态；服务失败不得等同于正常无证据

### Requirement: Isolate runtime bootstrap configuration
The system SHALL 由应用白名单加载本地.env并继承配置，将DSH实际启动目录置于本轮临时home，保持知识工具绝对路径可用；DSH不得重复解析应用项目的.env。关联FR-07、FR-11、AC-07、AC-11、AC-12。

#### Scenario: Application dotenv contains a DSH bootstrap setting
- **WHEN** 项目.env包含DSH_BASE_URL，应用已经加载配置并设置受信任继承环境
- **THEN** DSH能启动、调用知识工具并返回引用回答，不因CLI重新读取.env而退出
- **AND** API Key不写入临时patch、日志或提交文件

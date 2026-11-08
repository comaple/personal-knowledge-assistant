# model-output-safety Specification

## Purpose

确保外部模型返回的内容在进入个人知识助手页面和持久化历史之前遵守凭据与出处边界，覆盖服务诊断中意外返回凭据的情况，并明确工程校验与真实模型事实支持评估各自能够证明的范围。

## Requirements

### Requirement: Redact configured credentials before delivery and persistence
The system SHALL 在模型结果返回HTTP或写入历史前，移除所有已配置的非空API Key，包括短Key；覆盖答案、来源、元数据、轨迹的字符串值和对象键。关联 NFR-02、AC-13。

#### Scenario: Provider echoes credentials in multiple fields
- **WHEN** 测试服务在答案、嵌套轨迹值及对象键中返回配置的Key
- **THEN** 本轮HTTP响应、历史消息和轨迹接口均不包含该Key原文
- **AND** 长Key及两字符Key均适用

### Requirement: Require successful evidence retrieval and legal citations
The system SHALL 仅在本轮成功执行检索且生成引用均来自本轮证据时接受generated结果；工程校验不替代真实模型事实支持验收。关联 FR-07、AC-07。

#### Scenario: Model omits retrieval or invents a citation
- **WHEN** 模型不调用检索、检索仅返回错误或引用未返回的来源ID
- **THEN** 系统拒绝将其作为成功生成结果，并记录结构化失败

#### Scenario: Sources change while a turn is running
- **WHEN** 本轮开始后相关语料revision发生变化
- **THEN** 最终结果清空sources并返回CORPUS_CHANGED，不能保持generated
- **AND** 已收到的脱敏轨迹仍可供诊断

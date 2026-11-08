## ADDED Requirements

### Requirement: Keep generated claims within necessary supported facts
The system SHALL 在DSH提示中要求只回答问题所需的原文事实，禁止将资料未提及推断为事实不存在，或将某人负责一项工作推断为不负责其他工作；数值计算应标明推算。真实效果必须按固定题集逐题核验，提示存在本身不是质量通过证据。关联FR-07、AC-07及L4事实支持门槛。

#### Scenario: Evidence assigns roles without excluding other duties
- **WHEN** 资料仅说明甲负责归档、验收联系乙，问题询问验收联系方及材料
- **THEN** 回答提供乙与所需材料及出处，不追加“甲不负责验收”这类未经原文明示的排他结论

#### Scenario: Fixed questions expose an unsupported claim
- **WHEN** 实际模型输出包含未受原文支持的事实
- **THEN** 本轮质量验收标为未通过，保留失败案例并在修复后重新运行完整生成题集

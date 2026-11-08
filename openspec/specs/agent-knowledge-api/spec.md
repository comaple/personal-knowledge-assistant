# agent-knowledge-api Specification

## Purpose

为运行在同一台机器上的Agent和代码智能体提供明确、可复制的知识资料新增与无会话检索入口，复用已有来源与检索状态契约，保持本机Host和Origin边界，不把模型凭据、任意文件路径或SQL执行能力暴露给调用方。

## Requirements


### Requirement: Expose local agent knowledge operations
The system SHALL 提供无需聊天会话的POST /api/knowledge/search，返回原文来源和检索状态；支持lexical及retrieve，保留既有本机Host/Origin与输入校验。Agent通过既有POST /api/documents新增资料，不获得任意SQL/路径/服务Key。GET /api/openapi.json提供无凭据契约。关联FR-17、AC-20。

#### Scenario: Agent saves and retrieves knowledge
- **WHEN** 本机客户端按API页示例新增TXT/MD资料并执行lexical检索
- **THEN** 无需session_id和模型Key即可取得来源；retrieve会如实报告智能索引覆盖/降级

#### Scenario: Client submits an unsupported operation or foreign origin
- **WHEN** 请求注入SQL/路径/未知字段、超长query或跨来源写入
- **THEN** 拒绝请求，不扩大权限，不暴露凭据

### Requirement: Provide copyable agent integration instructions
The system SHALL 在API导航中显示当前本机地址、写入/检索/构建示例和OpenAPI链接，说明只在本机提供访问、内容与模型调用范围及错误处理，不展示服务器Key。

#### Scenario: User opens API navigation
- **WHEN** 用户打开API页
- **THEN** 能复制完整URL和有效JSON/curl示例，区分本地检索与可能调用模型的retrieve

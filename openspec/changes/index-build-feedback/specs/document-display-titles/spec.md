## ADDED Requirements

### Requirement: Generate imported display titles from bounded excerpts
The system SHALL 在导入请求auto_title=true时用正文前两段和末一段生成简短显示名；重叠段不重复，每段最多1200字符，少于三段按已有内容处理。原文不改变，不为默认的API写入或无Key导入强制调用模型；界面默认开启可关闭，并说明摘要外发范围及费用。关联FR-18、AC-21。

#### Scenario: Import has sufficient paragraphs
- **WHEN** 用户导入含多个段落的资料并开启AI命名
- **THEN** 模型只收到限定摘录；成功后更新显示名，用户仍可手动编辑；正文、切块身份和语料revision不因改名额外变化

#### Scenario: Naming fails or a document is concurrently edited
- **WHEN** 无凭据、模型失败、返回非法标题或导入后文档已变化
- **THEN** 导入资料不丢失，保留原名或用户最新编辑，返回可见命名状态，不泄漏原始供应商错误/Key

#### Scenario: Content already exists
- **WHEN** 相同正文再次导入
- **THEN** 不新增资料、不重复命名或覆盖现有显示名

#### Scenario: Rename an existing imported document
- **WHEN** 用户在已有导入资料行点击AI命名
- **THEN** 同样仅发送首两段/末一段摘录，保留原文并以乐观版本更新显示名，失败保留已有名称

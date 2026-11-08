## ADDED Requirements

### Requirement: Report observed build progress
The system SHALL 报告准备、处理、校验发布及终态；片段完成计数来自本轮真实LightRAG处理状态，范围为0至本轮总片段数。未知完成比例时使用不定进度，不以耗时估算整体百分比；只有索引校验与原子发布成功后报告完成。关联FR-04、FR-11、AC-04/AC-11。

#### Scenario: Build is running
- **WHEN** 用户启动有效构建
- **THEN** POST返回running，轮询得到阶段、完成片段数及总数；再次启动仍拒绝INDEX_BUSY
- **AND** 处理进度完成不代表索引已经发布

#### Scenario: Build fails or times out
- **WHEN** 构建失败、超时或语料已变化
- **THEN** 显示失败及可执行下一步，停止成功动画，不误报完成；旧代和用户资料不受破坏
- **AND** 不把供应商原始诊断、正文或凭据输出到进度消息

### Requirement: Keep build feedback visible and recover polling
The system SHALL 在桌面与390px页面主区域显示构建阶段、进度条和经过时间；切换功能或刷新后继续显示正在运行任务。临时轮询失败不得当作构建失败，应提示连接问题并继续重试；成功后提供图谱与问答入口。关联FR-08、NFR-05、AC-08/AC-15。

#### Scenario: User refreshes or switches tabs during a build
- **WHEN** 后台任务仍运行而用户刷新、切换功能或使用手机页面
- **THEN** 主区域仍能看到构建进度、经过时间和保持应用运行的提示，构建按钮禁用

#### Scenario: Polling disconnects temporarily
- **WHEN** 一次索引状态请求失败
- **THEN** 保留已知进度，显示暂时无法更新并自动重试；恢复后显示后台真实状态

### Requirement: Explain prerequisites and next actions
The system SHALL 就地说明先保存资料、本地关键词无需构建或Key、智能功能需要模型与向量服务以及构建步骤；模式选择时解释当前可用性、调用费用和结果如何使用。正文变化或删除导致过期时明确重建，空库或配置不足给出具体下一步。关联FR-03/04/05/07/11、AC-03/04/11/15。

#### Scenario: User chooses a mode before building
- **WHEN** 索引未构建或过期
- **THEN** 本地关键词仍可用；智能模式提示当前会降级及先构建/重建的建议，不虚称模型已连通

### Requirement: Build individual documents incrementally
The system SHALL 接受可选document_id，只处理选中文档新增/变化的片段，并复用同配置下已构建且仍有效的片段；原子发布包含这些已构建资料的索引。未构建资料仍可关键词检索，页面/API明确智能通道的覆盖范围。关联FR-16、AC-19。

#### Scenario: Build two documents one at a time
- **WHEN** 两份资料分别完成单篇构建
- **THEN** 第一份未变化的片段不重复向模型抽取，第二份完成后两份均参与向量/图谱检索，单篇进度绑定document_id

#### Scenario: An indexed document changes or is deleted
- **WHEN** 原文变化/删除后执行单篇或全库构建
- **THEN** 不复用失效片段或旧来源，保持最终revision检查；旧完整索引格式可继续读取

### Requirement: Skip up-to-date documents in a global build
The system SHALL 在总构建操作中只处理尚未构建、正文变化或配置失效的资料；已有内容复用。全部就绪时返回HTTP200的noop Job及“没有新文档可构建”，不启动子进程或调用模型。删除资料后的必要索引清理可单独发布，不重复抽取未变正文。

#### Scenario: All documents are already indexed
- **WHEN** 总构建被点击且当前全部资料与已发布索引一致
- **THEN** 提示没有新文档可构建，noop=true，无新任务、无模型请求

#### Scenario: New or edited documents exist
- **WHEN** 部分资料未构建或正文已变
- **THEN** 仅这些资料进入处理计数和模型抽取，已有有效资料继续复用

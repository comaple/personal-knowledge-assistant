## ADDED Requirements

### Requirement: Isolate runtime bootstrap configuration
The system SHALL 由应用白名单加载本地.env并继承配置，将DSH实际启动目录置于本轮临时home，保持知识工具绝对路径可用；DSH不得重复解析应用项目的.env。关联FR-07、FR-11、AC-07、AC-11、AC-12。

#### Scenario: Application dotenv contains a DSH bootstrap setting
- **WHEN** 项目.env包含DSH_BASE_URL，应用已经加载配置并设置受信任继承环境
- **THEN** DSH能启动、调用知识工具并返回引用回答，不因CLI重新读取.env而退出
- **AND** API Key不写入临时patch、日志或提交文件

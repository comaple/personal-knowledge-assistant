# DSH Harness 接入教程

对应本项目 1.0.0-rc.1，实际使用 `@deepseek-ai/dsh-sdk-client` 的 `DeepSeekHarness`。DSH、sdk-client、tools 固定0.2.2-rc.1，Node≥24.9.0。

## 01｜调用链

浏览器提问 → Python app/model_client → Node runner → DSH SDK运行时 → 知识工具 → Python只读桥 → SQLite/图谱 → DSH最终回答 → Python验收和事务保存。

Python没有手写替代Agent循环。每问创建临时home，实际`processCwd`也设为该目录，逻辑`cwd`仍指向项目根；使用 `sdk-minimal`，注入官方Messages适配器、提示词和知识插件；run结束后close并清理home。应用自己的会话、来源与SDK轨迹存SQLite。

## 02｜实际文件

| 文件 | 作用 |
| --- | --- |
| [model_client.py](../model_client.py) | Node子进程、150秒预算、进程树清理、结果/引用验收、轨迹脱敏 |
| [runner.mjs](../harness/runner.mjs) | DeepSeekHarness配置、run/close、工具结果来源合并及检索元数据 |
| [knowledge-plugin.mjs](../harness/knowledge-plugin.mjs) | 从OpenAPI契约适配SDK的schema DSL、注册四个工具、默认工具禁用/白名单、5步与8调用预算 |
| [kb_bridge.py](../harness/kb_bridge.py) | 独立参数校验、固定操作、SQLite mode=ro；模型不能传DB路径/命令 |

OpenAPI的标准JSON Schema不能直接假定SDK全支持；插件解析本地$ref，将nullable转为SDK支持的oneOf，将required数组适配为字段标记。长度/数量等边界仍由Python独立验证。

## 03｜版本和安全边界

服务端在开始时固定revision/generation/profile，经可信配置传入工具桥，不属于模型参数。每个工具检查版本；最终写事务再检查，资料变化则保存错误和已收到轨迹，拒绝旧证据。

默认终端/文件工具被禁用，实际工具注册表只允许kb_search、kb_list_documents、kb_graph_schema、kb_graph_query。图计划有界且只读；资料里的指令不会扩大工具权限。

本轮必须有成功的kb_search结果才可验收回答；引用ID由工具生成。来源合法不代表事实语义正确，质量测试仍需人工核对。

## 04｜复现链路

安装Node依赖后运行 `npm run test:harness`；测试使用真实SDK和本地脚本Messages服务，不调用付费模型。覆盖检索改写、无证据、5步限制、9次工具尝试时拒绝第9次，以及HTTP→SDK→SQLite轨迹持久化。

图谱多跳流程用 `python -m unittest -v test_rag` 验证，模型/8维向量仍为模拟服务。真实模型验证另见 [Verify](VERIFY.md)，不能从模拟脚本输出推断实际模型能力。

## 05｜真实配置暴露的启动问题

应用由`settings.load_env`白名单加载本地`.env`，再将配置通过进程环境传给Node。DSH CLI会先检查实际启动目录里的`.env`；如果再次读取项目文件，会拒绝`DSH_BASE_URL`这类只能由启动环境设置的变量，即使已经继承了同名环境变量也会报错。

因此`DeepSeekHarness`同时设置`dshHome: input.home`、`processCwd: input.home`及`cwd: root`，patch和插件路径使用绝对路径。这样凭据保留在应用本地配置中，SDK启动只继承受信任环境，不需要删除或改写用户配置。新增隔离项目回归在修复前失败、修复后通过；现有工具权限与历史隔离也继续验证。

`.env`不得提交GitHub；本机权限为0600。提交前从本地配置读取Key，对全部暂存文件做精确匹配扫描，结果只显示通过/失败，不输出Key。不能用实际Key替代测试夹具里的假凭据。

真实模型还暴露了“负责归档→不负责验收”的错误推断。提示词现在要求只回答必要事实，不从缺失信息或职责分工推出否定结论；最终效果仍须逐题核对，不能凭提示词存在判通过。

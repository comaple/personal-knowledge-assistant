# DSH Harness 接入教程

对应本项目 1.0.0-rc.1，实际使用 `@deepseek-ai/dsh-sdk-client` 的 `DeepSeekHarness`。DSH、sdk-client、tools 固定0.2.2-rc.1，Node≥24.9.0。

## 01｜调用链

浏览器提问 → Python app/model_client → Node runner → DSH SDK运行时 → 知识工具 → Python只读桥 → SQLite/图谱 → DSH最终回答 → Python验收和事务保存。

Python没有手写替代Agent循环。每问创建临时home，使用 `sdk-minimal`，注入官方Messages适配器、提示词和知识插件；run结束后close并清理home。应用自己的会话、来源与SDK轨迹存SQLite。

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

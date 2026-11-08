# 构建进度、文档增量构建、Agent API与AI命名验收

2026-11-08，执行：Codex。源文件工作树快照SHA-256见[运行清单](build-run.json)，本地测试数据均为隔离虚构资料。没有把用户的3份资料混入测试；日常数据库重启前后逐行转储摘要完全一致，并在忽略目录保存本机备份。Key仅存本地0600 .env，每次提交前对暂存区精确扫描，不记录Key。

## 实现结果

- 主区域显示真实构建阶段、已处理/待处理片段数、耗时及下一步；手机、切换功能、刷新后仍可见。未观测比例及校验发布阶段为不定进度，不显示虚构整体百分比；连接中断保留状态、3秒重试。
- 每份文档有“构建本文图谱与向量”。复用经文件摘要校验的有效旧代，删除失效后端片段，只插入未processed内容，发布仍检查全局revision。manifest v3可覆盖完整文档的子集，v2继续按全覆盖验证；页面/API说明智能通道覆盖范围，关键词始终可查全库。
- 总按钮“构建待处理文档”只处理新增、正文变化或模型配置失效的内容。全部就绪返回200、noop=true，提示没有新文档可构建，不启动进程；删除后的必要清理可进行零新增内容发布。
- API导航含完整可复制curl与OpenAPI下载。POST /api/knowledge/search支持无需聊天会话的lexical/retrieve；新增复用POST /api/documents。只监听本机、沿用Host/Origin及字段限制，不提供公网认证。
- 导入默认AI命名，可关闭；已有导入资料行有AI命名按钮。模型仅接收首两段和末一段摘录，每段≤1200字符，重叠段不重复；成功只改显示名，失败保留资料/名称，乐观版本避免覆盖并发编辑。API写入默认不命名，显式auto_title=true开启；已有资料可POST /api/documents/{id}/title。

## 实际检查

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 完整Python存储/API/LightRAG/增量/命名/故障回归 | 59通过、0失败、0跳过 | [日志](build-python-tests.txt) |
| DSH运行时与权限/预算/清理回归 | 10通过、0失败、0跳过 | [日志](build-dsh-tests.txt) |
| 原有Chrome流程 | 7项通过，1440/390px | [日志](build-existing-ui.txt)；当前ui.json及截图按新界面更新 |
| 新构建/Agent页面Chrome验证 | 4组通过 | [结果](build-ui.json)、[日志](build-browser-tests.txt) |
| 真实DeepSeek+BGE-M3隔离冒烟 | 命名、单篇、补齐、无新增、Agent多路检索全部通过 | [逐步记录](build-real-smoke.json) |
| OpenAPI标准校验与OpenSpec严格校验 | 通过，主规格同步后8项OpenSpec | [OpenSpec记录](build-openspec.txt) |
| 日常服务重启及真实页面 | 通过，3文档/13片段及完整SQL摘要不变 | [数据保留](build-startup.json)、[浏览器](build-live-browser.json) |

Chrome新流程对构建阶段/临时断网使用确定性状态响应，验证UI不会提前完成、能恢复轮询；实际后端处理与计数另由真实LightRAG、模拟服务及真实服务冒烟验证。没有把模拟状态当成真实模型质量分数。无新增测试通过补丁禁止Popen，证明无进程启动；待处理计数和不重复抽取由隔离后端测试验证。

真实服务仅使用两份虚构资料：首次单篇后coverage=partial，再总构建只处理剩余1段后coverage=complete；再次总构建HTTP200/noop；Agent retrieve实际为hybrid且返回双方来源。AI拟名“青松摄影课开课信息”与摘录一致。真实用量与完整费用未采集，未知，不填0。

本轮没有重跑此前固定20题语义评审，不把新冒烟当成20题成绩；历史20题绑定4290fa0，见REAL-REPORT.md。当前新增AC-19—AC-21通过，相关工程回归如上。UI图“build-progress-mobile.png”使用明确的测试状态与虚构资料，非用户资料。

## 使用

刷新 http://127.0.0.1:8501 。资料行可构建本文或AI命名，总按钮补齐待处理文章，API导航提供接入示例。构建期间保持应用运行、暂不要修改正文；本地搜索无需构建。更详细步骤见[教程](../tutorial.md)。

# Personal Knowledge Assistant

- 项目名称：个人知识管理助手。正式文档统一在doc/，不要创建docs/。
- 开发使用OpenSpec：先读取openspec/config.yaml及当前change；PRD、完整技术规格、OpenAPI和验收基线仍在doc/。用CLI确认root、status和instructions，不凭artifact存在宣布通过。
- 当前change为index-build-feedback。开始后续变更前确认该change状态，避免将无关工作混入。
- SQLite是原文事实源；DSH是运行时Harness，OpenSpec只是开发工具。四项知识工具只读，默认lexical不调用模型。
- 对用户已经授权的开发持续完成；迁入管理工具不撤销既有授权。如实记录迁入前的工作，不补造审批或开发历史。
- 测试使用隔离临时数据库；模拟服务测试与真实模型L3/L4分开。报告绑定实际提交；未执行/缺凭据/未知费用不得标通过或填0。
- 不将.env、API Key、真实个人资料库或未脱敏模型诊断提交Git。修改前保留现有未提交工作。
- 验证入口：npm run spec:validate；python -m unittest -v test_project.py test_rag test_acceptance；npm run test:harness。浏览器入口见doc/openspec-workflow.md及README。

# CX Notifier 工程约定

## 目录职责

- `plugins/cx-plugin/`：Codex 与 Claude Code 共享的插件实现。
- `scripts/`：安装、渠道配置和公开发布检查脚本。
- `tests/`：发布层安装与配置测试；Windows 专属验收脚本使用 `windows-*.ps1` 命名。
- `.github/workflows/`：GitHub Actions 自动质量门禁，只放小写短横线命名的 YAML 工作流。
- `ROADMAP.md`：记录已经实现并验证的版本进度，未验证事项必须明确标为待验收。

## 修改与验证

- Windows 安装改动至少运行发布层测试、插件测试、PowerShell 语法解析和 Windows Hook 启动验收。
- CI 不保存或输出 Webhook、签名密钥等凭据；云端测试只使用假的客户端命令和非真实配置。
- 自动门禁只能证明其实际执行范围，不能替代真实飞书机器人、人工交互或 Windows 用户验收。
- 完成独立变更后更新 `ROADMAP.md`，确认提交只包含本次相关文件再提交。

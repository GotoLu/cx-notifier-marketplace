# ROADMAP

## 当前阶段

`0.6.0` Windows 一键安装与原生 Hook 已 squash 合入 `main` 并通过主分支 Windows 自动门禁，等待版本发布确认；真实飞书机器人由后续 Windows 用户验收。

## 已完成

- `0.5.2`：Codex/Claude Code 共享通知 Hook、飞书/企微/钉钉/Webhook 渠道和统一安装器。
- Windows PowerShell 一键安装与飞书配置。
- Windows 原生 Codex Hook 启动适配。
- Windows Python 控制台、子进程和非交互输入的 UTF-8 兼容修复。
- GitHub Actions `windows-latest` 安装与 Hook 启动验收。
- `0.6.0` manifest、README 和 Changelog 同步。
- 插件、安装器、隐私与结构自动门禁通过。

## 进行中

- Windows 真实 `Stop` 飞书机器人通知验收。
- `0.6.0` 标签与 Release 发布确认。

## 待办

- 创建并推送 `v0.6.0` 标签，发布对应 Release。

## 阻塞

- 无代码阻塞；公开发布需主人最终确认。

## 最近验证

- 2026-07-24：发布层安装器测试 13/13 通过。
- 2026-07-24：插件测试 65/65 通过，包含真实回环 HTTP、重定向和超时路径。
- 2026-07-24：公开发布隐私门禁通过。
- 2026-07-24：Codex 插件结构验证通过。
- 2026-07-24：`git diff --check` 通过。
- 2026-07-27：GitHub Actions Windows 安装验收通过（run `30234512595`）。
- 2026-07-27：Windows PowerShell 安装器、Codex 安装命令链和 `cmd.exe` Hook 启动通过。
- 2026-07-27：Windows 发布层测试 13/13、插件测试 65/65、公开发布检查通过。
- 2026-07-27：`main` squash 合并提交 `caff6b0`，合并后 Windows Actions 通过（run `30234870402`）。

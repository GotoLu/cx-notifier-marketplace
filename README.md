# Codex Notifier Marketplace

这是 `cx-plugin` 的公开分发仓库。插件在 Codex 请求权限或结束当前回复时，向飞书、企业微信群机器人或通用 HTTPS Webhook 发送单向提醒。

## 安装

```bash
codex plugin marketplace add GotoLu/cx-notifier-marketplace
codex plugin add cx-plugin@cx-notifier
```

安装后请新建一个 Codex 任务，使 Hook 使用新安装的版本。具体配置见 [插件说明](plugins/cx-plugin/README.md)。

## 发布前检查

```bash
python3 scripts/check_public_release.py
python3 -m unittest discover -s plugins/cx-plugin/tests -v
```

隐私检查会拒绝本地配置、缓存、数据库、日志、私钥、常见真实凭据形状、个人主目录和非示例 URL。测试中用于验证脱敏的固定假凭据有精确白名单，不会放宽其他文件的检查。

## 隐私边界

- 仓库只包含 `config.example.json`，不包含实际 `config.json`。
- Webhook、签名密钥和 Bearer Token 应通过环境变量或用户本机的 `0600` 配置文件提供。
- 插件不会上传 transcript、原始工具输入、shell 命令、diff 或终端输出。
- 任务简介会限制长度，并对常见路径和凭据形状进行脱敏。

安全问题请参考 [SECURITY.md](SECURITY.md)。本项目使用 [MIT License](LICENSE)。

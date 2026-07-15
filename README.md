# Codex / Claude Code Notifier Marketplace

这是 `cx-plugin` 的公开分发仓库。插件在 Codex 或 Claude Code 请求权限、结束当前回复时，向飞书、企业微信群机器人或通用 HTTPS Webhook 发送单向提醒。

## Codex 安装

```bash
codex plugin marketplace add GotoLu/cx-notifier-marketplace
codex plugin add cx-plugin@cx-notifier
```

安装后请新建一个 Codex 任务，使 Hook 使用新安装的版本。

## Claude Code 安装

```bash
claude plugin marketplace add GotoLu/cx-notifier-marketplace
claude plugin install cx-plugin@cx-notifier
```

安装后运行 `/reload-plugins` 或新建 Claude Code 会话。具体配置见 [插件说明](plugins/cx-plugin/README.md)。

## 飞书机器人快速配置

1. 在接收通知的飞书群中打开“群设置” → “群机器人” → “添加机器人” → “自定义机器人”。
2. 设置机器人名称并完成添加，复制飞书生成的 Webhook 地址。
3. 建议在机器人的安全设置中启用“签名校验”，并复制签名密钥。若不启用签名校验，后续配置时省略 `--secret-prompt`。
4. 克隆本仓库并进入插件目录：

   ```bash
   git clone https://github.com/GotoLu/cx-notifier-marketplace.git
   cd cx-notifier-marketplace/plugins/cx-plugin
   ```

5. 通过隐藏输入保存 Webhook 和签名密钥，然后发送测试消息：

   ```bash
   python3 scripts/configure.py init
   python3 scripts/configure.py add --type feishu --name feishu-main \
     --webhook-prompt --secret-prompt
   python3 scripts/configure.py validate
   python3 scripts/configure.py test --channel feishu-main
   ```

命令不会在终端回显 Webhook 或密钥，配置默认保存在 `~/.config/cx-plugin/config.json` 并限制为当前用户可读写。需要每条通知都 `@所有人` 时，可在 `add` 命令末尾增加 `--mention-all`。完整说明和故障排查见[飞书机器人配置](plugins/cx-plugin/README.md#飞书机器人配置从零开始)。

## 仓库结构

```text
.agents/plugins/marketplace.json     # Codex marketplace
.claude-plugin/marketplace.json      # Claude Code marketplace
plugins/cx-plugin/                   # 两个平台共享的插件实现
  .codex-plugin/plugin.json          # Codex 元数据
  .claude-plugin/plugin.json         # Claude Code 元数据
  hooks/                             # 共享 Hook 配置与 Python 入口
  scripts/                           # 本地配置工具
  tests/                             # 自动化测试
scripts/check_public_release.py      # 发布隐私与结构检查
```

## 发布前检查

```bash
python3 scripts/check_public_release.py
python3 -m unittest discover -s plugins/cx-plugin/tests -v
```

隐私检查会拒绝本地配置、缓存、数据库、日志、私钥、常见真实凭据形状、个人主目录和非示例 URL，并校验两套 marketplace 与插件清单保持一致。测试中用于验证脱敏的固定假凭据有精确白名单，不会放宽其他文件的检查。

## 隐私边界

- 仓库只包含 `config.example.json`，不包含实际 `config.json`。
- Webhook、签名密钥和 Bearer Token 应通过环境变量或用户本机的 `0600` 配置文件提供。
- 插件不会上传 transcript、原始工具输入、shell 命令、diff 或终端输出。
- 任务简介会限制长度，并对常见路径和凭据形状进行脱敏。

安全问题请参考 [SECURITY.md](SECURITY.md)。本项目使用 [MIT License](LICENSE)。

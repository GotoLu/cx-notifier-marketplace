# Security Policy

## Reporting a vulnerability

公开报告前，请先通过仓库维护者提供的私密安全渠道联系维护者。报告中不要包含真实 Webhook、访问令牌、聊天记录或其他个人数据。

## Secret handling

此仓库不会收集或托管用户密钥。用户应在本机配置以下值，并确保配置文件权限为 `0600`：

- 飞书或企业微信 Webhook；
- 飞书签名密钥；
- 通用 Webhook Bearer Token。

不要把实际配置、`.env`、运行日志、状态数据库或任务 transcript 提交到仓库。提交前运行：

```bash
python3 scripts/check_public_release.py
```

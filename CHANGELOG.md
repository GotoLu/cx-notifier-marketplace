# Changelog

## Unreleased

- Recommend Claude Code's official per-marketplace auto-update flow and keep the one-line manual update command for troubleshooting.

## 0.5.0 - 2026-07-15

- Introduce a provider adapter registry while preserving the existing provider API.
- Add DingTalk custom robots, local desktop notifications, and configurable HMAC-SHA256 webhooks.

## 0.4.0 - 2026-07-15

- Route events by event name, project glob, client, and named channels.
- Add `doctor`, non-sending `simulate`, and sanitized `status` configuration commands.

## 0.3.1 - 2026-07-15

- Make the workspace root the canonical plugin source and generate the public plugin directory through one checked synchronization pipeline.

## 0.3.0 - 2026-07-15

- Add a one-command Feishu setup helper that locates an installed Codex or Claude Code plugin and delegates secret entry to the existing secure configurator.
- Send the sanitized, 160-character user-question summary in task-ending notifications instead of the assistant's final response.
- Capture question context locally through `UserPromptSubmit`, with per-turn matching for Codex and session fallback for Claude Code.
- Document the required Claude marketplace refresh and plugin update commands for existing installations.

## 0.2.0 - 2026-07-15

- Add Claude Code plugin and marketplace manifests.
- Share one notification runtime and Hook configuration across Codex and Claude Code.
- Identify the originating client in notification text and generic Webhook payloads.
- Support Claude Code's persistent `CLAUDE_PLUGIN_DATA` directory.
- Fix duplicate Claude Code Hook registration and cross-platform plugin-root resolution.

## 0.1.0 - 2026-07-15

- Initial public Codex release.

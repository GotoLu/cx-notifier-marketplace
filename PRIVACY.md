# CX Notifier Privacy Policy

Last updated: 2026-07-15

CX Notifier is a local, open-source notification plugin for Codex and Claude Code. It does not operate a CX Notifier cloud service and does not include analytics or telemetry.

## Data processed locally

The plugin processes native `PermissionRequest`, `UserPromptSubmit`, and `Stop` Hook events on the machine running the coding agent. It may store the following data in the local plugin data directory:

- a sanitized user-question summary, limited to 160 characters, so the matching completion notification has context;
- notification delivery identifiers and sanitized delivery results for deduplication and diagnostics;
- channel configuration supplied by the user.

Webhook URLs, signing secrets, and bearer tokens should be stored in the owner-only configuration file or referenced through environment variables. The configuration tool restricts the default file to the current user.

## Data sent to notification providers

When an enabled rule matches, the plugin sends a minimal notification to the provider selected by the user. A notification can include the event title, coding-agent client, project name, and sanitized user-question summary.

The plugin does not intentionally send source code, diffs, terminal output, tool arguments, full transcripts, or the assistant's response. Notification providers receive only the payload needed to deliver the configured reminder and process it under their own terms and privacy policies.

## User control

Users choose every notification channel and can inspect, pause, resume, test, or remove channels locally. Removing the plugin configuration and plugin data directory deletes the data controlled by CX Notifier on that machine. Data already sent to a third-party notification provider is governed by that provider.

## Security and questions

Do not post real Webhook URLs, tokens, or signing secrets in public issues. For vulnerability reporting and supported disclosure channels, see [SECURITY.md](SECURITY.md). For general questions, open a [GitHub issue](https://github.com/GotoLu/cx-notifier-marketplace/issues).

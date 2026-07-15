# Support

For setup questions, bug reports, and feature requests, open a [GitHub issue](https://github.com/GotoLu/cx-notifier-marketplace/issues).

Before filing an issue, run the plugin's local diagnostics from the installed plugin directory:

```bash
python3 scripts/configure.py validate
python3 scripts/configure.py doctor
python3 scripts/configure.py status
```

Include:

- operating system;
- Codex or Claude Code version;
- CX Notifier version;
- notification channel type;
- sanitized command output and reproduction steps.

Never include real Webhook URLs, signing secrets, bearer tokens, private source code, transcripts, or terminal output containing credentials. Report security vulnerabilities through the process in [SECURITY.md](SECURITY.md), not through a public issue.

# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| latest (main) | ✅ |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a security vulnerability, please email:

📧 **kumarpraveen92829@gmail.com**

Include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fix (optional)

You will receive a response within **48 hours**. If the issue is confirmed, a
patch will be released as soon as possible.

## Scope

The following are in scope:

- Credential or token exposure in code or logs
- Authentication/authorisation bypass in the policy module
- Data leakage — email content, task data, OAuth tokens
- Unsafe handling of Gmail, Telegram, or Calendar credentials

## Out of Scope

- Issues in third-party dependencies (report to the upstream project)
- Attacks requiring physical access to the machine
- Social engineering

## Security Design Notes

MailMate AI Desktop is designed with these security principles:

- **Credentials are read from environment variables only** — never hardcoded
- **Gmail OAuth tokens are stored locally** and never transmitted to third parties
- **Email body content is never written to logs**
- **The Telegram bot token is never logged in full** — only the first 8 characters
- **SQLite database is local only** — no remote sync
- **`gmail.readonly` scope only** — the app cannot send, delete, or modify email

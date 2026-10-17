# Contributing to MailMate AI Desktop

Thank you for your interest in contributing! This document explains how to get involved.

---

## Code of Conduct

Be respectful, constructive, and inclusive. Harassment or dismissive behaviour will not be tolerated.

---

## How to Contribute

### Reporting Bugs

Open an issue with:
- A clear title and description
- Steps to reproduce
- Expected vs actual behaviour
- OS / Python version

### Suggesting Features

Open an issue tagged `enhancement`. Describe the problem you want to solve, not just the solution.

### Submitting Code

1. **Fork** the repository
2. **Create a branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make your changes** — keep them focused and small
4. **Run tests** — all 365 must pass:
   ```bash
   python -m unittest discover -s tests -q
   ```
5. **Push** your branch and open a **Pull Request** against `main`
6. Wait for review — all PRs require approval from the repository owner before merging

---

## Branch Rules

- `main` is protected. Direct pushes are not allowed.
- All changes must go through a Pull Request.
- At least one approving review from the repository owner is required before merging.
- CI tests must pass.

---

## Development Setup

```bash
git clone https://github.com/praveen-pravi6B/mailmate-ai-desktop.git
cd mailmate-ai-desktop
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt   # see README for full list
cp .env.example .env              # fill in your credentials
python auth_setup.py              # one-time Google OAuth
```

---

## What to Contribute

Good first issues:
- Improve deadline detection for edge-case date phrases
- Add a `getUpdates` webhook/polling loop for Telegram callbacks
- Add Windows/Linux tray support (replace `rumps`)
- Expand the holdout email dataset with more diverse examples
- Write a setup guide for non-macOS platforms

---

## Contributors

See [CONTRIBUTORS.md](CONTRIBUTORS.md) for a full list of people who have contributed to this project.

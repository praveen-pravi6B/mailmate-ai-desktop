"""Send BotFather setup guide and images to Telegram."""

import os
import requests
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Load .env manually
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT  = os.environ["TELEGRAM_CHAT_ID"]
BASE  = f"https://api.telegram.org/bot{TOKEN}"


def send(text: str) -> bool:
    r = requests.post(f"{BASE}/sendMessage",
                      json={"chat_id": CHAT, "text": text, "parse_mode": "Markdown"})
    return r.ok


def send_photo(path: str, caption: str = "") -> bool:
    with open(path, "rb") as f:
        r = requests.post(
            f"{BASE}/sendPhoto",
            data={"chat_id": CHAT, "caption": caption, "parse_mode": "Markdown"},
            files={"photo": f},
        )
    return r.ok


ASSETS = ROOT / "docs" / "bot_assets"

results = []
results.append(send_photo(str(ASSETS / "profile_pic.png"),
    "🖼 *Profile picture*\nSet via @BotFather → /setuserpic"))

results.append(send_photo(str(ASSETS / "description_pic.png"),
    "🖼 *Description picture*\nSet via @BotFather → /setdescriptionpicture"))

results.append(send(
    "📋 *BotFather Setup Guide*\n\n"
    "Open @BotFather, select your bot, then apply each setting below 👇"
))

results.append(send(
    "1️⃣ */setname*\n"
    "`MailMate AI`"
))

results.append(send(
    "2️⃣ */setdescription* _\\(shown when user first opens the bot\\)_\n"
    "```\n"
    "MailMate AI watches your Gmail inbox, classifies emails by priority "
    "using the Laya typed-decision engine, and creates tasks for actionable emails.\n\n"
    "Get notified instantly via Telegram and confirm or decline routine tasks "
    "— all without leaving the chat. Your data stays local.\n"
    "```"
))

results.append(send(
    "3️⃣ */setabouttext* _\\(max 120 chars\\)_\n"
    "```\n"
    "AI email assistant — classify, prioritise & action your Gmail inbox. "
    "Local-first. Privacy-safe.\n"
    "```"
))

results.append(send(
    "4️⃣ */setcommands* _\\(paste the block below as-is\\)_\n"
    "```\n"
    "start - Start MailMate AI and show status\n"
    "tasks - View pending tasks\n"
    "poll - Trigger a manual Gmail poll now\n"
    "status - Check pipeline and integration status\n"
    "confirm - Confirm a pending P2 task\n"
    "decline - Decline a pending P2 task\n"
    "help - Show all available commands\n"
    "```"
))

results.append(send(
    "5️⃣ */setuserpic*\n"
    "Forward the *profile picture* above to @BotFather when prompted."
))

results.append(send(
    "6️⃣ */setdescriptionpicture*\n"
    "Forward the *description picture* above to @BotFather when prompted."
))

results.append(send("✅ *All done!* Bot is fully configured and ready to go 🚀"))

print(f"Sent {sum(results)}/{len(results)} messages successfully.")

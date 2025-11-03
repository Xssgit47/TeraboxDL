import os
import logging
import time
from collections import defaultdict
import requests

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)
from dotenv import load_dotenv

# -------------------------
# Setup & configuration
# -------------------------
load_dotenv()

# Required
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Optional but recommended
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL_ID")  # e.g. '@yourchannel' or channel/chat ID

# ADMIN_GROUP_ID may be unset; handle gracefully
_admin_group_raw = os.getenv("ADMIN_GROUP_ID", "").strip()
try:
    ADMIN_GROUP_ID = int(_admin_group_raw) if _admin_group_raw else None
except ValueError:
    ADMIN_GROUP_ID = None  # invalid provided value -> disable forwarding to review group

# Parse comma-separated admin user IDs safely
_admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = []
for part in _admin_ids_raw.split(","):
    part = part.strip()
    if not part:
        continue
    try:
        ADMIN_IDS.append(int(part))
    except ValueError:
        pass  # skip invalid entries

BRAND = "Powered by @FNxDANGER"

# Logging
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("terabox-bot")

# -------------------------
# Anti-spam
# -------------------------
USER_LAST_TIME = defaultdict(float)
ANTI_SPAM_INTERVAL = 15  # seconds

# -------------------------
# Messages
# -------------------------
WELCOME_MSG = f"""
👋 Welcome!
This bot fetches Terabox links with a clean, stylish flow.

✨ Features:
- Direct Terabox link processing
- Admin review forwarding (separate from force-join)
- Anti-spam protection
- Force-join channel check

{BRAND}
"""

HELP_MSG = f"""**User Commands:**
/start - Welcome & info
/help - List commands
/terabox <URL> - Process a Terabox link

**Admin Commands:**
/stats - Show bot stats
/ban <user_id> - Ban a user (stub)
/unban <user_id> - Unban a user (stub)
/broadcast <msg> - Send to tracked users

{BRAND}
"""

# -------------------------
# Helpers
# -------------------------
def spam_check(update: Update) -> bool:
    user_id = update.effective_user.id
    now = time.time()
    if now - USER_LAST_TIME[user_id] < ANTI_SPAM_INTERVAL:
        return True
    USER_LAST_TIME[user_id] = now
    return False

async def is_joined(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Check membership in the force-join channel.
    Returns True if no channel is configured, or on API access errors (fails open),
    so users are not blocked by misconfig. Place your policy here as desired.
    """
    if not FORCE_JOIN_CHANNEL:
        return True  # no force-join configured

    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(FORCE_JOIN_CHANNEL, user_id)
        status = getattr(member, "status", "")
        return status in ["member", "administrator", "creator"]
    except (BadRequest, Forbidden) as e:
        # Typical cases:
        # - Bot not admin in channel
        # - Channel is private and bot can’t read it
        # Choose policy: treat as not-joined to enforce subscription
        log.warning("get_chat_member failed: %s", e)
        return False
    except TelegramError as e:
        # Network or other transient errors: allow usage to avoid lockouts
        log.error("TelegramError in get_chat_member: %s", e)
        return True

async def prompt_force_join(update: Update):
    if not FORCE_JOIN_CHANNEL:
        return
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("JOIN CHANNEL 🔗", url=f'https://t.me/{str(FORCE_JOIN_CHANNEL).lstrip("@")}')]]
    )
    await update.effective_message.reply_text(
        f"To use this bot, please join our channel first.\n{BRAND}",
        reply_markup=keyboard
    )

def fetch_terabox(url: str) -> str:
    api_url = f"https://teraboxapi.alphaapi.workers.dev/?url={url}"
    try:
        response = requests.get(api_url, timeout=15)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        return f"Error contacting Terabox API: {e}"

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# -------------------------
# Handlers
# -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(WELCOME_MSG, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HELP_MSG, parse_mode="Markdown")

async def terabox_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Force-join gate
    if not await is_joined(update, context):
        await prompt_force_join(update)
        return

    # Anti-spam
    if spam_check(update):
        await update.effective_message.reply_text(f"⏱ Please wait before sending another request.\n{BRAND}")
        return

    # Validate args
    if not context.args:
        await update.effective_message.reply_text(f"❌ Please provide a Terabox URL.\n{BRAND}")
        return

    file_url = context.args[0].strip()
    reply = fetch_terabox(file_url)
    await update.effective_message.reply_text(f"{reply}\n{BRAND}", parse_mode="Markdown")

    # Forward to review group (if configured)
    if ADMIN_GROUP_ID is not None:
        try:
            user = update.effective_user
            await context.bot.send_message(
                ADMIN_GROUP_ID,
                f"🔎 *New File Request:*\n"
                f"User: [{user.full_name}](tg://user?id={user.id})\n"
                f"URL: {file_url}\n\n{BRAND}",
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except TelegramError as e:
            log.warning("Failed to forward to ADMIN_GROUP_ID %s: %s", ADMIN_GROUP_ID, e)

# Admin commands (stubs for ban/unban storage)
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Access denied.")
        return
    await update.effective_message.reply_text(
        f"Bot is running. Tracked users: {len(USER_LAST_TIME)}.\n{BRAND}"
    )

async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Access denied.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /ban <user_id>")
        return
    await update.effective_message.reply_text(f"User {context.args[0]} banned (stub).\n{BRAND}")

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Access denied.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /unban <user_id>")
        return
    await update.effective_message.reply_text(f"User {context.args[0]} unbanned (stub).\n{BRAND}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("Access denied.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /broadcast <message>")
        return
    msg = " ".join(context.args)
    # Send to every tracked user (seen once in this runtime)
    for uid in list(USER_LAST_TIME.keys()):
        try:
            await context.bot.send_message(uid, f"{msg}\n{BRAND}")
        except TelegramError:
            pass
    await update.effective_message.reply_text("Broadcast sent.")

# -------------------------
# Error handler
# -------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled exception: %s", context.error)

# -------------------------
# Entrypoint
# -------------------------
def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN is not set. Check your .env.")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("terabox", terabox_cmd))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("broadcast", broadcast))

    app.add_error_handler(error_handler)
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()

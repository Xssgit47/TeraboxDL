import os
import logging
import time
import requests
import asyncio
import tempfile
from collections import defaultdict

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.helpers import escape_markdown
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL_ID")

_admin_group_raw = os.getenv("ADMIN_GROUP_ID", "").strip()
try:
    ADMIN_GROUP_ID = int(_admin_group_raw) if _admin_group_raw else None
except ValueError:
    ADMIN_GROUP_ID = None

_admin_ids_raw = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = []
for part in _admin_ids_raw.split(","):
    part = part.strip()
    if part:
        try:
            ADMIN_IDS.append(int(part))
        except ValueError:
            pass

BRAND = "Powered by @FNxDANGER"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("terabox-bot")

USER_LAST_TIME = defaultdict(float)
ANTI_SPAM_INTERVAL = 15

WELCOME_MSG = f"""
👋 Welcome!
This bot fetches Terabox links with a clean, stylish flow.

✨ Features:
- Direct upload of video/photo from Terabox (downloading to VPS first)
- Admin review forwarding
- Anti-spam protection
- Force-join channel check

{BRAND}
"""

HELP_MSG = f"""**User Commands:**
/start - Welcome & Info
/help - List commands
/terabox <URL> - Get direct media or safe file links from Terabox

**Admin Commands:**
/stats - Bot stats
/ban <user_id> - Ban user (stub)
/unban <user_id> - Unban user (stub)
/broadcast <msg> - Broadcast to users

{BRAND}
"""

def spam_check(update: Update) -> bool:
    user_id = update.effective_user.id
    now = time.time()
    if now - USER_LAST_TIME[user_id] < ANTI_SPAM_INTERVAL:
        return True
    USER_LAST_TIME[user_id] = now
    return False

async def is_joined(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not FORCE_JOIN_CHANNEL:
        return True
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(FORCE_JOIN_CHANNEL, user_id)
        status = getattr(member, "status", "")
        return status in ["member", "administrator", "creator"]
    except (BadRequest, Forbidden):
        return False
    except TelegramError:
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(WELCOME_MSG, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HELP_MSG, parse_mode="Markdown")

async def send_media(context, chat_id, admin_group_id, file_info, user, file_url):
    name = file_info.get("name", "File")
    size = file_info.get("size", "")
    dlink = file_info.get("dlink", "")
    isdir = file_info.get("isdir", False)
    extension = os.path.splitext(dlink)[-1].lower()
    caption = f"📁 {name}\nSize: {size}\n{BRAND}"

    # Only try to send for direct file links, not folders
    if isdir or not dlink:
        info_msg = f"{name}\nSize: {size}\n{BRAND}\n{dlink}"
        await context.bot.send_message(chat_id, info_msg)
        if admin_group_id:
            await context.bot.send_message(admin_group_id, f"🔎 ADMIN REVIEW\nUser: {user.full_name}\nURL: {file_url}\n\n{info_msg}")
        return

    try:
        with tempfile.NamedTemporaryFile(delete=True) as temp_file:
            r = requests.get(dlink, stream=True, timeout=120)
            r.raise_for_status()
            total = 0
            for chunk in r.iter_content(chunk_size=1048576):
                temp_file.write(chunk)
                total += len(chunk)
                if total > 2_000_000_000 and extension in ('.mp4', '.mkv', '.mov', '.webm', '.avi'):
                    raise Exception("File too large for Telegram video (2GB limit)")
                if total > 20_000_000 and extension in ('.jpg', '.jpeg', '.png', '.gif'):
                    raise Exception("File too large for Telegram photo (20MB limit)")
            temp_file.flush()
            # Send video/photo if possible
            if extension in ('.mp4', '.mkv', '.mov', '.webm', '.avi'):
                await context.bot.send_video(chat_id=chat_id, video=open(temp_file.name, 'rb'), caption=caption)
                if admin_group_id:
                    await context.bot.send_video(chat_id=admin_group_id, video=open(temp_file.name, 'rb'),
                        caption=f"🔎 ADMIN REVIEW\n{caption}\nRequested by: {user.full_name}\nURL: {file_url}")
            elif extension in ('.jpg', '.jpeg', '.png', '.gif'):
                await context.bot.send_photo(chat_id=chat_id, photo=open(temp_file.name, 'rb'), caption=caption)
                if admin_group_id:
                    await context.bot.send_photo(chat_id=admin_group_id, photo=open(temp_file.name, 'rb'),
                        caption=f"🔎 ADMIN REVIEW\n{caption}\nRequested by: {user.full_name}\nURL: {file_url}")
            else:
                # Send file link fallback
                info_msg = f"{name}\nSize: {size}\n{BRAND}\n{dlink}"
                await context.bot.send_message(chat_id, info_msg)
                if admin_group_id:
                    await context.bot.send_message(admin_group_id, f"🔎 ADMIN REVIEW\nUser: {user.full_name}\nURL: {file_url}\n\n{info_msg}")
    except Exception as e:
        fail_msg = f"{name}\nSize: {size}\n{BRAND}\n{dlink}\n(File transfer failed: {e})"
        await context.bot.send_message(chat_id, fail_msg)
        if admin_group_id:
            await context.bot.send_message(admin_group_id, f"🔎 ADMIN REVIEW\nUser: {user.full_name}\nURL: {file_url}\n\n{fail_msg}")

async def terabox_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_joined(update, context):
        await prompt_force_join(update)
        return

    if spam_check(update):
        await update.effective_message.reply_text(f"⏱ Please wait before sending another request.\n{BRAND}")
        return

    if not context.args:
        await update.effective_message.reply_text(f"❌ Please provide a Terabox URL.\n{BRAND}")
        return

    file_url = context.args[0].strip()
    reply = fetch_terabox(file_url)
    import json
    try:
        data = json.loads(reply)
    except Exception:
        data = None

    user = update.effective_user
    chat_id = update.effective_chat.id

    if data and data.get("success") and data.get("files"):
        for file_info in data["files"]:
            await send_media(context, chat_id, ADMIN_GROUP_ID, file_info, user, file_url)
    else:
        await update.effective_message.reply_text(f"{reply}\n{BRAND}")

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
    for uid in list(USER_LAST_TIME.keys()):
        try:
            await context.bot.send_message(uid, f"{msg}\n{BRAND}")
        except TelegramError:
            pass
    await update.effective_message.reply_text("Broadcast sent.")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled exception: %s", context.error)

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

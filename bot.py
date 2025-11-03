import os
import logging
import time
import requests
import tempfile
from collections import defaultdict

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)
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
- Direct video/photo upload if possible from Terabox (using local VPS download & re-upload)
- Admin review forwarding
- Anti-spam protection
- Force-join channel check

{BRAND}
"""

HELP_MSG = f"""**User Commands:**
/start - Welcome & Info
/help - List commands
/terabox <URL> - Fetch direct media or download links from Terabox

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

def download_and_upload(context, chat_id, admin_group_id, file_info, user, file_url):
    name = file_info.get("name", "File")
    size = file_info.get("size", "")
    dlink = file_info.get("dlink", "")
    isdir = file_info.get("isdir", False)
    extension = os.path.splitext(dlink)[-1].lower()

    caption = f"📁 {name}\nSize: {size}\n{BRAND}"

    if isdir or not dlink:
        msg_text = f"Folder: {name}\nSize: {size}\n{BRAND}\n{dlink}"
        context.bot.send_message(chat_id, msg_text, disable_web_page_preview=False)
        if admin_group_id:
            admin_msg = (
                f"🔎 ADMIN REVIEW\nUser: {user.full_name}\nURL: {file_url}\n\n{msg_text}"
            )
            context.bot.send_message(admin_group_id, admin_msg)
        return

    try:
        # Try to download file and send as media (Telegram allows ≤2GB for videos, ≤20MB for images)
        with tempfile.NamedTemporaryFile(delete=True) as temp_file:
            r = requests.get(dlink, stream=True, timeout=60)
            r.raise_for_status()
            for chunk in r.iter_content(chunk_size=1048576): # 1MB at a time
                temp_file.write(chunk)
            temp_file.flush()

            # Try video first
            if extension in ('.mp4', '.mkv', '.mov', '.webm', '.avi'):
                context.bot.send_video(chat_id=chat_id, video=open(temp_file.name, 'rb'), caption=caption)
                if admin_group_id:
                    context.bot.send_video(chat_id=admin_group_id, video=open(temp_file.name, 'rb'),
                        caption=f"🔎 ADMIN REVIEW\n{caption}\nRequested by: {user.full_name}\nURL: {file_url}")
            # Try photo
            elif extension in ('.jpg', '.jpeg', '.png', '.gif'):
                context.bot.send_photo(chat_id=chat_id, photo=open(temp_file.name, 'rb'), caption=caption)
                if admin_group_id:
                    context.bot.send_photo(chat_id=admin_group_id, photo=open(temp_file.name, 'rb'),
                        caption=f"🔎 ADMIN REVIEW\n{caption}\nRequested by: {user.full_name}\nURL: {file_url}")
            else:
                msg_text = f"{name}\nSize: {size}\n{BRAND}\n{dlink}"
                context.bot.send_message(chat_id, msg_text, disable_web_page_preview=True)
                if admin_group_id:
                    admin_msg = (
                        f"🔎 ADMIN REVIEW\nUser: {user.full_name}\nURL: {file_url}\n\n{msg_text}"
                    )
                    context.bot.send_message(admin_group_id, admin_msg)
    except Exception as e:
        # Fallback: send link
        msg_text = f"{name}\nSize: {size}\n{BRAND}\n{dlink}\n(File transfer failed: {e})"
        context.bot.send_message(chat_id, msg_text, disable_web_page_preview=True)
        if admin_group_id:
            admin_msg = (
                f"🔎 ADMIN REVIEW\nUser: {user.full_name}\nURL: {file_url}\n\n{msg_text}"
            )
            context.bot.send_message(admin_group_id, admin_msg)

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

    # Use run_in_executor to avoid blocking async event loop with the file download/upload
    if data and data.get("success") and data.get("files"):
        import concurrent.futures
        for file_info in data["files"]:
            loop = context.application.loop
            with concurrent.futures.ThreadPoolExecutor() as pool:
                await loop.run_in_executor(pool, download_and_upload, context, chat_id, ADMIN_GROUP_ID, file_info, user, file_url)
    else:
        # fallback message
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

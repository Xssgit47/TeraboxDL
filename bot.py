import os
import logging
import time
from collections import defaultdict
import requests
import json
import uuid
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
    if not part:
        continue
    try:
        ADMIN_IDS.append(int(part))
    except ValueError:
        pass

VPS_IP = os.getenv("VPS_IP", "46.202.163.22")
VPS_PORT = os.getenv("VPS_PORT", "8083")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/tmp/bot_dl")

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
- Direct Terabox link processing with direct media sending
- Admin review forwarding (separate from force-join)
- Anti-spam protection
- Force-join channel check
{BRAND}
"""

HELP_MSG = f"""**User Commands:**
/start - Welcome & Info
/help - List commands
/terabox - Fetch direct media or download links from Terabox

**Admin Commands:**
/stats - Bot stats
/ban - Ban user (stub)
/unban - Unban user (stub)
/broadcast - Broadcast to users
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

def is_likely_video(file_info: dict) -> bool:
    """Detect video based on category, name extension, or size (>100MB)."""
    name = file_info.get("name", "")
    size_str = file_info.get("size", "0")
    category = file_info.get("category", 0)
    size_bytes = 0
    if 'MB' in size_str:
        size_bytes = float(size_str.split()[0]) * 1024 * 1024
    elif 'GB' in size_str:
        size_bytes = float(size_str.split()[0]) * 1024 * 1024 * 1024
    video_extensions = ('.mp4', '.mkv', '.avi', '.mov', '.webm', '.m4v')
    name_lower = name.lower()
    return (category == 1 or
            any(name_lower.endswith(ext) for ext in video_extensions) or
            size_bytes > 100 * 1024 * 1024)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(WELCOME_MSG, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HELP_MSG, parse_mode="Markdown")

async def send_file_to_user_and_group(context, chat_id, admin_group_id, file_info, user, file_url):
    name = file_info.get("name", "File")
    size = file_info.get("size", "")
    dlink = file_info.get("dlink", "")
    isdir = file_info.get("isdir", False)
    
    # Escape all parts individually
    name_escaped = escape_markdown(name, version=2)
    size_escaped = escape_markdown(size, version=2)
    dlink_escaped = escape_markdown(dlink, version=2) if dlink else ""
    file_url_escaped = escape_markdown(file_url, version=2)
    user_name_escaped = escape_markdown(user.full_name, version=2)
    
    if isdir or not dlink:
        msg_text = f"Folder: \\[{name_escaped}\\]\\({dlink_escaped}\\)\nSize: {size_escaped}\n{BRAND}"
        await context.bot.send_message(chat_id, msg_text, parse_mode="MarkdownV2", disable_web_page_preview=False)
        if admin_group_id:
            admin_msg = f"🔎 \\*New Folder Request: \\*\nUser: \\[{user_name_escaped}\\]\\(tg://user\\?id={user.id}\\)\nURL: `{file_url_escaped}`\n\n{msg_text}"
            try:
                await context.bot.send_message(admin_group_id, admin_msg, parse_mode="MarkdownV2")
            except BadRequest:
                # Fallback plain text: Unescape outside f-string
                clean_msg = msg_text.replace('\\\\', '').replace('\\[', '[').replace('\\]', ']').replace('\\(', '(').replace('\\)', ')')
                plain_admin = f"New Folder Request:\nUser: {user.full_name} (id:{user.id})\nURL: {file_url}\n\n{clean_msg}"
                await context.bot.send_message(admin_group_id, plain_admin)
        return

    try:
        if is_likely_video(file_info):
            unique_id = str(uuid.uuid4())
            ext = os.path.splitext(name)[1] or '.mp4'
            local_filename = f"{unique_id}{ext}"
            local_path = os.path.join(DOWNLOAD_DIR, local_filename)
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            vps_stream_url = f"http://{VPS_IP}:{VPS_PORT}/dl/{local_filename}"
            vps_url_escaped = escape_markdown(vps_stream_url, version=2)

            log.info(f"Attempting download for video: {name} to {local_path}")
            download_success = False
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Referer': 'https://www.terabox.com/'
            }
            try:
                response = requests.get(dlink, stream=True, timeout=120, headers=headers)
                response.raise_for_status()
                with open(local_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                download_success = os.path.exists(local_path) and os.path.getsize(local_path) > 0
                log.info(f"Download success: {download_success} for {name} (size: {os.path.getsize(local_path) if download_success else 0})")
            except Exception as download_e:
                log.warning(f"Download failed for {name}: {download_e}")
                if os.path.exists(local_path):
                    os.remove(local_path)

            # Safe caption construction with escaped links
            base_caption = f"📁 \\*{name_escaped}\\*\nSize: {size_escaped}"
            if download_success:
                dual_links = f"\n📥 Download: \\[{dlink_escaped}\\]\\({dlink_escaped}\\)\n▶️ Stream: \\[{vps_url_escaped}\\]\\({vps_url_escaped}\\)"
                caption = f"{base_caption}{dual_links}\n{BRAND}"
                video_url = vps_stream_url
            else:
                dual_links = f"\n📥 Download: \\[{dlink_escaped}\\]\\({dlink_escaped}\\)\n▶️ Stream (fallback): \\[{vps_url_escaped}\\]\\({dlink_escaped}\\)"
                caption = f"{base_caption}{dual_links}\n{BRAND}"
                video_url = dlink

            # Send video
            await context.bot.send_video(
                chat_id=chat_id, 
                video=video_url, 
                caption=caption, 
                parse_mode="MarkdownV2", 
                supports_streaming=True
            )
            # Admin send with try-except
            if admin_group_id:
                admin_caption = f"🔎 \\*ADMIN REVIEW\\*\n{caption}\nRequested by: \\[{user_name_escaped}\\]\\(tg://user\\?id={user.id}\\)\nURL: `{file_url_escaped}`"
                try:
                    await context.bot.send_video(
                        chat_id=admin_group_id, 
                        video=video_url, 
                        caption=admin_caption, 
                        parse_mode="MarkdownV2", 
                        supports_streaming=True
                    )
                except BadRequest as e:
                    log.warning(f"Admin video send failed: {e}")
                    # Fallback plain: Unescape outside f-string
                    clean_caption = caption.replace('\\\\', '').replace('\\[', '[').replace('\\]', ']').replace('\\(', '(').replace('\\)', ')').replace('\\*', '*')
                    plain_admin = f"ADMIN REVIEW\n{clean_caption}\nRequested by: {user.full_name} (tg://user?id={user.id})\nURL: {file_url}"
                    await context.bot.send_message(admin_group_id, plain_admin)
            return

        # Non-videos (images/docs) - unchanged but with safe escaping
        if dlink.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
            caption = f"📁 \\*{name_escaped}\\*\nSize: {size_escaped}\n{BRAND}"
            await context.bot.send_photo(chat_id=chat_id, photo=dlink, caption=caption, parse_mode="MarkdownV2")
            if admin_group_id:
                admin_caption = f"🔎 \\*ADMIN REVIEW\\*\n{caption}\nRequested by: \\[{user_name_escaped}\\]\\(tg://user\\?id={user.id}\\)\nURL: `{file_url_escaped}`"
                try:
                    await context.bot.send_photo(admin_group_id, photo=dlink, caption=admin_caption, parse_mode="MarkdownV2")
                except BadRequest:
                    # Fallback plain
                    clean_caption = caption.replace('\\\\', '').replace('\\[', '[').replace('\\]', ']').replace('\\(', '(').replace('\\)', ')').replace('\\*', '*')
                    plain_admin = f"ADMIN REVIEW\n{clean_caption}\nRequested by: {user.full_name}\nURL: {file_url}"
                    await context.bot.send_message(admin_group_id, plain_admin)
        else:
            msg_text = f"\\[{name_escaped}\\]\\({dlink_escaped}\\)\nSize: {size_escaped}\n{BRAND}"
            await context.bot.send_message(chat_id, msg_text, parse_mode="MarkdownV2", disable_web_page_preview=True)
            if admin_group_id:
                admin_msg = f"🔎 \\*New File Request: \\*\nUser: \\[{user_name_escaped}\\]\\(tg://user\\?id={user.id}\\)\nURL: `{file_url_escaped}`\n\n{msg_text}"
                try:
                    await context.bot.send_message(admin_group_id, admin_msg, parse_mode="MarkdownV2", disable_web_page_preview=True)
                except BadRequest:
                    # Fallback plain: Unescape outside f-string
                    clean_msg = msg_text.replace('\\\\', '').replace('\\[', '[').replace('\\]', ']').replace('\\(', '(').replace('\\)', ')').replace('\\*', '*')
                    plain_admin = f"New File Request:\nUser: {user.full_name}\nURL: {file_url}\n\n{clean_msg}"
                    await context.bot.send_message(admin_group_id, plain_admin)

    except Exception as e:
        log.error(f"Send error for {name}: {e}")
        # Safe fallback: Plain text with raw links
        fallback = f"{name}\nSize: {size}\nDownload: {dlink}\n{BRAND}"
        await context.bot.send_message(chat_id, fallback, disable_web_page_preview=True)
        if admin_group_id:
            admin_fallback = f"New File Request (fallback):\nUser: {user.full_name}\nURL: {file_url}\n\n{fallback}"
            await context.bot.send_message(admin_group_id, admin_fallback)

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

    try:
        data = json.loads(reply)
    except Exception:
        data = None

    user = update.effective_user
    chat_id = update.effective_chat.id

    if data and data.get("success") and data.get("files"):
        for file_info in data["files"]:
            await send_file_to_user_and_group(context, chat_id, ADMIN_GROUP_ID, file_info, user, file_url)
    else:
        safe_reply = escape_markdown(reply, version=2)
        await update.effective_message.reply_text(f"{safe_reply}\n{BRAND}", parse_mode="MarkdownV2")

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

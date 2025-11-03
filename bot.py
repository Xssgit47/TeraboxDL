import os
import logging
import time
from collections import defaultdict
import requests
import json

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.helpers import escape_markdown

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- CONFIGURATION ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL_ID")

# Admin Group and Admin User IDs
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

BRAND = "Powered by @FNxDANGER"

# --- LOGGING SETUP ---
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("terabox-bot")

# --- BOT MEMORY & CONSTANTS ---
USER_LAST_TIME = defaultdict(float)
ANTI_SPAM_INTERVAL = 15  # seconds

# --- BOT MESSAGES (with improved formatting) ---
WELCOME_MSG = f"""
👋 *Welcome to the Terabox Link Bot!*

This bot helps you fetch direct links from Terabox URLs with a clean and stylish flow.

✨ *Features:*
- **Direct Link Processing**: Instantly get media or download links.
- **Admin Review**: Files are forwarded for review.
- **Anti-Spam**: Protects against request flooding.
- **Force Join**: Ensures users join your channel.

To get started, simply send a Terabox URL.

{BRAND}
"""

HELP_MSG = f"""
*Here's how you can use the bot:*

👤 *User Commands:*
- `/start` - Shows the welcome message.
- `/help` - Displays this help message.
- `/terabox <URL>` - Fetches the direct link for a Terabox URL.

🔒 *Admin Commands:*
- `/stats` - View bot usage statistics.
- `/ban <user_id>` - Ban a user.
- `/unban <user_id>` - Unban a user.
- `/broadcast <message>` - Send a message to all users.

{BRAND}
"""

# --- HELPER FUNCTIONS ---
def spam_check(update: Update) -> bool:
    """Checks if the user is spamming the bot."""
    user_id = update.effective_user.id
    now = time.time()
    if now - USER_LAST_TIME[user_id] < ANTI_SPAM_INTERVAL:
        return True
    USER_LAST_TIME[user_id] = now
    return False

async def is_joined(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Checks if a user has joined the force-join channel."""
    if not FORCE_JOIN_CHANNEL:
        return True
    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(FORCE_JOIN_CHANNEL, user_id)
        return member.status in ["member", "administrator", "creator"]
    except (BadRequest, Forbidden):
        # User is not in the channel or channel is not accessible
        return False
    except TelegramError as e:
        log.error(f"Error checking channel membership: {e}")
        return True # Fail open to not block user if Telegram API fails

async def prompt_force_join(update: Update):
    """Sends a message prompting the user to join the channel."""
    if not FORCE_JOIN_CHANNEL:
        return
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("JOIN CHANNEL 🔗", url=f'https://t.me/{str(FORCE_JOIN_CHANNEL).lstrip("@")}')]]
    )
    await update.effective_message.reply_text(
        f"⚠️ *To use this bot, you must join our channel first.*\n\nClick the button below to join, then send your link again.\n\n{BRAND}",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

def fetch_terabox(url: str) -> str:
    """Fetches data from the Terabox API."""
    api_url = f"https://teraboxapi.alphaapi.workers.dev/?url={url}"
    try:
        response = requests.get(api_url, timeout=20)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        log.error(f"Terabox API request failed: {e}")
        return json.dumps({"success": False, "error": f"Error contacting Terabox API: {e}"})

def is_admin(user_id: int) -> bool:
    """Checks if a user is an admin."""
    return user_id in ADMIN_IDS

# --- CORE HANDLERS ---
async def send_file_to_user_and_group(context, chat_id, admin_group_id, file_info, user, file_url):
    """Sends file/folder info to the user and the admin group."""
    name = file_info.get("name", "File")
    size = file_info.get("size", "N/A")
    dlink = file_info.get("dlink", "")
    is_dir = file_info.get("isdir", False)

    # For User
    user_caption = f"📁 *{escape_markdown(name, 2)}*\n\n📏 *Size*: {escape_markdown(size, 2)}\n\n{escape_markdown(BRAND, 2)}"
    
    # For Admin
    admin_base_caption = (
        f"🔎 *ADMIN REVIEW*\n\n"
        f"👤 *User*: [{escape_markdown(user.full_name, 2)}](tg://user?id={user.id})\n"
        f"🔗 *URL*: `{escape_markdown(file_url, 2)}`\n\n"
        f"{user_caption}"
    )

    try:
        if is_dir:
            msg_text = f"🗂️ *Folder Request:*\n\n*{escape_markdown(name, 2)}*\n\nThis is a folder, not a direct file. Please check the contents via the original link.\n\n{escape_markdown(BRAND, 2)}"
            await context.bot.send_message(chat_id, msg_text, parse_mode="MarkdownV2")
            if admin_group_id:
                await context.bot.send_message(admin_group_id, admin_base_caption, parse_mode="MarkdownV2")
            return

        if dlink.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm')):
            await context.bot.send_video(chat_id, video=dlink, caption=user_caption, parse_mode="MarkdownV2", supports_streaming=True)
            if admin_group_id:
                await context.bot.send_video(admin_group_id, video=dlink, caption=admin_base_caption, parse_mode="MarkdownV2", supports_streaming=True)
        elif dlink.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
            await context.bot.send_photo(chat_id, photo=dlink, caption=user_caption, parse_mode="MarkdownV2")
            if admin_group_id:
                await context.bot.send_photo(admin_group_id, photo=dlink, caption=admin_base_caption, parse_mode="MarkdownV2")
        else: # Other file types
            download_msg = f"{user_caption}\n\n[⬇️ Download Link]({escape_markdown(dlink, 2)})"
            await context.bot.send_message(chat_id, download_msg, parse_mode="MarkdownV2", disable_web_page_preview=True)
            if admin_group_id:
                admin_download_msg = f"{admin_base_caption}\n\n[⬇️ Download Link]({escape_markdown(dlink, 2)})"
                await context.bot.send_message(admin_group_id, admin_download_msg, parse_mode="MarkdownV2", disable_web_page_preview=True)

    except Exception as e:
        log.error(f"Failed to send media: {e}")
        fallback_msg = f"❗️ *An error occurred while sending the file.*\n\nHere is a fallback link:\n[{escape_markdown(name, 2)}]({escape_markdown(dlink, 2)})\n\n{escape_markdown(BRAND, 2)}"
        await context.bot.send_message(chat_id, fallback_msg, parse_mode="MarkdownV2", disable_web_page_preview=True)
        if admin_group_id:
             await context.bot.send_message(admin_group_id, f"Failed to process for user, here is fallback info:\n{admin_base_caption}", parse_mode="MarkdownV2")


async def terabox_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /terabox command."""
    if not await is_joined(update, context):
        await prompt_force_join(update)
        return

    if spam_check(update):
        await update.effective_message.reply_text(f"⏱️ *Please wait a moment before sending another request.*\n\n{BRAND}", parse_mode="Markdown")
        return

    if not context.args:
        await update.effective_message.reply_text(f"❌ *Usage: /terabox <URL>*\n\nPlease provide a Terabox URL to process.\n\n{BRAND}", parse_mode="Markdown")
        return

    file_url = context.args[0].strip()
    user = update.effective_user
    chat_id = update.effective_chat.id
    
    thinking_message = await update.effective_message.reply_text("⏳ Processing your link, please wait...")

    reply = fetch_terabox(file_url)
    
    try:
        data = json.loads(reply)
    except json.JSONDecodeError:
        data = {"success": False, "error": "Invalid response from API."}

    await context.bot.delete_message(chat_id=chat_id, message_id=thinking_message.message_id)

    if data and data.get("success") and data.get("files"):
        for file_info in data["files"]:
            await send_file_to_user_and_group(context, chat_id, ADMIN_GROUP_ID, file_info, user, file_url)
    else:
        error_message = data.get("error", "Could not process the link. Please ensure it's a valid Terabox URL.")
        safe_reply = escape_markdown(error_message, version=2)
        await update.effective_message.reply_text(f"❗️ *Error:*\n{safe_reply}\n\n{escape_markdown(BRAND, 2)}", parse_mode="MarkdownV2")


# --- COMMAND HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(WELCOME_MSG, parse_mode="Markdown")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(HELP_MSG, parse_mode="Markdown")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("🚫 Access denied.")
        return
    await update.effective_message.reply_text(f"📊 *Bot Stats*\n\nTracked users: {len(USER_LAST_TIME)}\n\n{BRAND}", parse_mode="Markdown")

async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("🚫 Access denied.")
        return
    # Add ban logic here
    await update.effective_message.reply_text("Ban command is a stub.")

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("🚫 Access denied.")
        return
    # Add unban logic here
    await update.effective_message.reply_text("Unban command is a stub.")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("🚫 Access denied.")
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /broadcast <message>")
        return
    
    msg = " ".join(context.args)
    sent_count = 0
    for uid in list(USER_LAST_TIME.keys()):
        try:
            await context.bot.send_message(uid, f"📢 *Broadcast from Admin:*\n\n{msg}\n\n{BRAND}", parse_mode="Markdown")
            sent_count += 1
            time.sleep(0.1) # Avoid hitting rate limits
        except (Forbidden, BadRequest):
            # User blocked the bot or chat not found
            pass
        except Exception as e:
            log.error(f"Broadcast failed for user {uid}: {e}")
    await update.effective_message.reply_text(f"✅ Broadcast sent to {sent_count} users.")


# --- ERROR HANDLER ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled exception: %s", context.error)


# --- MAIN FUNCTION ---
def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN is not set. Check your .env file.")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("terabox", terabox_cmd))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("broadcast", broadcast))

    # Error handler
    app.add_error_handler(error_handler)

    # Start the bot
    log.info("Bot is starting...")
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()

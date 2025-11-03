import os
import logging
import time
from collections import defaultdict
import requests
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL_ID") # '@yourchannel'
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID")) # The group ID for file reviews
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(',')] # Comma-separated list of admin user IDs
BRAND = "Powered by @FNxDANGER"

# Anti-spam settings
USER_LAST_TIME = defaultdict(float)
ANTI_SPAM_INTERVAL = 15 # seconds

# Stylish greeting message
WELCOME_MSG = """
👋 Welcome!  
This bot helps you fetch files from Terabox instantly with an elegant UI.

✨ **Features:**
- Direct Terabox download links
- Strong anti-spam shields
- You must join our main channel to use the bot

{}

""".format(BRAND)

def is_joined(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check user membership in force-join channel."""
    user_id = update.effective_user.id
    chat_member = context.bot.get_chat_member(FORCE_JOIN_CHANNEL, user_id)
    return chat_member.status in ['member', 'administrator', 'creator']

def spam_check(update: Update):
    """Basic anti-spam mechanism."""
    user_id = update.effective_user.id
    now = time.time()
    if now - USER_LAST_TIME[user_id] < ANTI_SPAM_INTERVAL:
        return True
    USER_LAST_TIME[user_id] = now
    return False

async def force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detect if user is not joined and force them to join."""
    if is_joined(update, context):
        return False
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("JOIN CHANNEL 🔗", url=f'https://t.me/{FORCE_JOIN_CHANNEL.lstrip("@")}')]])
    await update.message.reply_text(
        f"To use this bot, you must join our channel first!\n{BRAND}", reply_markup=keyboard
    )
    return True

def fetch_terabox(url: str) -> str:
    api_url = f"https://teraboxapi.alphaapi.workers.dev/?url={url}"
    try:
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        return f"Error contacting Terabox API: {e}"

# User commands
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MSG, parse_mode='Markdown')

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "**User Commands:**\n"
        "/start  - Show welcome and info\n"
        "/help   - List user commands\n"
        "/terabox <URL> - Get file from Terabox\n"
        "\n**Admin Commands:**\n"
        "/stats   - Show bot stats\n"
        "/ban <user_id> - Ban a user\n"
        "/unban <user_id> - Unban user\n"
        "/broadcast <msg> - Send to all users\n"
        f"\n{BRAND}",
        parse_mode='Markdown'
    )

async def terabox_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await force_join(update, context):
        return
    if spam_check(update):
        await update.message.reply_text("⏱ Please wait before sending another request. " + BRAND)
        return
    if not context.args:
        await update.message.reply_text('❌ Please provide a Terabox file URL.\nPowered by @FNxDANGER')
        return

    file_url = context.args[0]
    reply = fetch_terabox(file_url)
    await update.message.reply_text(f"{reply}\n{BRAND}", parse_mode='Markdown')
    # Forward request info to the review group for moderation (message, username, etc.)
    await context.bot.send_message(
        ADMIN_GROUP_ID,
        f"🔎 *New File Request:*\nUser: [{update.effective_user.full_name}](tg://user?id={update.effective_user.id})\nURL: {file_url}\n\n{BRAND}",
        parse_mode='Markdown'
    )

# Admin commands
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Access denied.")
        return
    await update.message.reply_text("Bot is running. Users tracked: {}.\n{}".format(len(USER_LAST_TIME), BRAND))

async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Access denied.")
        return
    if context.args:
        banned_id = int(context.args[0])
        # you can add ban logic to a persistent store/database
        await update.message.reply_text(f"User {banned_id} banned.\n{BRAND}")

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Access denied.")
        return
    if context.args:
        unbanned_id = int(context.args[0])
        # you can add unban logic to a persistent store/database
        await update.message.reply_text(f"User {unbanned_id} unbanned.\n{BRAND}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Access denied.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    msg = " ".join(context.args)
    for uid in USER_LAST_TIME: # Send to each tracked user
        try:
            await context.bot.send_message(uid, msg + "\n" + BRAND)
        except Exception:
            pass
    await update.message.reply_text("Broadcast sent.")

# You can implement review-mode toggles, audit logs etc as needed.

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("terabox", terabox_cmd))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("broadcast", broadcast))
    # Add more admin/user commands/edit handlers

    app.run_polling()

if __name__ == "__main__":
    main()

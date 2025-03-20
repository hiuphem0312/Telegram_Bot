import os
import logging
import re
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from flask import Flask, request

# Import your existing functions
from utils import fetch_webpage_content, analyze_content, update_google_sheet

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Regex to quickly check if message text looks like a URL
URL_REGEX = re.compile(r"^https?://", re.IGNORECASE)

# Flask app for handling webhook requests
app = Flask(__name__)

# Get Telegram token and webhook URL
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = f"https://https://telegram-bot-2-6usu.onrender.com/webhook/7972682364:AAG4BFeK1jwPLeIQzB0Kw5sS-8Wu9JgeODo"  # Replace with your actual URL

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command."""
    await update.message.reply_text(
        "Xin chào! Hãy gửi cho tôi một URL và tôi sẽ tóm tắt nội dung bài viết cho bạn."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles messages that are URLs."""
    user_text = update.message.text.strip()

    # Check if user_text is a URL
    if not URL_REGEX.match(user_text):
        await update.message.reply_text(
            "Đây không phải là URL hợp lệ. Vui lòng gửi một đường dẫn bắt đầu với http:// hoặc https://."
        )
        return

    await update.message.reply_text("Đang xử lý bài báo...")

    try:
        # Fetch, analyze, and update Google Sheet
        content = fetch_webpage_content(user_text)
        if not content:
            await update.message.reply_text("Không thể trích xuất nội dung từ URL này.")
            return

        analysis = analyze_content(content)
        if not analysis:
            await update.message.reply_text("Không thể phân tích nội dung.")
            return

        update_google_sheet(analysis, user_text)

        # Send result to user
        subject = analysis.get("subject", "N/A")
        title = analysis.get("title", "N/A")
        summary = analysis.get("summary", "N/A")

        response_text = (
            f"**Kết quả phân tích**\n"
            f"Chủ đề: {subject}\n"
            f"Tiêu đề: {title}\n"
            f"Tóm tắt: {summary}\n\n"
            f"Link bài báo: {user_text}"
        )
        await update.message.reply_text(response_text, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error handling URL: {e}", exc_info=True)
        await update.message.reply_text(f"Đã xảy ra lỗi: {str(e)}")

# Initialize Telegram bot
bot_app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
bot_app.add_handler(CommandHandler("start", start_command))
bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# Flask route for handling Telegram webhook updates
@app.route(f"/webhook/{TELEGRAM_BOT_TOKEN}", methods=["POST"])
def webhook():
    """Handle incoming Telegram updates."""
    update = Update.de_json(request.get_json(), bot_app.bot)
    bot_app.process_update(update)
    return "OK", 200

def set_webhook():
    """Register the webhook with Telegram."""
    bot_app.bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set to {WEBHOOK_URL}")

if __name__ == "__main__":
    set_webhook()  # Set the webhook on startup
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8443)))  # Run Flask app

import os
import re
import logging
import threading
from dotenv import load_dotenv

from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# Import your existing functions
from utils import fetch_webpage_content, analyze_content, update_google_sheet

# Load environment variables (including TELEGRAM_BOT_TOKEN)
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Regex to quickly check if message text looks like a URL
URL_REGEX = re.compile(r"^https?://", re.IGNORECASE)

# Minimal Flask app to keep Render from timing out
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    """Simple endpoint so Render sees an open port."""
    return "Bot is alive!"

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start command handler.
    """
    await update.message.reply_text(
        "Xin chào! Hãy gửi cho tôi một URL và tôi sẽ tóm tắt nội dung bài viết cho bạn."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle any non-command message. We check if it looks like a URL, then process it.
    """
    user_text = update.message.text.strip()

    # Check if user_text is a URL
    if not URL_REGEX.match(user_text):
        await update.message.reply_text(
            "Đây không phải là URL hợp lệ. Vui lòng gửi một đường dẫn bắt đầu với http:// hoặc https://."
        )
        return

    # Acknowledge we received a URL
    await update.message.reply_text("Đang xử lý bài báo...")

    try:
        # 1. Fetch content
        content = fetch_webpage_content(user_text)
        if not content:
            await update.message.reply_text("Không thể trích xuất nội dung từ URL này.")
            return

        # 2. Analyze content
        analysis = analyze_content(content)
        if not analysis:
            await update.message.reply_text("Không thể phân tích nội dung.")
            return

        # 3. Update Google Sheet
        update_google_sheet(analysis, user_text)

        # 4. Send result back to user
        subject = analysis.get('subject', 'N/A')
        title = analysis.get('title', 'N/A')
        summary = analysis.get('summary', 'N/A')

        response_text = (
            f"**Kết quả phân tích**\n"
            f"Chủ đề: {subject}\n"
            f"Tiêu đề: {title}\n"
            f"Tóm tắt: {summary}\n\n"
            f"Link bài báo: {user_text}"
        )
        await update.message.reply_text(response_text, parse_mode='Markdown')

    except Exception as e:
        logger.error(f"Error handling URL: {e}", exc_info=True)
        await update.message.reply_text(f"Đã xảy ra lỗi: {str(e)}")

def run_telegram_bot():
    """
    Runs the Telegram bot in polling mode.
    """
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set in .env")

    # Create the bot application
    app = ApplicationBuilder().token(telegram_token).build()

    # Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is running in polling mode...")
    app.run_polling()

def run_flask():
    """
    Runs a minimal Flask server so Render detects an open port.
    """
    port = int(os.getenv("PORT", 8000))
    logger.info(f"Starting Flask on port {port}")
    flask_app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    # 1) Start Flask in one thread (keeps the service alive)
    threading.Thread(target=run_flask).start()

    # 2) Run the Telegram bot in the main thread
    run_telegram_bot()

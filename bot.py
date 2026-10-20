import os
import re
import asyncio
from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Configuration - Sirf BOT_TOKEN chahiye
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", "8080"))

# Directories
DOWNLOAD_DIR = "downloads"
THUMBNAIL_DIR = "thumbnails"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(THUMBNAIL_DIR, exist_ok=True)

# User state storage: {user_id: {"file_id": ..., "file_name": ...}}
USER_STATE = {}


def sanitize_filename(filename: str) -> str:
    """Clean filename by removing invalid characters for files."""
    filename = filename.strip()
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    # Replace characters not allowed in filenames
    clean_name = re.sub(r'[\\/*?:"<>|]', '_', filename)
    clean_name = re.sub(r'[\s_]+', '_', clean_name)
    return clean_name


# --- Render Health Check Web Server ---
async def start_web_server():
    async def handle_ping(request):
        return web.Response(text="PDF Renamer Bot is active and running!")

    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/health", handle_ping)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"[*] Health Check Server listening on port {PORT}")


# --- Command Handlers ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = (
        f"👋 **Namaste {user.first_name}!**\n\n"
        "Main ek **PDF Renamer & Custom Thumbnail Bot** hoon.\n\n"
        "📌 **Kaise use karein:**\n"
        "1. Sabse pehle koi bhi **Photo** bhejein jo aap PDF ka thumbnail banana chahte hain.\n"
        "2. Fir apni **PDF file** bhejein.\n"
        "3. PDF ka naya naam type karke bhejein.\n\n"
        "✨ **Commands:**\n"
        "• `/delthumb` - Saved thumbnail delete karne ke liye\n"
        "• `/viewthumb` - Current thumbnail dekhne ke liye\n"
        "• `/cancel` - Ongoing process cancel karne ke liye"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in USER_STATE:
        del USER_STATE[user_id]
        await update.message.reply_text("❌ Process cancel kar diya gaya hai.")
    else:
        await update.message.reply_text("Koi active process nahi chal raha hai.")


async def delthumb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    thumb_path = os.path.join(THUMBNAIL_DIR, f"{user_id}.jpg")
    if os.path.exists(thumb_path):
        os.remove(thumb_path)
        await update.message.reply_text("🗑️ Aapka custom thumbnail delete kar diya gaya hai!")
    else:
        await update.message.reply_text("Aapne abhi tak koi custom thumbnail set nahi kiya hai.")


async def viewthumb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    thumb_path = os.path.join(THUMBNAIL_DIR, f"{user_id}.jpg")
    if os.path.exists(thumb_path):
        with open(thumb_path, "rb") as photo:
            await update.message.reply_photo(photo=photo, caption="Yeh aapka current saved thumbnail hai.")
    else:
        await update.message.reply_text("Aapne abhi tak koi custom thumbnail set nahi kiya hai.")


# --- Photo Handler (Thumbnail Save) ---
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    thumb_path = os.path.join(THUMBNAIL_DIR, f"{user_id}.jpg")

    wait_msg = await update.message.reply_text("⏳ Saving thumbnail...")
    try:
        photo_file = await update.message.photo[-1].get_file()
        await photo_file.download_to_drive(thumb_path)
        await wait_msg.edit_text(
            "✅ **Custom Thumbnail Saved!**\n\n"
            "Ab jab aap PDF bhejenge, yahi photo preview me dikhai degi.",
            parse_mode="Markdown"
        )
    except Exception as e:
        await wait_msg.edit_text(f"❌ Thumbnail save karne me error: {e}")


# --- Document Handler (PDF Receive) ---
async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document
    file_name = doc.file_name or "document.pdf"

    if not file_name.lower().endswith(".pdf"):
        await update.message.reply_text("⚠️ Kripya sirf **PDF** file bhejein.", parse_mode="Markdown")
        return

    # User state save
    USER_STATE[user_id] = {
        "file_id": doc.file_id,
        "original_name": file_name
    }

    reply_text = (
        f"📩 **PDF Receive ho gayi!** (`{file_name}`)\n\n"
        f"✍️ Kripya is PDF ka **NAYA NAAM** (Name) type karke bhejein:\n"
        f"(Jaise: **Polity Notes** ya **Chapter 1.pdf**)\n\n"
        f"_Process cancel karne ke liye /cancel likhein._"
    )
    await update.message.reply_text(reply_text, parse_mode="Markdown")


# --- Text Handler (Receive New Name & Rename) ---
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in USER_STATE:
        await update.message.reply_text("Pehle koi PDF file bhejein jise rename karna ho.")
        return

    state = USER_STATE.pop(user_id)
    file_id = state["file_id"]
    new_name_raw = update.message.text.strip()
    safe_filename = sanitize_filename(new_name_raw)

    status_msg = await update.message.reply_text("⏳ **PDF download ho rahi hai... Kripya thoda intezar karein.**", parse_mode="Markdown")

    file_path = os.path.join(DOWNLOAD_DIR, f"{user_id}_{safe_filename}")

    try:
        # Download original PDF
        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(file_path)

        await status_msg.edit_text("📤 **Renamed PDF upload ho rahi hai...**", parse_mode="Markdown")

        # Custom thumbnail check
        thumb_path = os.path.join(THUMBNAIL_DIR, f"{user_id}.jpg")
        thumb_file = open(thumb_path, "rb") if os.path.exists(thumb_path) else None

        caption = f"📄 **{safe_filename}**\n\n⚡ _Processed by PDF Renamer Bot_"

        with open(file_path, "rb") as doc_file:
            await update.message.reply_document(
                document=doc_file,
                filename=safe_filename,
                caption=caption,
                thumbnail=thumb_file,
                parse_mode="Markdown"
            )

        if thumb_file:
            thumb_file.close()

        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"❌ Error aaya: {e}")

    finally:
        # Cleanup temporary downloaded PDF
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass


async def main():
    if not BOT_TOKEN:
        print("[!] ERROR: BOT_TOKEN environment variable set nahi hai!")
        return

    # Start health check server for Render free tier
    await start_web_server()

    # Create telegram application
    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers register karein
    application.add_handler(CommandHandler(["start", "help"], start_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("delthumb", delthumb_command))
    application.add_handler(CommandHandler("viewthumb", viewthumb_command))
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    application.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Start polling
    print("[✓] Bot is up and running successfully with ONLY BOT_TOKEN!")
    async with application:
        await application.start()
        await application.updater.start_polling()
        # Keep running
        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())

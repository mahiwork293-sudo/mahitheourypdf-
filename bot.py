import os
import re
import html
import asyncio
from aiohttp import web
from PIL import Image
from telegram import Update
from telegram.request import HTTPXRequest
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
    # Replace characters not allowed in filenames with '-'
    clean_name = re.sub(r'[\\/*?:"<>|]', '-', filename)
    # Replace multiple spaces with single space
    clean_name = re.sub(r'\s+', ' ', clean_name)
    return clean_name


def process_thumbnail(image_path: str):
    """
    Telegram Bot API Rules for Document Thumbnail:
    1. Format: JPEG
    2. Dimensions: Max 320x320 pixels
    3. Size: Must be strictly under 200 KB
    """
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img.thumbnail((320, 320), Image.Resampling.LANCZOS)
            img.save(image_path, "JPEG", quality=85, optimize=True)
        return True
    except Exception as e:
        print(f"[!] Thumbnail processing error: {e}")
        return False


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
    safe_name = html.escape(user.first_name or "User")
    welcome_text = (
        f"👋 <b>Namaste {safe_name}!</b>\n\n"
        "Main ek <b>PDF Renamer & Custom Thumbnail Bot</b> hoon.\n\n"
        "📌 <b>Kaise use karein:</b>\n"
        "1. Sabse pehle koi bhi <b>Photo</b> bhejein jo aap PDF ka thumbnail banana chahte hain.\n"
        "2. Fir apni <b>PDF file</b> bhejein.\n"
        "3. PDF ka naya naam type karke bhejein.\n\n"
        "✨ <b>Commands:</b>\n"
        "• <code>/delthumb</code> - Saved thumbnail delete karne ke liye\n"
        "• <code>/viewthumb</code> - Current thumbnail dekhne ke liye\n"
        "• <code>/cancel</code> - Ongoing process cancel karne ke liye"
    )
    await update.message.reply_text(welcome_text, parse_mode="HTML")


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
            await update.message.reply_photo(photo=photo, caption="Yeh aapka current saved thumbnail hai (Telegram standard 320x320 format me).")
    else:
        await update.message.reply_text("Aapne abhi tak koi custom thumbnail set nahi kiya hai.")


# --- Photo Handler (Thumbnail Save & Resize) ---
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    thumb_path = os.path.join(THUMBNAIL_DIR, f"{user_id}.jpg")

    wait_msg = await update.message.reply_text("⏳ Saving & processing thumbnail...")
    try:
        photo_file = await update.message.photo[-1].get_file()
        await photo_file.download_to_drive(thumb_path)

        success = process_thumbnail(thumb_path)
        if success:
            await wait_msg.edit_text(
                "✅ <b>Custom Thumbnail Saved!</b>\n\n"
                "Ab jab aap PDF bhejenge, yahi photo preview me dikhai degi.",
                parse_mode="HTML"
            )
        else:
            await wait_msg.edit_text("⚠️ Photo save ho gayi par thumbnail format optimize karne me issue aaya.")
    except Exception as e:
        await wait_msg.edit_text(f"❌ Thumbnail save karne me error: {e}")


# --- Document Handler (PDF Receive) ---
async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    doc = update.message.document
    file_name = doc.file_name or "document.pdf"

    if not file_name.lower().endswith(".pdf"):
        await update.message.reply_text("⚠️ Kripya sirf <b>PDF</b> file bhejein.", parse_mode="HTML")
        return

    USER_STATE[user_id] = {
        "file_id": doc.file_id,
        "original_name": file_name
    }

    safe_orig_name = html.escape(file_name)
    reply_text = (
        f"📩 <b>PDF Receive ho gayi!</b> (<code>{safe_orig_name}</code>)\n\n"
        f"✍️ Kripya is PDF ka <b>NAYA NAAM</b> (Name) type karke bhejein:\n"
        f"(Jaise: <b>Polity Notes</b> ya <b>Chapter 1.pdf</b>)\n\n"
        f"<i>Process cancel karne ke liye /cancel likhein.</i>"
    )
    await update.message.reply_text(reply_text, parse_mode="HTML")


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

    status_msg = await update.message.reply_text("⏳ <b>PDF download ho rahi hai... Kripya thoda intezar karein.</b>", parse_mode="HTML")

    file_path = os.path.join(DOWNLOAD_DIR, f"{user_id}_{safe_filename}")

    try:
        # Download original PDF (Timeout set to 120s)
        tg_file = await context.bot.get_file(file_id, read_timeout=120, write_timeout=120)
        await tg_file.download_to_drive(file_path, read_timeout=120, write_timeout=120)

        await status_msg.edit_text("📤 <b>Renamed PDF upload ho rahi hai...</b>", parse_mode="HTML")

        # Custom thumbnail check
        thumb_path = os.path.join(THUMBNAIL_DIR, f"{user_id}.jpg")
        
        safe_display_name = html.escape(safe_filename)
        caption = f"📄 <b>{safe_display_name}</b>\n\n⚡ <i>Processed by PDF Renamer Bot</i>"

        # Upload with 120s timeout so "Timed out" error never happens
        if os.path.exists(thumb_path):
            with open(file_path, "rb") as doc_file, open(thumb_path, "rb") as thumb_file:
                await update.message.reply_document(
                    document=doc_file,
                    filename=safe_filename,
                    caption=caption,
                    thumbnail=thumb_file,
                    parse_mode="HTML",
                    read_timeout=120,
                    write_timeout=120
                )
        else:
            with open(file_path, "rb") as doc_file:
                await update.message.reply_document(
                    document=doc_file,
                    filename=safe_filename,
                    caption=caption,
                    parse_mode="HTML",
                    read_timeout=120,
                    write_timeout=120
                )

        # Status message delete karein jab upload complete ho jaye
        try:
            await status_msg.delete()
        except Exception:
            pass

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

    # Network timeouts: 120s limit taaki badi PDF par timeout na aaye
    request_config = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=60.0,
        read_timeout=120.0,
        write_timeout=120.0
    )

    # Create telegram application with custom timeouts
    application = Application.builder().token(BOT_TOKEN).request(request_config).build()

    # Handlers register karein
    application.add_handler(CommandHandler(["start", "help"], start_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("delthumb", delthumb_command))
    application.add_handler(CommandHandler("viewthumb", viewthumb_command))
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    application.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # Start polling
    print("[✓] Bot is up and running successfully!")
    async with application:
        await application.start()
        await application.updater.start_polling()
        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())

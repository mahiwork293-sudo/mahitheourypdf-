import os
import re
import asyncio
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import Message

# Configuration - Environment Variables
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", "8080"))

# Directories
DOWNLOAD_DIR = "downloads"
THUMBNAIL_DIR = "thumbnails"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(THUMBNAIL_DIR, exist_ok=True)

# User state storage: {user_id: {"step": "waiting_name", "message": message}}
USER_STATE = {}

# Initialize Pyrogram Bot Client
bot = Client(
    "pdf_renamer_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)


def sanitize_filename(filename: str) -> str:
    """Clean filename by removing invalid characters for files."""
    filename = filename.strip()
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"
    # Replace characters not allowed in filenames
    clean_name = re.sub(r'[\\/*?:"<>|]', '_', filename)
    # Replace consecutive spaces or underscores with single underscore
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
@bot.on_message(filters.command(["start", "help"]) & filters.private)
async def start_handler(client: Client, message: Message):
    welcome_text = (
        f"👋 **Namaste {message.from_user.first_name}!**\n\n"
        "Main ek **PDF Renamer & Custom Thumbnail Bot** hoon.\n\n"
        "📌 **Kaise use karein:**\n"
        "1. Sabse pehle koi bhi **Photo** bhejein jo aap PDF ka cover/thumbnail banana chahte hain.\n"
        "2. Fir apni **PDF file** bhejein.\n"
        "3. PDF ka naya naam type karke bhejein.\n\n"
        "✨ **Commands:**\n"
        "• `/delthumb` - Saved thumbnail delete karne ke liye\n"
        "• `/viewthumb` - Current thumbnail dekhne ke liye\n"
        "• `/cancel` - Ongoing process cancel karne ke liye"
    )
    await message.reply_text(welcome_text)


@bot.on_message(filters.command("cancel") & filters.private)
async def cancel_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in USER_STATE:
        del USER_STATE[user_id]
        await message.reply_text("❌ Process cancel kar diya gaya hai.")
    else:
        await message.reply_text("Koi active process nahi chal raha hai.")


@bot.on_message(filters.command("delthumb") & filters.private)
async def delthumb_handler(client: Client, message: Message):
    user_id = message.from_user.id
    thumb_path = os.path.join(THUMBNAIL_DIR, f"{user_id}.jpg")
    if os.path.exists(thumb_path):
        os.remove(thumb_path)
        await message.reply_text("🗑️ Aapka custom thumbnail delete kar diya gaya hai!")
    else:
        await message.reply_text("Aapne abhi tak koi custom thumbnail set nahi kiya hai.")


@bot.on_message(filters.command("viewthumb") & filters.private)
async def viewthumb_handler(client: Client, message: Message):
    user_id = message.from_user.id
    thumb_path = os.path.join(THUMBNAIL_DIR, f"{user_id}.jpg")
    if os.path.exists(thumb_path):
        await message.reply_photo(photo=thumb_path, caption="Yeh aapka current saved thumbnail hai.")
    else:
        await message.reply_text("Aapne abhi tak koi custom thumbnail set nahi kiya hai.")


# --- Photo Handler (Thumbnail) ---
@bot.on_message(filters.photo & filters.private)
async def save_thumbnail(client: Client, message: Message):
    user_id = message.from_user.id
    thumb_path = os.path.join(THUMBNAIL_DIR, f"{user_id}.jpg")

    wait_msg = await message.reply_text("⏳ Saving thumbnail...")
    try:
        await client.download_media(message, file_name=thumb_path)
        await wait_msg.edit_text(
            "✅ **Custom Thumbnail Saved!**\n\n"
            "Ab jab aap PDF bhejenge, yahi photo preview me dikhai degi."
        )
    except Exception as e:
        await wait_msg.edit_text(f"❌ Thumbnail save karne me error aaya: `{e}`")


# --- Document Handler (PDF Receive) ---
@bot.on_message(filters.document & filters.private)
async def receive_pdf(client: Client, message: Message):
    user_id = message.from_user.id
    file_name = message.document.file_name or "document.pdf"

    if not file_name.lower().endswith(".pdf"):
        await message.reply_text("⚠️ Kripya sirf **PDF** file bhejein.")
        return

    # Store state
    USER_STATE[user_id] = {
        "step": "waiting_name",
        "doc_message": message,
        "original_filename": file_name
    }

    reply_text = (
        f"📩 **PDF Receive ho gayi!** (`{file_name}`)\n\n"
        f"✍️ Kripya is PDF ka **NAYA NAAM** (Name) type karke bhejein:\n"
        f"(Jaise: **Polity Notes** ya **Chapter 1.pdf**)\n\n"
        f"_Process cancel karne ke liye /cancel likhein._"
    )
    await message.reply_text(reply_text)


# --- Text Handler (Receive New Name and Process) ---
@bot.on_message(filters.text & filters.private & ~filters.command(["start", "help", "cancel", "delthumb", "viewthumb"]))
async def process_rename(client: Client, message: Message):
    user_id = message.from_user.id

    if user_id not in USER_STATE or USER_STATE[user_id].get("step") != "waiting_name":
        await message.reply_text("Pehle koi PDF file bhejein jise rename karna ho.")
        return

    doc_info = USER_STATE.pop(user_id)
    doc_message = doc_info["doc_message"]
    new_name_raw = message.text.strip()
    safe_filename = sanitize_filename(new_name_raw)

    status_msg = await message.reply_text("⏳ **PDF download ho rahi hai... Kripya thoda intezar karein.**")

    temp_input_path = None
    renamed_path = None

    try:
        # Download PDF
        temp_input_path = await client.download_media(
            doc_message,
            file_name=os.path.join(DOWNLOAD_DIR, f"{user_id}_{doc_message.id}.pdf")
        )

        await status_msg.edit_text("⚙️ **Thumbnail apply ho raha hai aur rename kiya ja raha hai...**")

        # Rename file
        renamed_path = os.path.join(DOWNLOAD_DIR, f"{user_id}_{safe_filename}")
        if os.path.exists(renamed_path):
            os.remove(renamed_path)
        os.rename(temp_input_path, renamed_path)

        # Check for custom thumbnail
        thumb_path = os.path.join(THUMBNAIL_DIR, f"{user_id}.jpg")
        thumb_to_send = thumb_path if os.path.exists(thumb_path) else None

        await status_msg.edit_text("📤 **Renamed PDF upload ho rahi hai...**")

        # Send uploaded document with thumbnail & caption
        caption = f"📄 **{safe_filename}**\n\n⚡ _Processed by PDF Renamer Bot_"
        await client.send_document(
            chat_id=message.chat.id,
            document=renamed_path,
            file_name=safe_filename,
            caption=caption,
            thumb=thumb_to_send
        )

        await status_msg.delete()

    except Exception as e:
        await status_msg.edit_text(f"❌ Error aaya: `{e}`")

    finally:
        # Clean up temporary downloaded files
        for path in [temp_input_path, renamed_path]:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass


# --- Main entry point ---
async def main():
    print("[*] Starting Web Server for Render Free Tier...")
    await start_web_server()

    print("[*] Starting Telegram Client...")
    await bot.start()
    print("[✓] Bot is up and running successfully!")

    await idle()
    await bot.stop()


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())

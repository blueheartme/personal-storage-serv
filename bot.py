import os
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import aiohttp
import aiofiles

# تنظیمات لاگ - فقط خطاها
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.ERROR
)
logger = logging.getLogger(__name__)

# تنظیمات
BOT_TOKEN = os.getenv('BOT_TOKEN')
STORAGE_API_URL = os.getenv('STORAGE_API_URL', 'http://localhost:8080')
ADMIN_ID = os.getenv('ADMIN_ID')
SECRET_TOKEN = os.getenv('SECRET_TOKEN')

def check_admin(user_id):
    """بررسی ادمین بودن"""
    return str(user_id) == ADMIN_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دستور شروع"""
    if not check_admin(update.effective_user.id):
        await update.message.reply_text("⛔️ شما دسترسی ندارید")
        return
    
    await update.message.reply_text(
        "🤖 **ربات تبدیل فایل به لینک**\n\n"
        "📤 فایل خود را ارسال کنید (حداکثر 2GB)\n"
        "🔗 لینک دانلود دریافت کنید\n\n"
        "**دستورات:**\n"
        "📊 /info - اطلاعات فضای ذخیره‌سازی\n"
        "🗑 /cleanup - پاکسازی فایل‌های قدیمی\n"
        "ℹ️ /help - راهنما",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """راهنما"""
    if not check_admin(update.effective_user.id):
        return
    
    await update.message.reply_text(
        "📖 **راهنمای استفاده**\n\n"
        "1️⃣ هر فایلی (عکس، ویدیو، سند و...) را ارسال کنید\n"
        "2️⃣ ربات فایل را آپلود می‌کند\n"
        "3️⃣ لینک مستقیم دریافت می‌کنید\n"
        "4️⃣ فایل بعد از 6 ساعت حذف می‌شود\n\n"
        "⚠️ **نکات مهم:**\n"
        "• حداکثر حجم: 2GB\n"
        "• مدت نگهداری: 6 ساعت\n"
        "• فضای کل: 10GB\n"
        "• فقط شما دسترسی دارید",
        parse_mode='Markdown'
    )

async def get_storage_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اطلاعات فضا"""
    if not check_admin(update.effective_user.id):
        await update.message.reply_text("⛔️ شما دسترسی ندارید")
        return
    
    headers = {
        'X-Auth-Token': SECRET_TOKEN,
        'X-User-ID': ADMIN_ID
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{STORAGE_API_URL}/storage/info",
                headers=headers
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    storage = data['storage']
                    files = data.get('files', [])
                    
                    message = (
                        f"📊 **اطلاعات ذخیره‌سازی**\n\n"
                        f"💾 فضای استفاده شده: {storage['total_gb']}/{storage['max_gb']} GB\n"
                        f"📁 تعداد فایل‌ها: {storage['file_count']}\n"
                        f"📈 درصد استفاده: {(storage['total_gb']/storage['max_gb']*100):.1f}%\n\n"
                    )
                    
                    if files:
                        message += "**فایل‌های اخیر:**\n"
                        for i, f in enumerate(files[:5], 1):
                            message += (
                                f"{i}. `{f['name'][:25]}...`\n"
                                f"   📦 {f['size_mb']} MB | "
                                f"⏱ باقی‌مانده: {f['expires_in_hours']:.1f}h\n"
                            )
                    
                    await update.message.reply_text(message, parse_mode='Markdown')
                else:
                    await update.message.reply_text("❌ خطا در دریافت اطلاعات")
    
    except Exception as e:
        logger.error(f"Info error: {str(e)}")
        await update.message.reply_text(f"❌ خطا: {str(e)}")

async def cleanup_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پاکسازی فایل‌ها"""
    if not check_admin(update.effective_user.id):
        await update.message.reply_text("⛔️ شما دسترسی ندارید")
        return
    
    headers = {
        'X-Auth-Token': SECRET_TOKEN,
        'X-User-ID': ADMIN_ID
    }
    
    status_msg = await update.message.reply_text("🗑 در حال پاکسازی...")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{STORAGE_API_URL}/cleanup",
                headers=headers
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    deleted = data.get('deleted', [])
                    
                    if deleted:
                        message = f"✅ {len(deleted)} فایل حذف شد:\n\n"
                        for item in deleted[:10]:
                            reason = "منقضی شده" if item['reason'] == 'expired' else "فضا کم"
                            message += f"• `{item['file'][:30]}...` ({reason})\n"
                    else:
                        message = "✅ فایلی برای حذف وجود ندارد"
                    
                    await status_msg.edit_text(message, parse_mode='Markdown')
                else:
                    await status_msg.edit_text("❌ خطا در پاکسازی")
    
    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}")
        await status_msg.edit_text(f"❌ خطا: {str(e)}")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مدیریت فایل‌های ارسالی"""
    if not check_admin(update.effective_user.id):
        await update.message.reply_text("⛔️ شما دسترسی ندارید")
        return
    
    # تشخیص نوع فایل
    if update.message.document:
        file = update.message.document
        file_name = file.file_name
    elif update.message.photo:
        file = update.message.photo[-1]
        file_name = f"photo_{file.file_unique_id}.jpg"
    elif update.message.video:
        file = update.message.video
        file_name = file.file_name or f"video_{file.file_unique_id}.mp4"
    elif update.message.audio:
        file = update.message.audio
        file_name = file.file_name or f"audio_{file.file_unique_id}.mp3"
    elif update.message.voice:
        file = update.message.voice
        file_name = f"voice_{file.file_unique_id}.ogg"
    elif update.message.video_note:
        file = update.message.video_note
        file_name = f"video_note_{file.file_unique_id}.mp4"
    elif update.message.sticker:
        file = update.message.sticker
        file_name = f"sticker_{file.file_unique_id}.webp"
    else:
        await update.message.reply_text("❌ نوع فایل پشتیبانی نمی‌شود")
        return
    
    file_size = file.file_size
    file_size_mb = file_size / (1024 * 1024)
    
    # بررسی حجم
    if file_size > 2 * 1024 * 1024 * 1024:  # 2GB
        await update.message.reply_text("❌ حجم فایل بیش از 2GB است")
        return
    
    # پیام شروع
    status_msg = await update.message.reply_text(
        f"⏳ **در حال آپلود...**\n\n"
        f"📄 نام: `{file_name}`\n"
        f"📦 حجم: {file_size_mb:.2f} MB\n"
        f"🔄 لطفاً صبر کنید...",
        parse_mode='Markdown'
    )
    
    try:
        # دانلود فایل از تلگرام
        tg_file = await context.bot.get_file(file.file_id)
        file_path = f"/tmp/{file.file_id}"
        
        # دانلود
        await tg_file.download_to_drive(file_path)
        
        # آپلود به سرور
        headers = {
            'X-Auth-Token': SECRET_TOKEN,
            'X-User-ID': ADMIN_ID
        }
        
        async with aiohttp.ClientSession() as session:
            with open(file_path, 'rb') as f:
                data = aiohttp.FormData()
                data.add_field('file', f, filename=file_name)
                
                async with session.post(
                    f"{STORAGE_API_URL}/upload",
                    data=data,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=600)
                ) as response:
                    
                    # حذف فایل موقت
                    try:
                        os.remove(file_path)
                    except:
                        pass
                    
                    if response.status == 201:
                        result = await response.json()
                        
                        message = (
                            f"✅ **آپلود موفق!**\n\n"
                            f"📄 نام: `{file_name}`\n"
                            f"📦 حجم: {result['size_mb']} MB\n"
                            f"⏱ انقضا: {result['expires_in_hours']} ساعت\n\n"
                            f"🔗 **لینک‌ها:**\n"
                            f"👁 مشاهده: `{result['url']}`\n"
                            f"⬇️ دانلود: `{result['direct_url']}`\n\n"
                            f"💾 فضا: {result['storage']['used_gb']}/{result['storage']['max_gb']} GB"
                        )
                        
                        if result.get('warning'):
                            message += f"\n\n⚠️ {result['warning']}"
                        
                        await status_msg.edit_text(message, parse_mode='Markdown')
                    
                    elif response.status == 507:
                        error_data = await response.json()
                        await status_msg.edit_text(
                            f"❌ **فضا کافی نیست!**\n\n"
                            f"💾 فضای فعلی: {error_data.get('current_gb', 0)} GB\n"
                            f"📊 حداکثر: {error_data.get('max_gb', 10)} GB\n\n"
                            f"از /cleanup برای پاکسازی استفاده کنید"
                        )
                    
                    else:
                        error_data = await response.json()
                        await status_msg.edit_text(
                            f"❌ خطا در آپلود: {error_data.get('error', 'نامشخص')}"
                        )
    
    except asyncio.TimeoutError:
        await status_msg.edit_text("❌ زمان آپلود تمام شد. فایل خیلی بزرگ است.")
    
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        await status_msg.edit_text(f"❌ خطا: {str(e)}")
        
        # حذف فایل موقت
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass

def main():
    """اجرای ربات"""
    
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        return
    
    if not ADMIN_ID:
        logger.error("ADMIN_ID not set!")
        return
    
    # ساخت اپلیکیشن
    app = Application.builder().token(BOT_TOKEN).build()
    
    # هندلرها
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("info", get_storage_info))
    app.add_handler(CommandHandler("cleanup", cleanup_files))
    
    # هندلرهای فایل
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_document))
    app.add_handler(MessageHandler(filters.VIDEO, handle_document))
    app.add_handler(MessageHandler(filters.AUDIO, handle_document))
    app.add_handler(MessageHandler(filters.VOICE, handle_document))
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_document))
    app.add_handler(MessageHandler(filters.Sticker.ALL, handle_document))
    
    print("✅ Bot started successfully!")
    logger.info("Bot is running...")
    
    # اجرای ربات
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()

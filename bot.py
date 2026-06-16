import os
import logging
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from database import (
    init_db, add_user, get_users_count, is_admin, add_admin, remove_admin,
    get_admins, add_channel, remove_channel, get_channels,
    add_video, get_video, delete_video, increment_view, get_video_stats
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))


# ─── Helpers ────────────────────────────────────────────────────────────────

async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    """Return list of channels the user has NOT joined."""
    channels = get_channels()
    not_joined = []
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(f"@{ch['username']}", user_id)
            if member.status in ("left", "kicked"):
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    return not_joined


def membership_keyboard(not_joined: list[dict], video_id: str) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(f"📢 {ch['title']}", url=ch["link"])] for ch in not_joined]
    buttons.append([InlineKeyboardButton("✅ عضو شدم", callback_data=f"check_{video_id}")])
    return InlineKeyboardMarkup(buttons)


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def require_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not is_owner(user_id) and not is_admin(user_id):
            await update.message.reply_text("⛔ شما دسترسی ادمین ندارید.")
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper


# ─── Commands ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username)

    args = context.args
    if args:
        video_id = args[0]
        await send_video_to_user(update, context, video_id)
        return

    await update.message.reply_text(
        f"سلام {user.first_name}! 👋\n\n"
        "برای دریافت ویدیو، لینک مربوطه را باز کنید."
    )


async def send_video_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE, video_id: str):
    user_id = update.effective_user.id

    not_joined = await check_membership(user_id, context)
    if not_joined:
        kb = membership_keyboard(not_joined, video_id)
        await update.message.reply_text(
            "⚠️ برای دریافت ویدیو، ابتدا در کانال‌های زیر عضو شوید:",
            reply_markup=kb
        )
        return

    video = get_video(video_id)
    if not video:
        await update.message.reply_text("❌ ویدیو پیدا نشد یا حذف شده است.")
        return

    increment_view(video_id, user_id)
    await context.bot.send_video(
        chat_id=update.effective_chat.id,
        video=video["file_id"],
        caption=video.get("caption") or "",
    )


async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    video_id = query.data.split("_", 1)[1]

    not_joined = await check_membership(user_id, context)
    if not_joined:
        kb = membership_keyboard(not_joined, video_id)
        await query.edit_message_text(
            "⚠️ هنوز در همه کانال‌ها عضو نشده‌اید:",
            reply_markup=kb
        )
        return

    video = get_video(video_id)
    if not video:
        await query.edit_message_text("❌ ویدیو پیدا نشد یا حذف شده است.")
        return

    await query.delete_message()
    increment_view(video_id, user_id)
    await context.bot.send_video(
        chat_id=query.message.chat_id,
        video=video["file_id"],
        caption=video.get("caption") or "",
    )


# ─── Admin: upload video ─────────────────────────────────────────────────────

@require_admin
async def upload_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin sends a video → bot saves it and returns a shareable link."""
    message = update.message
    if not message.video:
        await message.reply_text("❌ لطفاً یک ویدیو ارسال کنید.")
        return

    video_id = uuid.uuid4().hex[:10]
    file_id = message.video.file_id
    caption = message.caption or ""
    uploader = message.from_user.id

    add_video(video_id, file_id, caption, uploader)

    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={video_id}"
    await message.reply_text(
        f"✅ ویدیو ذخیره شد!\n\n"
        f"🆔 شناسه: `{video_id}`\n"
        f"🔗 لینک: {link}",
        parse_mode="Markdown"
    )


# ─── Admin: stats ────────────────────────────────────────────────────────────

@require_admin
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        total_users = get_users_count()
        await update.message.reply_text(
            f"📊 *آمار ربات*\n\n"
            f"👤 کاربران: {total_users}\n",
            parse_mode="Markdown"
        )
        return

    video_id = args[0]
    st = get_video_stats(video_id)
    if not st:
        await update.message.reply_text("❌ ویدیو پیدا نشد.")
        return

    await update.message.reply_text(
        f"📊 *آمار ویدیو* `{video_id}`\n\n"
        f"👁 بازدید کل: {st['view_count']}\n"
        f"👥 بینندگان یکتا: {st['unique_viewers']}\n"
        f"📅 آپلود: {st['uploaded_at']}",
        parse_mode="Markdown"
    )


@require_admin
async def delete_video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("استفاده: /delvideo <video_id>")
        return
    video_id = args[0]
    delete_video(video_id)
    await update.message.reply_text(f"✅ ویدیو `{video_id}` حذف شد.", parse_mode="Markdown")


# ─── Admin: channel management ───────────────────────────────────────────────

@require_admin
async def add_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /addchannel @username Title https://t.me/..."""
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("استفاده: /addchannel @username عنوان لینک")
        return
    username = args[0].lstrip("@")
    title = args[1]
    link = args[2]
    add_channel(username, title, link)
    await update.message.reply_text(f"✅ کانال @{username} اضافه شد.")


@require_admin
async def remove_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("استفاده: /removechannel @username")
        return
    username = args[0].lstrip("@")
    remove_channel(username)
    await update.message.reply_text(f"✅ کانال @{username} حذف شد.")


@require_admin
async def list_channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = get_channels()
    if not channels:
        await update.message.reply_text("هیچ کانالی ثبت نشده.")
        return
    text = "📢 *کانال‌های اجباری:*\n\n"
    for ch in channels:
        text += f"• @{ch['username']} — {ch['title']}\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── Owner: admin management ─────────────────────────────────────────────────

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ فقط مالک می‌تواند ادمین اضافه کند.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("استفاده: /addadmin <user_id>")
        return
    try:
        new_admin_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ آیدی نامعتبر.")
        return
    add_admin(new_admin_id, update.effective_user.id)
    await update.message.reply_text(f"✅ کاربر {new_admin_id} به عنوان ادمین اضافه شد.")


async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ فقط مالک می‌تواند ادمین حذف کند.")
        return
    args = context.args
    if not args:
        await update.message.reply_text("استفاده: /removeadmin <user_id>")
        return
    try:
        admin_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ آیدی نامعتبر.")
        return
    remove_admin(admin_id)
    await update.message.reply_text(f"✅ ادمین {admin_id} حذف شد.")


async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return
    admins = get_admins()
    if not admins:
        await update.message.reply_text("هیچ ادمینی ثبت نشده.")
        return
    text = "👮 *ادمین‌ها:*\n\n"
    for a in admins:
        text += f"• `{a['user_id']}` (اضافه شده توسط {a['added_by']})\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── Help ────────────────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_owner(user_id) or is_admin(user_id):
        text = (
            "🛠 *دستورات ادمین:*\n\n"
            "📤 ارسال ویدیو → ربات لینک می‌دهد\n\n"
            "*کانال‌ها:*\n"
            "/addchannel @username عنوان لینک\n"
            "/removechannel @username\n"
            "/channels — لیست کانال‌ها\n\n"
            "*ویدیو:*\n"
            "/stats — آمار کلی\n"
            "/stats <id> — آمار ویدیو\n"
            "/delvideo <id> — حذف ویدیو\n\n"
            "*ادمین (فقط مالک):*\n"
            "/addadmin <id>\n"
            "/removeadmin <id>\n"
            "/admins"
        )
    else:
        text = "برای دریافت ویدیو، لینک مستقیم را باز کنید. 🎬"
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("delvideo", delete_video_command))
    app.add_handler(CommandHandler("addchannel", add_channel_command))
    app.add_handler(CommandHandler("removechannel", remove_channel_command))
    app.add_handler(CommandHandler("channels", list_channels_command))
    app.add_handler(CommandHandler("addadmin", add_admin_command))
    app.add_handler(CommandHandler("removeadmin", remove_admin_command))
    app.add_handler(CommandHandler("admins", list_admins_command))
    app.add_handler(MessageHandler(filters.VIDEO & filters.ChatType.PRIVATE, upload_video))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern=r"^check_"))

    logger.info("Bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

import os
import logging
import uuid
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

from database import (
    init_db, add_user, get_users_count, is_admin, add_admin, remove_admin,
    get_admins, add_channel, remove_channel, get_channels,
    add_video, get_video, delete_video, increment_view, get_video_stats,
    get_videos_paginated,
    get_spam_settings, set_setting, check_and_record_spam, unblock_user,
    create_bundle, add_to_bundle, get_bundle, get_bundle_videos,
    delete_bundle, get_bundles_paginated,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID  = int(os.environ.get("OWNER_ID", "0"))
PAGE_SIZE = 5

# session های ساخت باندل  {user_id: {"bundle_id": str, "title": str, "videos": list}}
bundle_sessions: dict[int, dict] = {}


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

def escape_md(text: str) -> str:
    """Escape کاراکترهای خاص MarkdownV2."""
    if not text:
        return ""
    for ch in r"\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text


def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


def require_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if not is_owner(uid) and not is_admin(uid):
            await update.message.reply_text("⛔ شما دسترسی ادمین ندارید.")
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper


async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    """کانال‌هایی که کاربر عضو نیست را برمی‌گرداند."""
    not_joined = []
    for ch in get_channels():
        try:
            identifier = (
                int(ch["username"])
                if ch["username"].lstrip("-").isdigit()
                else f"@{ch['username']}"
            )
            member = await context.bot.get_chat_member(identifier, user_id)
            if member.status in ("left", "kicked"):
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    return not_joined


def membership_keyboard(not_joined: list[dict], content_id: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(f"📢 {ch['title']}", url=ch["link"])]
        for ch in not_joined
    ]
    buttons.append([InlineKeyboardButton("✅ عضو شدم", callback_data=f"check_{content_id}")])
    return InlineKeyboardMarkup(buttons)


def schedule_delete(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                    message_id: int, delay: int = 60):
    """حذف پیام با تأخیر از طریق job_queue (پایدارتر از create_task)."""
    async def _delete(ctx: ContextTypes.DEFAULT_TYPE):
        try:
            await ctx.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

    context.application.job_queue.run_once(
        _delete,
        when=delay,
        name=f"del_{chat_id}_{message_id}",
    )


async def _send_media(context: ContextTypes.DEFAULT_TYPE,
                      chat_id: int, video: dict, caption: str):
    """ارسال عکس یا ویدیو بر اساس content_type."""
    if video.get("content_type") == "photo":
        return await context.bot.send_photo(
            chat_id=chat_id, photo=video["file_id"], caption=caption
        )
    return await context.bot.send_video(
        chat_id=chat_id, video=video["file_id"], caption=caption
    )


# ═══════════════════════════════════════════════════════════════
#  Admin Panel
# ═══════════════════════════════════════════════════════════════

def admin_panel_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📊 آمار کلی",          callback_data="panel_stats")],
        [InlineKeyboardButton("📢 لیست کانال‌ها",      callback_data="panel_channels")],
        [
            InlineKeyboardButton("➕ افزودن کانال",    callback_data="panel_addchannel"),
            InlineKeyboardButton("➖ حذف کانال",       callback_data="panel_removechannel"),
        ],
        [InlineKeyboardButton("🎬 لیست محتواها",       callback_data="panel_videos_0")],
        [InlineKeyboardButton("🗑 حذف محتوا",          callback_data="panel_delvideo")],
        [InlineKeyboardButton("📦 لیست باندل‌ها",      callback_data="panel_bundles_0")],
        [InlineKeyboardButton("🚫 تنظیمات ضد اسپم",   callback_data="panel_spam_settings")],
    ]
    if is_owner(user_id):
        buttons += [
            [InlineKeyboardButton("👮 لیست ادمین‌ها",  callback_data="panel_admins")],
            [
                InlineKeyboardButton("➕ افزودن ادمین", callback_data="panel_addadmin"),
                InlineKeyboardButton("➖ حذف ادمین",    callback_data="panel_removeadmin"),
            ],
        ]
    return InlineKeyboardMarkup(buttons)


PANEL_BACK_KB = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🔙 بازگشت", callback_data="panel_back")]]
)

PANEL_HELP_TEXTS: dict[str, str] = {
    "panel_addchannel": (
        "➕ *افزودن کانال*\n\n"
        "کانال عمومی:\n`/addchannel @username عنوان لینک`\n\n"
        "کانال خصوصی:\n`/addchannel -1001234567890 عنوان لینک_دعوت`"
    ),
    "panel_removechannel": (
        "➖ *حذف کانال*\n\n"
        "`/removechannel @username`\n"
        "یا: `/removechannel -100xxxxx`"
    ),
    "panel_delvideo": (
        "🗑 *حذف محتوا*\n\n`/delvideo <id>`"
    ),
    "panel_addadmin": (
        "➕ *افزودن ادمین*\n\n`/addadmin <user_id>`"
    ),
    "panel_removeadmin": (
        "➖ *حذف ادمین*\n\n`/removeadmin <user_id>`"
    ),
}


# ═══════════════════════════════════════════════════════════════
#  Spam Settings Panel
# ═══════════════════════════════════════════════════════════════

SPAM_STEPS: dict[str, tuple] = {
    "hits":   ("spam_max_hits",        1,   1,   20),
    "window": ("spam_window_seconds", 10,  10,  600),
    "block":  ("spam_block_seconds",  30,  30, 3600),
}


def spam_settings_text() -> str:
    cfg    = get_spam_settings()
    hits   = cfg.get("spam_max_hits",       4)
    window = cfg.get("spam_window_seconds", 60)
    block  = cfg.get("spam_block_seconds",  120)
    return (
        "🚫 *تنظیمات ضد اسپم*\n\n"
        f"🔢 حداکثر درخواست مجاز: *{hits}*\n"
        f"⏱ پنجره زمانی: *{window} ثانیه*\n"
        f"🔒 مدت بلاک: *{block} ثانیه* \\({block // 60} دقیقه\\)\n\n"
        "با دکمه‌های ➕/➖ مقادیر را تغییر دهید\\.\n"
        "برای آنبلاک: `/unblock <user_id>`"
    )


def spam_settings_keyboard() -> InlineKeyboardMarkup:
    cfg    = get_spam_settings()
    hits   = cfg.get("spam_max_hits",       4)
    window = cfg.get("spam_window_seconds", 60)
    block  = cfg.get("spam_block_seconds",  120)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔢 حداکثر درخواست: {hits}",    callback_data="spam_noop")],
        [InlineKeyboardButton("➖", callback_data="spam_hits_dec"),
         InlineKeyboardButton("➕", callback_data="spam_hits_inc")],
        [InlineKeyboardButton(f"⏱ پنجره زمانی: {window}ث",     callback_data="spam_noop")],
        [InlineKeyboardButton("➖", callback_data="spam_window_dec"),
         InlineKeyboardButton("➕", callback_data="spam_window_inc")],
        [InlineKeyboardButton(f"🔒 مدت بلاک: {block}ث",         callback_data="spam_noop")],
        [InlineKeyboardButton("➖", callback_data="spam_block_dec"),
         InlineKeyboardButton("➕", callback_data="spam_block_inc")],
        [InlineKeyboardButton("🔙 بازگشت",                      callback_data="panel_back")],
    ])


# ═══════════════════════════════════════════════════════════════
#  Paginated Lists
# ═══════════════════════════════════════════════════════════════

async def show_videos_page(query, context: ContextTypes.DEFAULT_TYPE, page: int):
    try:
        data        = get_videos_paginated(page=page, page_size=PAGE_SIZE)
        videos      = data["videos"]
        total       = data["total"]
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

        if not videos:
            await query.edit_message_text("📭 هیچ محتوایی آپلود نشده.", reply_markup=PANEL_BACK_KB)
            return

        bot_username = (await context.bot.get_me()).username
        lines = [f"🎬 *لیست محتواها* \\(صفحه {page + 1} از {total_pages} \\| مجموع: {total}\\)\n"]

        for v in videos:
            icon        = "🖼" if v["content_type"] == "photo" else "🎬"
            raw_cap     = v["caption"] or "بدون کپشن"
            cap_preview = escape_md((raw_cap[:25] + "…") if len(raw_cap) > 25 else raw_cap)
            vid_id      = v["video_id"]
            link        = f"https://t\\.me/{bot_username}?start={vid_id}"
            date        = escape_md(v["uploaded_at"][:16])
            lines.append(
                f"{icon} `{vid_id}`\n"
                f"   📝 {cap_preview}\n"
                f"   🔗 [لینک دریافت]({link})\n"
                f"   📅 {date}\n"
            )

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"panel_videos_{page - 1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"panel_videos_{page + 1}"))

        kb_rows = ([nav] if nav else []) + [[InlineKeyboardButton("🔙 بازگشت", callback_data="panel_back")]]
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(kb_rows),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error("show_videos_page error", exc_info=True)
        try:
            await query.edit_message_text(f"❌ خطا:\n`{e}`", parse_mode="Markdown",
                                          reply_markup=PANEL_BACK_KB)
        except Exception:
            pass


async def show_bundles_page(query, context: ContextTypes.DEFAULT_TYPE, page: int):
    try:
        data        = get_bundles_paginated(page=page, page_size=PAGE_SIZE)
        bundles     = data["bundles"]
        total       = data["total"]
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

        if not bundles:
            await query.edit_message_text(
                "📭 هیچ باندلی ساخته نشده.\n\nبرای ساخت: /newbundle",
                reply_markup=PANEL_BACK_KB,
            )
            return

        bot_username = (await context.bot.get_me()).username
        lines = [f"📦 *لیست باندل‌ها* \\(صفحه {page + 1} از {total_pages} \\| مجموع: {total}\\)\n"]

        for b in bundles:
            link  = f"https://t\\.me/{bot_username}?start=b_{b['bundle_id']}"
            title = escape_md(b["title"])
            date  = escape_md(b["created_at"][:16])
            lines.append(
                f"📦 `{b['bundle_id']}`\n"
                f"   📝 {title}\n"
                f"   🎬 {b['item_count']} محتوا\n"
                f"   🔗 [لینک باندل]({link})\n"
                f"   📅 {date}\n"
            )

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ قبلی", callback_data=f"panel_bundles_{page - 1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("➡️ بعدی", callback_data=f"panel_bundles_{page + 1}"))

        kb_rows = (
            ([nav] if nav else []) +
            [[InlineKeyboardButton("🗑 حذف باندل", callback_data="panel_delbundle")]] +
            [[InlineKeyboardButton("🔙 بازگشت",    callback_data="panel_back")]]
        )
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(kb_rows),
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error("show_bundles_page error", exc_info=True)
        try:
            await query.edit_message_text(f"❌ خطا:\n`{e}`", parse_mode="Markdown",
                                          reply_markup=PANEL_BACK_KB)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
#  Panel Command & Callback
# ═══════════════════════════════════════════════════════════════

@require_admin
async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(
        "🛠 *پنل مدیریت*\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
        parse_mode="Markdown",
        reply_markup=admin_panel_keyboard(uid),
    )


async def panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    uid    = query.from_user.id
    action = query.data

    if not is_owner(uid) and not is_admin(uid):
        await query.answer("⛔ شما دسترسی ادمین ندارید.", show_alert=True)
        return

    await query.answer()

    # ── بازگشت ───────────────────────────────────────────────────────────────
    if action == "panel_back":
        await query.edit_message_text(
            "🛠 *پنل مدیریت*\n\nیکی از گزینه‌های زیر را انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=admin_panel_keyboard(uid),
        )
        return

    # ── آمار ─────────────────────────────────────────────────────────────────
    if action == "panel_stats":
        await query.edit_message_text(
            f"📊 *آمار ربات*\n\n👤 کاربران: {get_users_count()}",
            parse_mode="Markdown",
            reply_markup=PANEL_BACK_KB,
        )
        return

    # ── کانال‌ها ──────────────────────────────────────────────────────────────
    if action == "panel_channels":
        channels = get_channels()
        if not channels:
            text = "هیچ کانالی ثبت نشده."
        else:
            text = "📢 *کانال‌های اجباری:*\n\n"
            for ch in channels:
                ident = ch["username"]
                label = f"-{ident}" if ident.lstrip("-").isdigit() else f"@{ident}"
                text += f"• {label} — {ch['title']}\n"
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=PANEL_BACK_KB)
        return

    # ── ادمین‌ها (فقط مالک) ───────────────────────────────────────────────────
    if action == "panel_admins":
        if not is_owner(uid):
            await query.edit_message_text("⛔ این بخش فقط برای مالک است.", reply_markup=PANEL_BACK_KB)
            return
        admins = get_admins()
        text = "👮 *ادمین‌ها:*\n\n" + (
            "".join(f"• `{a['user_id']}` (توسط {a['added_by']})\n" for a in admins)
            if admins else "هیچ ادمینی ثبت نشده."
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=PANEL_BACK_KB)
        return

    if action in ("panel_addadmin", "panel_removeadmin") and not is_owner(uid):
        await query.edit_message_text("⛔ این بخش فقط برای مالک است.", reply_markup=PANEL_BACK_KB)
        return

    # ── لیست محتواها ─────────────────────────────────────────────────────────
    if action.startswith("panel_videos_"):
        page = int(action.rsplit("_", 1)[-1]) if action.rsplit("_", 1)[-1].isdigit() else 0
        await show_videos_page(query, context, page)
        return

    # ── لیست باندل‌ها ─────────────────────────────────────────────────────────
    if action.startswith("panel_bundles_"):
        page = int(action.rsplit("_", 1)[-1]) if action.rsplit("_", 1)[-1].isdigit() else 0
        await show_bundles_page(query, context, page)
        return

    # ── حذف باندل راهنما ─────────────────────────────────────────────────────
    if action == "panel_delbundle":
        await query.edit_message_text(
            "🗑 *حذف باندل*\n\n`/delbundle <bundle_id>`",
            parse_mode="Markdown",
            reply_markup=PANEL_BACK_KB,
        )
        return

    # ── تنظیمات ضد اسپم ──────────────────────────────────────────────────────
    if action == "panel_spam_settings":
        await query.edit_message_text(
            spam_settings_text(),
            parse_mode="MarkdownV2",
            reply_markup=spam_settings_keyboard(),
        )
        return

    # ── متون راهنما ──────────────────────────────────────────────────────────
    if action in PANEL_HELP_TEXTS:
        await query.edit_message_text(
            PANEL_HELP_TEXTS[action],
            parse_mode="Markdown",
            reply_markup=PANEL_BACK_KB,
        )
        return


# ═══════════════════════════════════════════════════════════════
#  Spam Settings Callback
# ═══════════════════════════════════════════════════════════════

async def spam_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    uid    = query.from_user.id
    action = query.data

    if not is_owner(uid) and not is_admin(uid):
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    await query.answer()

    if action == "spam_noop":
        return

    parts = action.split("_")          # ['spam', 'hits', 'inc']
    if len(parts) != 3:
        return

    _, field, direction = parts
    if field not in SPAM_STEPS:
        return

    key, step, min_val, max_val = SPAM_STEPS[field]
    cfg     = get_spam_settings()
    current = cfg.get(key, step)
    new_val = min(current + step, max_val) if direction == "inc" else max(current - step, min_val)
    set_setting(key, str(new_val))

    await query.edit_message_text(
        spam_settings_text(),
        parse_mode="MarkdownV2",
        reply_markup=spam_settings_keyboard(),
    )


# ═══════════════════════════════════════════════════════════════
#  /start
# ═══════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username)

    if not context.args:
        await update.message.reply_text(
            f"سلام {user.first_name}! 👋\n\nبرای دریافت محتوا، لینک مربوطه را باز کنید."
        )
        return

    content_id  = context.args[0]
    spam_result = check_and_record_spam(user.id)

    if spam_result["blocked"]:
        mins = spam_result["seconds_left"] // 60
        secs = spam_result["seconds_left"] % 60
        await update.message.reply_text(
            f"🚫 شما موقتاً محدود شده‌اید.\n⏳ {mins} دقیقه و {secs} ثانیه دیگر تلاش کنید."
        )
        return

    if spam_result.get("just_blocked"):
        mins = spam_result["seconds_left"] // 60
        await update.message.reply_text(
            f"⚠️ تعداد درخواست‌های شما بیش از حد مجاز بود.\n"
            f"🚫 دسترسی شما برای {mins} دقیقه محدود شد."
        )
        return

    if content_id.startswith("b_"):
        await send_bundle_to_user(update, context, content_id[2:])
    else:
        await send_content_to_user(update, context, content_id)


# ═══════════════════════════════════════════════════════════════
#  Send Content / Bundle
# ═══════════════════════════════════════════════════════════════

async def send_content_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE, content_id: str):
    uid = update.effective_user.id

    not_joined = await check_membership(uid, context)
    if not_joined:
        await update.message.reply_text(
            "⚠️ برای دریافت محتوا، ابتدا در کانال‌های زیر عضو شوید:",
            reply_markup=membership_keyboard(not_joined, content_id),
        )
        return

    video = get_video(content_id)
    if not video:
        await update.message.reply_text("❌ محتوا پیدا نشد یا حذف شده است.")
        return

    increment_view(content_id, uid)
    stats      = get_video_stats(content_id)
    view_count = stats["view_count"] if stats else 0
    caption    = (video.get("caption") or "") + f"\n\n👁 {view_count} بازدید"

    sent   = await _send_media(context, update.effective_chat.id, video, caption)
    notice = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="⏳ این محتوا بعد از ۱ دقیقه حذف می‌شود.",
        reply_to_message_id=sent.message_id,
    )
    schedule_delete(context, sent.chat_id,   sent.message_id,   60)
    schedule_delete(context, notice.chat_id, notice.message_id, 60)


async def send_bundle_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE, bundle_id: str):
    uid      = update.effective_user.id
    check_id = f"b_{bundle_id}"

    not_joined = await check_membership(uid, context)
    if not_joined:
        await update.message.reply_text(
            "⚠️ برای دریافت محتوا، ابتدا در کانال‌های زیر عضو شوید:",
            reply_markup=membership_keyboard(not_joined, check_id),
        )
        return

    bundle = get_bundle(bundle_id)
    if not bundle:
        await update.message.reply_text("❌ باندل پیدا نشد یا حذف شده است.")
        return

    videos = get_bundle_videos(bundle_id)
    if not videos:
        await update.message.reply_text("❌ این باندل محتوایی ندارد.")
        return

    header = await update.message.reply_text(
        f"📦 *{bundle['title']}*\n🎬 {len(videos)} محتوا در حال ارسال...",
        parse_mode="Markdown",
    )
    sent_messages = [header]

    for i, video in enumerate(videos, 1):
        increment_view(video["video_id"], uid)
        stats      = get_video_stats(video["video_id"])
        view_count = stats["view_count"] if stats else 0
        caption    = (video.get("caption") or "") + f"\n\n[{i}/{len(videos)}] 👁 {view_count} بازدید"
        sent       = await _send_media(context, update.effective_chat.id, video, caption)
        sent_messages.append(sent)
        await asyncio.sleep(0.3)

    notice = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"⏳ {len(videos)} محتوای ارسال‌شده بعد از ۱ دقیقه حذف می‌شوند.",
    )
    sent_messages.append(notice)

    for msg in sent_messages:
        schedule_delete(context, msg.chat_id, msg.message_id, 60)


# ═══════════════════════════════════════════════════════════════
#  Check Join Callback
# ═══════════════════════════════════════════════════════════════

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query      = update.callback_query
    await query.answer()
    uid        = query.from_user.id
    content_id = query.data.split("_", 1)[1]
    chat_id    = query.message.chat_id

    not_joined = await check_membership(uid, context)
    if not_joined:
        await query.edit_message_text(
            "⚠️ هنوز در همه کانال‌ها عضو نشده‌اید:",
            reply_markup=membership_keyboard(not_joined, content_id),
        )
        return

    await query.delete_message()

    if content_id.startswith("b_"):
        bundle_id = content_id[2:]
        bundle    = get_bundle(bundle_id)
        if not bundle:
            await context.bot.send_message(chat_id=chat_id, text="❌ باندل پیدا نشد.")
            return
        videos = get_bundle_videos(bundle_id)
        if not videos:
            await context.bot.send_message(chat_id=chat_id, text="❌ این باندل محتوایی ندارد.")
            return

        header = await context.bot.send_message(
            chat_id=chat_id,
            text=f"📦 *{bundle['title']}*\n🎬 {len(videos)} محتوا در حال ارسال...",
            parse_mode="Markdown",
        )
        sent_messages = [header]
        for i, video in enumerate(videos, 1):
            increment_view(video["video_id"], uid)
            stats      = get_video_stats(video["video_id"])
            view_count = stats["view_count"] if stats else 0
            caption    = (video.get("caption") or "") + f"\n\n[{i}/{len(videos)}] 👁 {view_count} بازدید"
            sent       = await _send_media(context, chat_id, video, caption)
            sent_messages.append(sent)
            await asyncio.sleep(0.3)

        notice = await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏳ {len(videos)} محتوای ارسال‌شده بعد از ۱ دقیقه حذف می‌شوند.",
        )
        sent_messages.append(notice)
        for msg in sent_messages:
            schedule_delete(context, msg.chat_id, msg.message_id, 60)

    else:
        video = get_video(content_id)
        if not video:
            await context.bot.send_message(chat_id=chat_id, text="❌ محتوا پیدا نشد.")
            return
        increment_view(content_id, uid)
        stats      = get_video_stats(content_id)
        view_count = stats["view_count"] if stats else 0
        caption    = (video.get("caption") or "") + f"\n\n👁 {view_count} بازدید"
        sent       = await _send_media(context, chat_id, video, caption)
        notice     = await context.bot.send_message(
            chat_id=chat_id,
            text="⏳ این محتوا بعد از ۱ دقیقه حذف می‌شود.",
            reply_to_message_id=sent.message_id,
        )
        schedule_delete(context, sent.chat_id,   sent.message_id,   60)
        schedule_delete(context, notice.chat_id, notice.message_id, 60)


# ═══════════════════════════════════════════════════════════════
#  Upload
# ═══════════════════════════════════════════════════════════════

@require_admin
async def upload_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    uid     = message.from_user.id

    if not message.video:
        await message.reply_text("❌ لطفاً یک ویدیو ارسال کنید.")
        return

    content_id = uuid.uuid4().hex[:10]
    add_video(content_id, message.video.file_id, message.caption or "", uid, "video")

    if uid in bundle_sessions:
        session = bundle_sessions[uid]
        add_to_bundle(session["bundle_id"], content_id, len(session["videos"]))
        session["videos"].append(content_id)
        await message.reply_text(
            f"✅ ویدیو {len(session['videos'])} به باندل اضافه شد\\.\n"
            f"📦 باندل: *{escape_md(session['title'])}*\n\n"
            "ادامه بده یا /donebundle برای پایان\\.",
            parse_mode="MarkdownV2",
        )
        return

    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={content_id}"
    await message.reply_text(
        f"✅ ویدیو ذخیره شد\\!\n\n🆔 شناسه: `{content_id}`\n🔗 لینک: {escape_md(link)}",
        parse_mode="MarkdownV2",
    )


@require_admin
async def upload_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    uid     = message.from_user.id

    if not message.photo:
        await message.reply_text("❌ لطفاً یک عکس ارسال کنید.")
        return

    content_id = uuid.uuid4().hex[:10]
    add_video(content_id, message.photo[-1].file_id, message.caption or "", uid, "photo")

    if uid in bundle_sessions:
        session = bundle_sessions[uid]
        add_to_bundle(session["bundle_id"], content_id, len(session["videos"]))
        session["videos"].append(content_id)
        await message.reply_text(
            f"✅ عکس {len(session['videos'])} به باندل اضافه شد\\.\n"
            f"📦 باندل: *{escape_md(session['title'])}*\n\n"
            "ادامه بده یا /donebundle برای پایان\\.",
            parse_mode="MarkdownV2",
        )
        return

    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={content_id}"
    await message.reply_text(
        f"✅ عکس ذخیره شد\\!\n\n🆔 شناسه: `{content_id}`\n🔗 لینک: {escape_md(link)}",
        parse_mode="MarkdownV2",
    )


# ═══════════════════════════════════════════════════════════════
#  Bundle Commands
# ═══════════════════════════════════════════════════════════════

@require_admin
async def new_bundle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "استفاده: `/newbundle عنوان باندل`\nمثال: `/newbundle پکیج آموزشی ۱`",
            parse_mode="Markdown",
        )
        return

    title     = " ".join(context.args)
    bundle_id = uuid.uuid4().hex[:8]
    create_bundle(bundle_id, title, uid)
    bundle_sessions[uid] = {"bundle_id": bundle_id, "title": title, "videos": []}

    await update.message.reply_text(
        f"📦 باندل *{title}* ساخته شد\\!\n\n"
        f"🆔 شناسه: `{bundle_id}`\n\n"
        "حالا ویدیوها و عکس‌ها را یکی‌یکی ارسال کن\\.\n"
        "وقتی تمام شد /donebundle را بفرست\\.",
        parse_mode="MarkdownV2",
    )


@require_admin
async def done_bundle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in bundle_sessions:
        await update.message.reply_text("❌ هیچ باندل فعالی وجود ندارد. ابتدا /newbundle را اجرا کن.")
        return

    session = bundle_sessions.pop(uid)
    count   = len(session["videos"])

    if count == 0:
        delete_bundle(session["bundle_id"])
        await update.message.reply_text("❌ باندل بدون محتوا حذف شد.")
        return

    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=b_{session['bundle_id']}"
    await update.message.reply_text(
        f"✅ باندل *{session['title']}* با {count} محتوا ذخیره شد\\!\n\n"
        f"🆔 شناسه: `{session['bundle_id']}`\n"
        f"🔗 لینک: {escape_md(link)}",
        parse_mode="MarkdownV2",
    )


@require_admin
async def cancel_bundle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in bundle_sessions:
        session = bundle_sessions.pop(uid)
        delete_bundle(session["bundle_id"])
        await update.message.reply_text("❌ ساخت باندل لغو شد.")
    else:
        await update.message.reply_text("هیچ باندل فعالی وجود ندارد.")


@require_admin
async def del_bundle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استفاده: /delbundle <bundle_id>")
        return
    bundle_id = context.args[0]
    if not get_bundle(bundle_id):
        await update.message.reply_text("❌ باندل پیدا نشد.")
        return
    delete_bundle(bundle_id)
    await update.message.reply_text(f"✅ باندل `{bundle_id}` حذف شد.", parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════
#  Stats / Delete
# ═══════════════════════════════════════════════════════════════

@require_admin
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            f"📊 *آمار ربات*\n\n👤 کاربران: {get_users_count()}",
            parse_mode="Markdown",
        )
        return

    st = get_video_stats(context.args[0])
    if not st:
        await update.message.reply_text("❌ محتوا پیدا نشد.")
        return
    await update.message.reply_text(
        f"📊 *آمار محتوا* `{context.args[0]}`\n\n"
        f"👁 بازدید کل: {st['view_count']}\n"
        f"👥 بینندگان یکتا: {st['unique_viewers']}\n"
        f"📅 آپلود: {st['uploaded_at']}",
        parse_mode="Markdown",
    )


@require_admin
async def delete_video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استفاده: /delvideo <id>")
        return
    delete_video(context.args[0])
    await update.message.reply_text(f"✅ محتوا `{context.args[0]}` حذف شد.", parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════
#  Channels
# ═══════════════════════════════════════════════════════════════

@require_admin
async def add_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text(
            "استفاده:\n"
            "عمومی: `/addchannel @username عنوان لینک`\n"
            "خصوصی: `/addchannel -1001234567890 عنوان لینک_دعوت`",
            parse_mode="Markdown",
        )
        return
    username = context.args[0].lstrip("@")
    add_channel(username, context.args[1], context.args[2])
    await update.message.reply_text(f"✅ کانال اضافه شد: {context.args[1]}")


@require_admin
async def remove_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استفاده: /removechannel @username")
        return
    remove_channel(context.args[0].lstrip("@"))
    await update.message.reply_text("✅ کانال حذف شد.")


@require_admin
async def list_channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = get_channels()
    if not channels:
        await update.message.reply_text("هیچ کانالی ثبت نشده.")
        return
    text = "📢 *کانال‌های اجباری:*\n\n"
    for ch in channels:
        ident = ch["username"]
        label = f"-{ident}" if ident.lstrip("-").isdigit() else f"@{ident}"
        text += f"• {label} — {ch['title']}\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════
#  Admins (Owner only)
# ═══════════════════════════════════════════════════════════════

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ فقط مالک می‌تواند ادمین اضافه کند.")
        return
    if not context.args:
        await update.message.reply_text("استفاده: /addadmin <user_id>")
        return
    try:
        new_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ آیدی نامعتبر.")
        return
    add_admin(new_id, update.effective_user.id)
    await update.message.reply_text(f"✅ کاربر {new_id} به عنوان ادمین اضافه شد.")


async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("⛔ فقط مالک می‌تواند ادمین حذف کند.")
        return
    if not context.args:
        await update.message.reply_text("استفاده: /removeadmin <user_id>")
        return
    try:
        admin_id = int(context.args[0])
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
    text = "👮 *ادمین‌ها:*\n\n" + (
        "".join(f"• `{a['user_id']}` (توسط {a['added_by']})\n" for a in admins)
        if admins else "هیچ ادمینی ثبت نشده."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════
#  Unblock / Help
# ═══════════════════════════════════════════════════════════════

@require_admin
async def unblock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("استفاده: /unblock <user_id>")
        return
    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ آیدی نامعتبر.")
        return
    unblock_user(target_id)
    await update.message.reply_text(f"✅ کاربر {target_id} آنبلاک شد.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_owner(uid) or is_admin(uid):
        text = (
            "🛠 *دستورات ادمین:*\n\n"
            "/panel — پنل مدیریت\n"
            "/newbundle عنوان — شروع ساخت باندل\n"
            "/donebundle — پایان ساخت باندل\n"
            "/cancelbundle — لغو ساخت باندل\n"
            "/delbundle id — حذف باندل\n"
            "/unblock user\\_id — آنبلاک کاربر\n"
            "/stats \\[id\\] — آمار\n"
            "/delvideo id — حذف محتوا\n"
        )
    else:
        text = "برای دریافت ویدیو یا عکس، لینک مستقیم را باز کنید. 🎬"
    await update.message.reply_text(text, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",         start))
    app.add_handler(CommandHandler("help",          help_command))
    app.add_handler(CommandHandler("panel",         panel_command))
    app.add_handler(CommandHandler("stats",         stats_command))
    app.add_handler(CommandHandler("delvideo",      delete_video_command))
    app.add_handler(CommandHandler("addchannel",    add_channel_command))
    app.add_handler(CommandHandler("removechannel", remove_channel_command))
    app.add_handler(CommandHandler("channels",      list_channels_command))
    app.add_handler(CommandHandler("addadmin",      add_admin_command))
    app.add_handler(CommandHandler("removeadmin",   remove_admin_command))
    app.add_handler(CommandHandler("admins",        list_admins_command))
    app.add_handler(CommandHandler("unblock",       unblock_command))
    app.add_handler(CommandHandler("newbundle",     new_bundle_command))
    app.add_handler(CommandHandler("donebundle",    done_bundle_command))
    app.add_handler(CommandHandler("cancelbundle",  cancel_bundle_command))
    app.add_handler(CommandHandler("delbundle",     del_bundle_command))

    app.add_handler(MessageHandler(filters.VIDEO & filters.ChatType.PRIVATE, upload_video))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, upload_photo))

    app.add_handler(CallbackQueryHandler(check_join_callback, pattern=r"^check_"))
    app.add_handler(CallbackQueryHandler(spam_callback,       pattern=r"^spam_"))
    app.add_handler(CallbackQueryHandler(panel_callback,      pattern=r"^panel_"))

    logger.info("Bot started...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

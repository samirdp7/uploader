# ربات تلگرام آپلودر ویدیو

## امکانات
- آپلود ویدیو توسط ادمین و دریافت لینک مستقیم
- عضویت اجباری در کانال‌ها قبل از دریافت ویدیو
- آمار بازدید (کل و یکتا)
- مدیریت ادمین‌ها توسط مالک
- مدیریت کانال‌های اجباری

---

## راه‌اندازی روی Railway

### ۱. ساخت ربات
از [@BotFather](https://t.me/BotFather) یک ربات بسازید و توکن را نگه دارید.

### ۲. گرفتن آیدی عددی خودتان
به [@userinfobot](https://t.me/userinfobot) پیام بدید تا آیدی عددی‌تان را بگیرید.

### ۳. آپلود کد روی GitHub
```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/USERNAME/REPO.git
git push -u origin main
```

### ۴. ساخت پروژه در Railway
1. وارد [railway.app](https://railway.app) شوید
2. **New Project → Deploy from GitHub repo** را انتخاب کنید
3. ریپوی خود را انتخاب کنید

### ۵. تنظیم متغیرهای محیطی
در Railway به تب **Variables** بروید و اضافه کنید:

| کلید | مقدار |
|------|-------|
| `BOT_TOKEN` | توکن ربات از BotFather |
| `OWNER_ID` | آیدی عددی خودتان |

### ۶. Deploy
Railway به صورت خودکار build و deploy می‌کند.

---

## دستورات

### برای مالک (Owner)
| دستور | توضیح |
|-------|-------|
| `/addadmin 123456789` | اضافه کردن ادمین |
| `/removeadmin 123456789` | حذف ادمین |
| `/admins` | لیست ادمین‌ها |

### برای ادمین‌ها
| دستور | توضیح |
|-------|-------|
| ارسال ویدیو | ربات لینک مستقیم می‌دهد |
| `/addchannel @username عنوان لینک` | اضافه کانال اجباری |
| `/removechannel @username` | حذف کانال |
| `/channels` | لیست کانال‌ها |
| `/stats` | آمار کلی کاربران |
| `/stats <video_id>` | آمار یک ویدیو |
| `/delvideo <video_id>` | حذف ویدیو |
| `/help` | راهنما |

---

## نکته مهم درباره Railway
Railway فضای ذخیره‌سازی دائمی (persistent disk) ندارد مگر اینکه Volume اضافه کنید.
برای اینکه دیتابیس SQLite از بین نرود، در Railway یک **Volume** بسازید و آن را به مسیر `/app` یا مسیر دلخواه mount کنید، سپس در `database.py` مسیر `bot.db` را به آن مسیر تغییر دهید.

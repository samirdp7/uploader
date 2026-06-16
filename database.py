import sqlite3
from datetime import datetime, timedelta

DB_PATH = "/data/bot.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # بهتر برای همزمانی
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                joined_at   TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS admins (
                user_id     INTEGER PRIMARY KEY,
                added_by    INTEGER,
                added_at    TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS channels (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT UNIQUE,
                title       TEXT,
                link        TEXT
            );
            CREATE TABLE IF NOT EXISTS videos (
                video_id     TEXT PRIMARY KEY,
                file_id      TEXT NOT NULL,
                caption      TEXT DEFAULT '',
                uploaded_by  INTEGER,
                uploaded_at  TEXT DEFAULT (datetime('now')),
                view_count   INTEGER DEFAULT 0,
                content_type TEXT DEFAULT 'video'
            );
            CREATE TABLE IF NOT EXISTS video_views (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id    TEXT NOT NULL,
                user_id     INTEGER NOT NULL,
                viewed_at   TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (video_id) REFERENCES videos(video_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS spam_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                hit_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS spam_blocks (
                user_id       INTEGER PRIMARY KEY,
                blocked_until TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bundles (
                bundle_id   TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                created_by  INTEGER,
                created_at  TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS bundle_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                bundle_id   TEXT NOT NULL,
                video_id    TEXT NOT NULL,
                position    INTEGER DEFAULT 0,
                FOREIGN KEY (bundle_id) REFERENCES bundles(bundle_id) ON DELETE CASCADE,
                FOREIGN KEY (video_id)  REFERENCES videos(video_id)   ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_spam_log_user_time
                ON spam_log(user_id, hit_at);
            CREATE INDEX IF NOT EXISTS idx_video_views_video
                ON video_views(video_id);
            CREATE INDEX IF NOT EXISTS idx_bundle_items_bundle
                ON bundle_items(bundle_id);
        """)

        # مهاجرت: اضافه کردن content_type اگه نبود
        try:
            conn.execute("ALTER TABLE videos ADD COLUMN content_type TEXT DEFAULT 'video'")
            conn.commit()
        except sqlite3.OperationalError:
            pass

        # مقادیر پیش‌فرض تنظیمات ضد اسپم
        defaults = [
            ("spam_max_hits",       "4"),
            ("spam_window_seconds", "60"),
            ("spam_block_seconds",  "120"),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", defaults
        )
        conn.commit()
    finally:
        conn.close()


# ─── Users ───────────────────────────────────────────────────────────────────

def add_user(user_id: int, username: str | None = None):
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username)
        )
        conn.commit()
    finally:
        conn.close()


def get_users_count() -> int:
    conn = get_db()
    try:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()


# ─── Admins ──────────────────────────────────────────────────────────────────

def add_admin(user_id: int, added_by: int):
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
            (user_id, added_by)
        )
        conn.commit()
    finally:
        conn.close()


def remove_admin(user_id: int):
    conn = get_db()
    try:
        conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def get_admins() -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM admins").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def is_admin(user_id: int) -> bool:
    conn = get_db()
    try:
        result = conn.execute(
            "SELECT 1 FROM admins WHERE user_id = ?", (user_id,)
        ).fetchone()
        return result is not None
    finally:
        conn.close()


# ─── Channels ────────────────────────────────────────────────────────────────

def add_channel(username: str, title: str, link: str):
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO channels (username, title, link) VALUES (?, ?, ?)",
            (username, title, link)
        )
        conn.commit()
    finally:
        conn.close()


def remove_channel(username: str):
    conn = get_db()
    try:
        conn.execute("DELETE FROM channels WHERE username = ?", (username,))
        conn.commit()
    finally:
        conn.close()


def get_channels() -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM channels").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── Videos ──────────────────────────────────────────────────────────────────

def add_video(video_id: str, file_id: str, caption: str,
              uploaded_by: int, content_type: str = "video"):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO videos (video_id, file_id, caption, uploaded_by, content_type)
               VALUES (?, ?, ?, ?, ?)""",
            (video_id, file_id, caption or "", uploaded_by, content_type)
        )
        conn.commit()
    finally:
        conn.close()


def get_video(video_id: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_video(video_id: str):
    """
    حذف ویدیو + همه view ها و bundle_item های مرتبط.
    چون ON DELETE CASCADE فعاله، فقط حذف از videos کافیه.
    """
    conn = get_db()
    try:
        conn.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))
        conn.commit()
    finally:
        conn.close()


def increment_view(video_id: str, user_id: int):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE videos SET view_count = view_count + 1 WHERE video_id = ?",
            (video_id,)
        )
        conn.execute(
            "INSERT INTO video_views (video_id, user_id) VALUES (?, ?)",
            (video_id, user_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_video_stats(video_id: str) -> dict | None:
    """
    برگرداندن آمار ویدیو.
    باگ قبلی: اگه هیچ view ای نبود GROUP BY چیزی برنمی‌گردوند.
    حل: LEFT JOIN + COALESCE برای شمارش یکتا بینندگان.
    """
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT
                v.view_count,
                v.uploaded_at,
                COUNT(DISTINCT vv.user_id) AS unique_viewers
            FROM videos v
            LEFT JOIN video_views vv ON v.video_id = vv.video_id
            WHERE v.video_id = ?
            GROUP BY v.video_id
        """, (video_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_videos_paginated(page: int = 0, page_size: int = 5) -> dict:
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        offset = page * page_size
        rows = conn.execute("""
            SELECT video_id, caption, content_type, uploaded_at, uploaded_by
            FROM videos
            ORDER BY uploaded_at DESC
            LIMIT ? OFFSET ?
        """, (page_size, offset)).fetchall()
        return {
            "videos":    [dict(r) for r in rows],
            "total":     total,
            "page":      page,
            "page_size": page_size,
        }
    finally:
        conn.close()


# ─── Settings ────────────────────────────────────────────────────────────────

def get_setting(key: str) -> str | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def set_setting(key: str, value: str):
    conn = get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        conn.commit()
    finally:
        conn.close()


def get_spam_settings() -> dict:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'spam_%'"
        ).fetchall()
        return {r[0]: int(r[1]) for r in rows}
    finally:
        conn.close()


# ─── Anti-Spam ───────────────────────────────────────────────────────────────

def check_and_record_spam(user_id: int) -> dict:
    """
    بررسی اسپم و ثبت درخواست کاربر.

    خروجی:
        {"blocked": True,  "seconds_left": N}
            ← کاربر هنوز بلاک است
        {"blocked": False, "just_blocked": True, "seconds_left": N}
            ← همین الان بلاک شد
        {"blocked": False, "just_blocked": False}
            ← مجاز است

    تغییرات نسبت به نسخه قبل:
        - import ها به بالای فایل منتقل شدن
        - try/finally برای جلوگیری از connection leak
        - پاکسازی spam_log موقع expire بلاک
        - استفاده از یک transaction برای ثبت + شمارش (کمتر race condition)
    """
    cfg = get_spam_settings()
    max_hits   = cfg.get("spam_max_hits",       4)
    window_sec = cfg.get("spam_window_seconds", 60)
    block_sec  = cfg.get("spam_block_seconds",  120)

    conn = get_db()
    try:
        now = datetime.utcnow()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # ── بررسی بلاک فعلی ──────────────────────────────────────────────
        block_row = conn.execute(
            "SELECT blocked_until FROM spam_blocks WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if block_row:
            blocked_until = datetime.strptime(block_row[0], "%Y-%m-%d %H:%M:%S")
            if now < blocked_until:
                seconds_left = int((blocked_until - now).total_seconds())
                return {"blocked": True, "seconds_left": seconds_left}
            else:
                # بلاک منقضی شده، پاکسازی
                conn.execute("DELETE FROM spam_blocks WHERE user_id = ?", (user_id,))
                conn.execute("DELETE FROM spam_log    WHERE user_id = ?", (user_id,))
                conn.commit()

        # ── ثبت این درخواست ──────────────────────────────────────────────
        conn.execute(
            "INSERT INTO spam_log (user_id, hit_at) VALUES (?, ?)",
            (user_id, now_str)
        )
        conn.commit()

        # ── شمارش در پنجره زمانی ─────────────────────────────────────────
        window_start = (now - timedelta(seconds=window_sec)).strftime("%Y-%m-%d %H:%M:%S")
        count = conn.execute(
            "SELECT COUNT(*) FROM spam_log WHERE user_id = ? AND hit_at >= ?",
            (user_id, window_start)
        ).fetchone()[0]

        if count > max_hits:
            blocked_until     = now + timedelta(seconds=block_sec)
            blocked_until_str = blocked_until.strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT OR REPLACE INTO spam_blocks (user_id, blocked_until) VALUES (?, ?)",
                (user_id, blocked_until_str)
            )
            conn.commit()
            return {"blocked": False, "just_blocked": True, "seconds_left": block_sec}

        return {"blocked": False, "just_blocked": False}

    finally:
        conn.close()


def unblock_user(user_id: int):
    conn = get_db()
    try:
        conn.execute("DELETE FROM spam_blocks WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM spam_log    WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


# ─── Bundles ─────────────────────────────────────────────────────────────────

def create_bundle(bundle_id: str, title: str, created_by: int):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO bundles (bundle_id, title, created_by) VALUES (?, ?, ?)",
            (bundle_id, title, created_by)
        )
        conn.commit()
    finally:
        conn.close()


def add_to_bundle(bundle_id: str, video_id: str, position: int):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO bundle_items (bundle_id, video_id, position) VALUES (?, ?, ?)",
            (bundle_id, video_id, position)
        )
        conn.commit()
    finally:
        conn.close()


def get_bundle(bundle_id: str) -> dict | None:
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM bundles WHERE bundle_id = ?", (bundle_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_bundle_videos(bundle_id: str) -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT v.video_id, v.file_id, v.caption, v.content_type,
                   v.view_count, bi.position
            FROM bundle_items bi
            JOIN videos v ON bi.video_id = v.video_id
            WHERE bi.bundle_id = ?
            ORDER BY bi.position ASC
        """, (bundle_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_bundle(bundle_id: str):
    """
    حذف باندل + همه bundle_items مرتبط.
    چون ON DELETE CASCADE فعاله، فقط حذف از bundles کافیه.
    ویدیوهای داخل باندل حذف نمی‌شن (فقط رابطه قطع می‌شه).
    """
    conn = get_db()
    try:
        conn.execute("DELETE FROM bundles WHERE bundle_id = ?", (bundle_id,))
        conn.commit()
    finally:
        conn.close()


def get_bundles_paginated(page: int = 0, page_size: int = 5) -> dict:
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM bundles").fetchone()[0]
        offset = page * page_size
        rows = conn.execute("""
            SELECT b.bundle_id, b.title, b.created_at,
                   COUNT(bi.id) AS item_count
            FROM bundles b
            LEFT JOIN bundle_items bi ON b.bundle_id = bi.bundle_id
            GROUP BY b.bundle_id
            ORDER BY b.created_at DESC
            LIMIT ? OFFSET ?
        """, (page_size, offset)).fetchall()
        return {
            "bundles":   [dict(r) for r in rows],
            "total":     total,
            "page":      page,
            "page_size": page_size,
        }
    finally:
        conn.close()

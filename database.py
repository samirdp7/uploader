import sqlite3
from datetime import datetime

def get_db():
    conn = sqlite3.connect("bot.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
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
            caption      TEXT,
            uploaded_by  INTEGER,
            uploaded_at  TEXT DEFAULT (datetime('now')),
            view_count   INTEGER DEFAULT 0,
            content_type TEXT DEFAULT 'video'
        );
        CREATE TABLE IF NOT EXISTS video_views (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id    TEXT,
            user_id     INTEGER,
            viewed_at   TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (video_id) REFERENCES videos(video_id)
        );
    """)

    try:
        conn.execute("ALTER TABLE videos ADD COLUMN content_type TEXT DEFAULT 'video'")
        conn.commit()
    except sqlite3.OperationalError:
        pass

    conn.close()

def add_user(user_id, username=None):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()

def get_users_count():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    return count

def add_admin(user_id, added_by):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)", (user_id, added_by))
    conn.commit()
    conn.close()

def remove_admin(user_id):
    conn = get_db()
    conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_admins():
    conn = get_db()
    rows = conn.execute("SELECT * FROM admins").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def is_admin(user_id):
    conn = get_db()
    result = conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return result is not None

def add_channel(username, title, link):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO channels (username, title, link) VALUES (?, ?, ?)", (username, title, link))
    conn.commit()
    conn.close()

def remove_channel(username):
    conn = get_db()
    conn.execute("DELETE FROM channels WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def get_channels():
    conn = get_db()
    rows = conn.execute("SELECT * FROM channels").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def add_video(video_id, file_id, caption, uploaded_by, content_type="video"):
    conn = get_db()
    conn.execute(
        "INSERT INTO videos (video_id, file_id, caption, uploaded_by, content_type) VALUES (?, ?, ?, ?, ?)",
        (video_id, file_id, caption, uploaded_by, content_type)
    )
    conn.commit()
    conn.close()

def get_video(video_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

def delete_video(video_id):
    conn = get_db()
    conn.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))
    conn.execute("DELETE FROM video_views WHERE video_id = ?", (video_id,))
    conn.commit()
    conn.close()

def increment_view(video_id, user_id):
    conn = get_db()
    conn.execute("UPDATE videos SET view_count = view_count + 1 WHERE video_id = ?", (video_id,))
    conn.execute("INSERT INTO video_views (video_id, user_id) VALUES (?, ?)", (video_id, user_id))
    conn.commit()
    conn.close()

def get_video_stats(video_id):
    conn = get_db()
    row = conn.execute("""
        SELECT v.view_count, v.uploaded_at, COUNT(DISTINCT vv.user_id) as unique_viewers
        FROM videos v
        LEFT JOIN video_views vv ON v.video_id = vv.video_id
        WHERE v.video_id = ?
        GROUP BY v.video_id
    """, (video_id,)).fetchone()
    conn.close()
    return dict(row) if row else None

# ─── تابع جدید: لیست ویدیوها با صفحه‌بندی ────────────────────────────────────

def get_videos_paginated(page: int = 0, page_size: int = 5) -> dict:
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    offset = page * page_size
    rows = conn.execute(
        """
        SELECT video_id, caption, content_type, uploaded_at, uploaded_by
        FROM videos
        ORDER BY uploaded_at DESC
        LIMIT ? OFFSET ?
        """,
        (page_size, offset)
    ).fetchall()
    conn.close()
    return {
        "videos": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size
    }

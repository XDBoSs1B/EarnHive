"""
EarnHive Bot - Database Layer
SQLite ব্যবহার করা হয়েছে, আলাদা সার্ভার লাগবে না।
"""
import sqlite3
from datetime import datetime
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            language TEXT DEFAULT 'en',
            balance REAL DEFAULT 0,
            total_earned REAL DEFAULT 0,
            referred_by INTEGER,
            joined_channel INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            method TEXT,             -- 'bkash' or 'usdt'
            amount_usd REAL,
            account_info TEXT,       -- bkash number or USDT wallet address
            status TEXT DEFAULT 'pending',  -- pending / approved / rejected
            requested_at TEXT,
            processed_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS task_completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            task_type TEXT,   -- 'channel_join', 'website_visit', 'ad_view'
            reward REAL,
            completed_at TEXT
        )
    """)

    conn.commit()
    conn.close()


# ---------- USER FUNCTIONS ----------

def get_user(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row


def create_user(user_id, username, referred_by=None):
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username, referred_by, created_at) VALUES (?, ?, ?, ?)",
        (user_id, username, referred_by, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def set_language(user_id, lang):
    conn = get_conn()
    conn.execute("UPDATE users SET language=? WHERE user_id=?", (lang, user_id))
    conn.commit()
    conn.close()


def add_balance(user_id, amount):
    conn = get_conn()
    conn.execute(
        "UPDATE users SET balance = balance + ?, total_earned = total_earned + ? WHERE user_id=?",
        (amount, amount, user_id)
    )
    conn.commit()
    conn.close()


def deduct_balance(user_id, amount):
    conn = get_conn()
    conn.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


def get_referral_counts(user_id):
    """Level 1, 2 রেফার সংখ্যা বের করে"""
    conn = get_conn()
    level1 = conn.execute("SELECT user_id FROM users WHERE referred_by=?", (user_id,)).fetchall()
    level1_ids = [r["user_id"] for r in level1]

    level2_ids = []
    for uid in level1_ids:
        rows = conn.execute("SELECT user_id FROM users WHERE referred_by=?", (uid,)).fetchall()
        level2_ids.extend([r["user_id"] for r in rows])

    conn.close()
    return len(level1_ids), len(level2_ids)


def get_referral_chain(user_id):
    """একজন ইউজারের উপরের ২ লেভেল রেফারার বের করে (কমিশন দেওয়ার জন্য)"""
    conn = get_conn()
    chain = []
    current = user_id
    for _ in range(2):
        row = conn.execute("SELECT referred_by FROM users WHERE user_id=?", (current,)).fetchone()
        if row and row["referred_by"]:
            chain.append(row["referred_by"])
            current = row["referred_by"]
        else:
            break
    conn.close()
    return chain  # [level1_referrer, level2_referrer]


# ---------- TASK FUNCTIONS ----------

def log_task_completion(user_id, task_type, reward):
    conn = get_conn()
    conn.execute(
        "INSERT INTO task_completions (user_id, task_type, reward, completed_at) VALUES (?, ?, ?, ?)",
        (user_id, task_type, reward, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def has_completed_task_today(user_id, task_type):
    """একই টাস্ক বারবার করে আয় করা ঠেকাতে (যেমন দিনে একবার চ্যানেল-জয়েন রিওয়ার্ড)"""
    conn = get_conn()
    today = datetime.utcnow().date().isoformat()
    row = conn.execute(
        "SELECT * FROM task_completions WHERE user_id=? AND task_type=? AND completed_at LIKE ?",
        (user_id, task_type, f"{today}%")
    ).fetchone()
    conn.close()
    return row is not None


def get_task_completion_count_today(user_id, task_type):
    """আজকে এই টাস্কটা কতবার সম্পন্ন হয়েছে (কুলডাউন-বেসড টাস্কের জন্য, যেমন ওয়েবসাইট ভিজিট)"""
    conn = get_conn()
    today = datetime.utcnow().date().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM task_completions WHERE user_id=? AND task_type=? AND completed_at LIKE ?",
        (user_id, task_type, f"{today}%")
    ).fetchone()
    conn.close()
    return row["cnt"] if row else 0


def get_last_task_completion_time(user_id, task_type):
    """এই টাস্কটা সর্বশেষ কখন সম্পন্ন হয়েছে (কুলডাউন চেক করার জন্য)"""
    conn = get_conn()
    row = conn.execute(
        "SELECT completed_at FROM task_completions WHERE user_id=? AND task_type=? ORDER BY completed_at DESC LIMIT 1",
        (user_id, task_type)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return datetime.fromisoformat(row["completed_at"])


# ---------- WITHDRAWAL FUNCTIONS ----------

def create_withdrawal(user_id, method, amount_usd, account_info):
    conn = get_conn()
    conn.execute(
        "INSERT INTO withdrawals (user_id, method, amount_usd, account_info, requested_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, method, amount_usd, account_info, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def get_pending_withdrawals():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM withdrawals WHERE status='pending' ORDER BY requested_at ASC").fetchall()
    conn.close()
    return rows


def update_withdrawal_status(withdrawal_id, status):
    conn = get_conn()
    conn.execute(
        "UPDATE withdrawals SET status=?, processed_at=? WHERE id=?",
        (status, datetime.utcnow().isoformat(), withdrawal_id)
    )
    conn.commit()
    conn.close()


def get_withdrawal(withdrawal_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)).fetchone()
    conn.close()
    return row

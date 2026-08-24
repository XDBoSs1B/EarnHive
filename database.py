"""
EarnHive Bot - Database Layer
এখন PostgreSQL (Supabase) ব্যবহার করা হয়েছে, SQLite না।
কারণ: Render-এর ফ্রি সার্ভিসের ফাইল সিস্টেম "ephemeral" (অস্থায়ী) -
প্রতিবার রিডিপ্লয়/রিস্টার্টে লোকাল ফাইল (SQLite) মুছে যায়, ইউজারদের ব্যালেন্স হারিয়ে যেত।
Supabase একটা আলাদা, স্থায়ী ডাটাবেস সার্ভিস - Render রিডিপ্লয় হলেও এখানকার ডাটা অক্ষত থাকে।
"""
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from config import DATABASE_URL


def get_conn():
    # sslmode='require' জরুরি - Supabase SSL ছাড়া কানেকশন গ্রহণ করে না
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            language TEXT DEFAULT 'en',
            balance DOUBLE PRECISION DEFAULT 0,
            total_earned DOUBLE PRECISION DEFAULT 0,
            referred_by BIGINT,
            joined_channel INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            method TEXT,
            amount_usd DOUBLE PRECISION,
            account_info TEXT,
            status TEXT DEFAULT 'pending',
            requested_at TEXT,
            processed_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS task_completions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            task_type TEXT,
            reward DOUBLE PRECISION,
            completed_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS referral_earnings (
            id SERIAL PRIMARY KEY,
            referrer_id BIGINT,
            source_user_id BIGINT,
            level INTEGER,
            amount DOUBLE PRECISION,
            created_at TEXT
        )
    """)

    # CPAlead থেকে আসা conversion (postback) গুলোর রেকর্ড - lead_id UNIQUE রাখা হয়েছে
    # যাতে CPAlead একই postback রিট্রাই/ডুপ্লিকেট পাঠালেও দ্বিতীয়বার টাকা যোগ না হয়।
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cpalead_conversions (
            id SERIAL PRIMARY KEY,
            lead_id TEXT UNIQUE NOT NULL,
            user_id BIGINT,
            offer_id TEXT,
            campaign_name TEXT,
            payout DOUBLE PRECISION,
            created_at TEXT
        )
    """)

    # ইউজার কোন CPAlead offer-এ "Start" চাপলো তার রেকর্ড - postback (verify) আসার আগ পর্যন্ত
    # "Pending" হিসেবে দেখানোর জন্য।
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cpalead_started (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            offer_id TEXT,
            title TEXT,
            amount DOUBLE PRECISION,
            started_at TEXT
        )
    """)

    # নিজস্ব "Link Locker" টাস্কের জন্য - Telegram-এর বাইরে (আলাদা ব্রাউজার পেজে) খোলা
    # ২-ধাপের বিজ্ঞাপন পেজে ইউজারকে নিরাপদে শনাক্ত করার জন্য একবার-ব্যবহারযোগ্য টোকেন।
    # কোনো Telegram initData এই পেজগুলোতে পাওয়া যায় না, তাই টোকেনই একমাত্র প্রমাণ যে
    # এই সম্পূর্ণকরণটা আসলে কোন ইউজারের জন্য গণ্য হবে।
    cur.execute("""
        CREATE TABLE IF NOT EXISTS link_tokens (
            id SERIAL PRIMARY KEY,
            token TEXT UNIQUE NOT NULL,
            user_id BIGINT,
            task_id TEXT,
            created_at TEXT,
            used INTEGER DEFAULT 0
        )
    """)

    # ---------- URL SHORTENER ("৫ নম্বর ঘর") ----------
    # ইউজার নিজে যে লিংক শর্ট করে, সেটার তথ্য এখানে থাকে - কে বানিয়েছে, কোথায় যাবে,
    # এখন পর্যন্ত মোট কতবার দেখা হয়েছে আর মোট কত রিওয়ার্ড এসেছে (এই owner-এর জন্য)।
    cur.execute("""
        CREATE TABLE IF NOT EXISTS short_links (
            id SERIAL PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            user_id BIGINT,
            destination_url TEXT,
            views INTEGER DEFAULT 0,
            total_reward DOUBLE PRECISION DEFAULT 0,
            created_at TEXT
        )
    """)

    # একই IP থেকে একই শর্ট লিংকে ২৪ ঘণ্টার মধ্যে দ্বিতীয়বার view দিলে যাতে আবার
    # reward না দেওয়া হয় (fraud protection) - সেই লগ এখানে রাখা হয়।
    cur.execute("""
        CREATE TABLE IF NOT EXISTS short_link_views (
            id SERIAL PRIMARY KEY,
            code TEXT,
            viewer_ip TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    cur.close()
    conn.close()


# ---------- USER FUNCTIONS ----------

def get_user(user_id):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def create_user(user_id, username, referred_by=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (user_id, username, referred_by, created_at) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (user_id) DO NOTHING",
        (user_id, username, referred_by, datetime.utcnow().isoformat())
    )
    conn.commit()
    cur.close()
    conn.close()


def set_language(user_id, lang):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET language=%s WHERE user_id=%s", (lang, user_id))
    conn.commit()
    cur.close()
    conn.close()


def set_joined_channel(user_id, joined=True):
    """ইউজার রিকোয়ার্ড চ্যানেলে জয়েন করেছে কিনা - ভেরিফাই হওয়ার পর ক্যাশ করে রাখে,
    যাতে বারবার Telegram API কল করতে না হয়।"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET joined_channel=%s WHERE user_id=%s", (1 if joined else 0, user_id))
    conn.commit()
    cur.close()
    conn.close()


def add_balance(user_id, amount):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET balance = balance + %s, total_earned = total_earned + %s WHERE user_id=%s",
        (amount, amount, user_id)
    )
    conn.commit()
    cur.close()
    conn.close()


def deduct_balance(user_id, amount):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance - %s WHERE user_id=%s", (amount, user_id))
    conn.commit()
    cur.close()
    conn.close()


def get_referral_counts(user_id):
    """Level 1, 2 রেফার সংখ্যা বের করে"""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT user_id FROM users WHERE referred_by=%s", (user_id,))
    level1 = cur.fetchall()
    level1_ids = [r["user_id"] for r in level1]

    level2_ids = []
    for uid in level1_ids:
        cur.execute("SELECT user_id FROM users WHERE referred_by=%s", (uid,))
        rows = cur.fetchall()
        level2_ids.extend([r["user_id"] for r in rows])

    cur.close()
    conn.close()
    return len(level1_ids), len(level2_ids)


def get_referral_chain(user_id):
    """একজন ইউজারের উপরের ২ লেভেল রেফারার বের করে (কমিশন দেওয়ার জন্য)"""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    chain = []
    current = user_id
    for _ in range(2):
        cur.execute("SELECT referred_by FROM users WHERE user_id=%s", (current,))
        row = cur.fetchone()
        if row and row["referred_by"]:
            chain.append(row["referred_by"])
            current = row["referred_by"]
        else:
            break
    cur.close()
    conn.close()
    return chain  # [level1_referrer, level2_referrer]


def log_referral_earning(referrer_id, source_user_id, level, amount):
    """প্রতিবার কমিশন দেওয়ার সময় কে থেকে কত এসেছে তা রেকর্ড রাখে"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO referral_earnings (referrer_id, source_user_id, level, amount, created_at) VALUES (%s, %s, %s, %s, %s)",
        (referrer_id, source_user_id, level, amount, datetime.utcnow().isoformat())
    )
    conn.commit()
    cur.close()
    conn.close()


def get_referral_breakdown(user_id):
    """প্রতিটা রেফার করা মানুষ থেকে এখন পর্যন্ত মোট কত টাকা এসেছে তার লিস্ট"""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT re.source_user_id, u.username, re.level, SUM(re.amount) as total
        FROM referral_earnings re
        LEFT JOIN users u ON u.user_id = re.source_user_id
        WHERE re.referrer_id = %s
        GROUP BY re.source_user_id, u.username, re.level
        ORDER BY total DESC
    """, (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = []
    for r in rows:
        result.append({
            "user_id": r["source_user_id"],
            "username": r["username"] or f"User {r['source_user_id']}",
            "level": r["level"],
            "total_earned": round(r["total"], 4)
        })
    return result


# ---------- TASK FUNCTIONS ----------
# (এই ফাংশনগুলো "task_type" স্ট্রিং নেয় বলে tasks_config.py-তে যত ইচ্ছা নতুন
#  টাস্ক আইডি যোগ করলেও এখানে কিছু বদলাতে হয় না।)

def log_task_completion(user_id, task_type, reward):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO task_completions (user_id, task_type, reward, completed_at) VALUES (%s, %s, %s, %s)",
        (user_id, task_type, reward, datetime.utcnow().isoformat())
    )
    conn.commit()
    cur.close()
    conn.close()


def has_completed_task_today(user_id, task_type):
    """রিপিটেবল দৈনিক টাস্কের জন্য (যেমন Watch Ad) - আজ অন্তত একবার হয়েছে কিনা"""
    return get_task_completion_count_today(user_id, task_type) > 0


def has_completed_task_ever(user_id, task_type):
    """এককালীন টাস্কের জন্য (সার্ভে, অফার, অ্যাপ ইনস্টল ইত্যাদি) - জীবনে একবারও হয়েছে কিনা"""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT 1 FROM task_completions WHERE user_id=%s AND task_type=%s LIMIT 1", (user_id, task_type))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row is not None


def get_task_completion_count_today(user_id, task_type):
    """আজকে এই টাস্কটা কতবার সম্পন্ন হয়েছে (দৈনিক-লিমিট টাস্কের জন্য)"""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    today = datetime.utcnow().date().isoformat()
    cur.execute(
        "SELECT COUNT(*) as cnt FROM task_completions WHERE user_id=%s AND task_type=%s AND completed_at LIKE %s",
        (user_id, task_type, f"{today}%")
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["cnt"] if row else 0


def get_today_summary(user_id):
    """আজকে (UTC তারিখ অনুযায়ী) ইউজার মোট কতগুলো টাস্ক করেছে আর কত ডলার আয় করেছে -
    Home পেজে 'আজকের আয়' আর 'আজকের টাস্ক' বক্স দেখানোর জন্য।"""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    today = datetime.utcnow().date().isoformat()
    cur.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(reward), 0) as total "
        "FROM task_completions WHERE user_id=%s AND completed_at LIKE %s",
        (user_id, f"{today}%")
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return (row["cnt"] if row else 0), round(row["total"], 4) if row else 0.0


def get_task_completion_count_ever(user_id, task_type):
    """এই টাস্কটা জীবনে মোট কতবার সম্পন্ন হয়েছে"""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT COUNT(*) as cnt FROM task_completions WHERE user_id=%s AND task_type=%s",
        (user_id, task_type)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row["cnt"] if row else 0


def get_last_task_completion_time(user_id, task_type):
    """এই টাস্কটা সর্বশেষ কখন সম্পন্ন হয়েছে (কুলডাউন চেক করার জন্য)"""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT completed_at FROM task_completions WHERE user_id=%s AND task_type=%s ORDER BY completed_at DESC LIMIT 1",
        (user_id, task_type)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return None
    return datetime.fromisoformat(row["completed_at"])


# ---------- WITHDRAWAL FUNCTIONS ----------

def create_withdrawal(user_id, method, amount_usd, account_info):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO withdrawals (user_id, method, amount_usd, account_info, requested_at) VALUES (%s, %s, %s, %s, %s)",
        (user_id, method, amount_usd, account_info, datetime.utcnow().isoformat())
    )
    conn.commit()
    cur.close()
    conn.close()


def get_pending_withdrawals():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM withdrawals WHERE status='pending' ORDER BY requested_at ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def update_withdrawal_status(withdrawal_id, status):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE withdrawals SET status=%s, processed_at=%s WHERE id=%s",
        (status, datetime.utcnow().isoformat(), withdrawal_id)
    )
    conn.commit()
    cur.close()
    conn.close()


def get_withdrawal(withdrawal_id):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM withdrawals WHERE id=%s", (withdrawal_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


# ---------- CPALEAD CONVERSIONS ----------

def record_cpalead_conversion(lead_id, user_id, offer_id, campaign_name, payout):
    """
    CPAlead postback থেকে আসা conversion স্টোর করার চেষ্টা করে।
    lead_id UNIQUE কলাম হওয়ায়, একই lead_id দ্বিতীয়বার এলে insert হবে না (ON CONFLICT DO NOTHING)।
    Return: True মানে এটা নতুন/প্রথমবার (balance যোগ করা উচিত),
            False মানে এটা আগেই প্রসেস হয়ে গেছে (duplicate - balance যোগ করা যাবে না)।
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO cpalead_conversions (lead_id, user_id, offer_id, campaign_name, payout, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (lead_id) DO NOTHING",
        (lead_id, user_id, offer_id, campaign_name, payout, datetime.utcnow().isoformat())
    )
    inserted = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return inserted


def record_cpalead_start(user_id, offer_id, title, amount):
    """ইউজার একটা CPAlead offer-এ 'Start' চাপলে এটা রেকর্ড হয় - এখান থেকেই 'Pending' লিস্ট তৈরি হয়।"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO cpalead_started (user_id, offer_id, title, amount, started_at) VALUES (%s, %s, %s, %s, %s)",
        (user_id, offer_id, title, amount, datetime.utcnow().isoformat())
    )
    conn.commit()
    cur.close()
    conn.close()


def get_pending_cpalead(user_id):
    """
    ইউজার যেসব CPAlead offer 'Start' করেছে কিন্তু এখনো postback দিয়ে verify হয়নি,
    তাদের তালিকা ফেরত দেয় (Tasks পেজে 'Pending' হিসেবে দেখানোর জন্য)।
    ৩ দিনের বেশি পুরনো এন্ট্রি আর 'Pending' তালিকায় দেখানো হয় না (তালিকা পরিষ্কার
    রাখার জন্য) - কিন্তু এর পরেও যদি CPAlead postback পাঠায়, balance ঠিকই যোগ হবে,
    এই cutoff শুধু UI-তে কতদিন 'Pending' দেখাবে সেটা নিয়ন্ত্রণ করে।
    """
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cutoff = (datetime.utcnow() - timedelta(days=3)).isoformat()
    cur.execute("""
        SELECT s.offer_id, s.title, s.amount, s.started_at
        FROM cpalead_started s
        WHERE s.user_id = %s AND s.started_at >= %s
        AND NOT EXISTS (
            SELECT 1 FROM cpalead_conversions c
            WHERE c.user_id = s.user_id AND c.offer_id = s.offer_id AND c.created_at >= s.started_at
        )
        ORDER BY s.started_at DESC
        LIMIT 20
    """, (user_id, cutoff))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


# ---------- LINK LOCKER TOKENS ----------

def create_link_token(user_id, task_id):
    """
    ইউজার bot-এর ভেতর থেকে Link Locker টাস্কের 'Start' চাপলে একটা একবার-ব্যবহারযোগ্য
    র‍্যান্ডম টোকেন তৈরি করে DB-তে রাখে - এই টোকেনটাই বাইরের বিজ্ঞাপন পেজ থেকে
    ফিরে সঠিক ইউজারকে reward দেওয়ার একমাত্র প্রমাণ।
    """
    import secrets
    token = secrets.token_urlsafe(24)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO link_tokens (token, user_id, task_id, created_at, used) VALUES (%s, %s, %s, %s, 0)",
        (token, user_id, task_id, datetime.utcnow().isoformat())
    )
    conn.commit()
    cur.close()
    conn.close()
    return token


def get_link_token(token):
    """টোকেন দিয়ে খুঁজে বের করে - এখনো valid (unused, ৩০ মিনিটের মধ্যে তৈরি) কিনা চেক করার জন্য।"""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM link_tokens WHERE token=%s", (token,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def mark_link_token_used(token):
    """টোকেন একবার reward দেওয়ার পর 'used' করে দেয় - যাতে একই টোকেন দিয়ে দ্বিতীয়বার
    reward না নেওয়া যায় (লিংক আবার ভিজিট করলে বা রিফ্রেশ করলেও)।"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE link_tokens SET used=1 WHERE token=%s", (token,))
    conn.commit()
    cur.close()
    conn.close()

# ---------- URL SHORTENER ----------

def create_short_link(user_id, destination_url):
    """ইউজার নতুন শর্ট লিংক তৈরি করলে এটা কল হয় - একটা ইউনিক কোড বানিয়ে সংরক্ষণ করে।"""
    import secrets
    code = secrets.token_urlsafe(4)[:5]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO short_links (code, user_id, destination_url, views, total_reward, created_at) "
        "VALUES (%s, %s, %s, 0, 0, %s)",
        (code, user_id, destination_url, datetime.utcnow().isoformat())
    )
    conn.commit()
    cur.close()
    conn.close()
    return code


def get_short_link(code):
    """কোড দিয়ে একটা শর্ট লিংকের তথ্য বের করে (destination, owner ইত্যাদি)।"""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM short_links WHERE code=%s", (code,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def get_user_short_links(user_id):
    """একজন ইউজারের তৈরি করা সব শর্ট লিংক, নতুন থেকে পুরনো ক্রমে।"""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT code, destination_url, views, total_reward, created_at "
        "FROM short_links WHERE user_id=%s ORDER BY id DESC LIMIT 100",
        (user_id,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def has_viewed_recently(code, ip):
    """একই IP গত ২৪ ঘণ্টায় এই লিংক আগে দেখেছে কিনা - দেখলে আর নতুন reward দেওয়া হবে না।"""
    conn = get_conn()
    cur = conn.cursor()
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    cur.execute(
        "SELECT 1 FROM short_link_views WHERE code=%s AND viewer_ip=%s AND created_at >= %s LIMIT 1",
        (code, ip, cutoff)
    )
    exists = cur.fetchone() is not None
    cur.close()
    conn.close()
    return exists


def record_short_link_view(code, ip, reward):
    """নতুন (duplicate না) view রেকর্ড করে, লিংকের views/total_reward বাড়ায়,
    আর owner-এর মূল balance-এও reward যোগ করে।"""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO short_link_views (code, viewer_ip, created_at) VALUES (%s, %s, %s)",
        (code, ip, datetime.utcnow().isoformat())
    )
    cur.execute(
        "UPDATE short_links SET views = views + 1, total_reward = total_reward + %s WHERE code=%s",
        (reward, code)
    )
    conn.commit()
    cur.close()
    conn.close()

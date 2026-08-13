"""
EarnHive - Combined Backend (Flask API + Telegram Webhook)
এই একটামাত্র ফাইল Render/Railway-এর মতো Web Service প্ল্যাটফর্মে চলবে —
Polling-এর বদলে Telegram Webhook ব্যবহার করা হয়েছে, তাই এটা যেকোনো
"request-response" স্টাইল হোস্টিং-এ (Render Free সহ) কাজ করবে।
"""
import hashlib
import hmac
import time
import json
from datetime import datetime
from urllib.parse import parse_qsl

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

import config
import database as db
import tasks_config

app = Flask(__name__)
CORS(app)

db.init_db()

TELEGRAM_API = f"https://api.telegram.org/bot{config.BOT_TOKEN}"


# ================= TELEGRAM AUTH VALIDATION (Mini App) =================

def validate_init_data(init_data: str):
    """Telegram Mini App থেকে পাঠানো initData যাচাই করে (সত্যিকারের Telegram থেকে এসেছে কিনা)"""
    try:
        parsed = dict(parse_qsl(init_data))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            return None

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if computed_hash != received_hash:
            return None

        auth_date = int(parsed.get("auth_date", 0))
        if time.time() - auth_date > 86400:
            return None

        user_data = json.loads(parsed.get("user", "{}"))
        return {
            "user_id": user_data.get("id"),
            "username": user_data.get("username") or user_data.get("first_name"),
            "start_param": parsed.get("start_param"),
        }
    except Exception:
        return None


def get_authed_user():
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    return validate_init_data(init_data)


def require_channel_member(user_id):
    """
    ইউজার রিকোয়ার্ড চ্যানেলে জয়েন করেছে কিনা চেক করে (সার্ভার-সাইড, তাই কেউ
    ফ্রন্টএন্ড বাইপাস করে সরাসরি API কল করলেও এড়াতে পারবে না)।
    আগে DB-তে ক্যাশড ফ্ল্যাগ চেক করে - একবার true হয়ে গেলে বারবার Telegram API
    কল করতে হয় না। এখনো জয়েন না থাকলে লাইভ চেক করে এবং true হলে ক্যাশ করে রাখে।
    is_channel_member() ফাংশনটা এই ফাইলের নিচে (TELEGRAM BOT সেকশনে) সংজ্ঞায়িত -
    Python এ ফাংশন কল-টাইমে resolve হয় বলে এটা এখানে ব্যবহার করতে সমস্যা নেই।
    """
    user = db.get_user(user_id)
    if user and user.get("joined_channel"):
        return True
    if is_channel_member(user_id):
        db.set_joined_channel(user_id, True)
        return True
    return False


# ================= MINI APP API =================

@app.route("/api/auth", methods=["POST"])
def auth():
    auth_user = get_authed_user()
    if not auth_user or not auth_user["user_id"]:
        return jsonify({"error": "invalid_init_data"}), 401

    user_id = auth_user["user_id"]
    existing = db.get_user(user_id)

    if not existing:
        referred_by = None
        # initData-তে start_param সবসময় থাকে না (Telegram স্বয়ংক্রিয়ভাবে আমাদের কাস্টম
        # URL প্যারামিটার initData-তে যোগ করে না) - তাই ফ্রন্টএন্ড URL থেকে সরাসরি পাঠানো
        # start_param-কেই আসল উৎস হিসেবে ব্যবহার করা হচ্ছে।
        body = request.get_json(silent=True) or {}
        start_param = body.get("start_param") or auth_user.get("start_param")
        if start_param and start_param.startswith("ref_"):
            try:
                ref_id = int(start_param.replace("ref_", ""))
                if ref_id != user_id:
                    referred_by = ref_id
            except ValueError:
                pass
        db.create_user(user_id, auth_user["username"], referred_by)
        existing = db.get_user(user_id)

    l1, l2 = db.get_referral_counts(user_id)
    joined_channel = require_channel_member(user_id)
    today_count, today_earned = db.get_today_summary(user_id)

    return jsonify({
        "user_id": existing["user_id"],
        "username": existing["username"],
        "language": existing["language"],
        "balance": existing["balance"],
        "total_earned": existing["total_earned"],
        "referrals": {"level1": l1, "level2": l2},
        "joined_channel": joined_channel,
        "today": {"tasks_count": today_count, "earned": today_earned},
        "config": {
            "referral_rates": [config.REFERRAL_LEVEL_1, config.REFERRAL_LEVEL_2],
            "min_withdraw_bkash": config.MIN_WITHDRAW_BKASH,
            "min_withdraw_usdt": config.MIN_WITHDRAW_USDT,
            "min_withdraw_faucetpay": config.MIN_WITHDRAW_FAUCETPAY,
            "required_channel": config.REQUIRED_CHANNEL,
        }
    })


@app.route("/api/language", methods=["POST"])
def set_language():
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401

    lang = request.json.get("language")
    if lang not in config.LANGUAGES:
        return jsonify({"error": "invalid_language"}), 400

    db.set_language(auth_user["user_id"], lang)
    return jsonify({"ok": True})


def distribute_referral_commission(user_id, reward_amount):
    chain = db.get_referral_chain(user_id)
    percents = [config.REFERRAL_LEVEL_1, config.REFERRAL_LEVEL_2]

    for level, referrer_id in enumerate(chain):
        if level >= len(percents):
            break
        commission = reward_amount * (percents[level] / 100)
        if commission > 0:
            db.add_balance(referrer_id, commission)
            db.log_referral_earning(referrer_id, user_id, level + 1, commission)
            try:
                requests.post(f"{TELEGRAM_API}/sendMessage", json={
                    "chat_id": referrer_id,
                    "text": f"🎉 আপনার Level {level+1} রেফার থেকে ${commission:.4f} কমিশন পেয়েছেন!"
                }, timeout=5)
            except Exception:
                pass


@app.route("/api/verify_channel", methods=["POST"])
def verify_channel():
    """Mini App থেকে ইউজার 'জয়েন করেছি - ভেরিফাই করুন' চাপলে এটা কল হয়।
    লাইভ Telegram API চেক করে, জয়েন থাকলে DB-তে ক্যাশ করে রাখে।"""
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    joined = is_channel_member(user_id)
    if joined:
        db.set_joined_channel(user_id, True)
    return jsonify({"ok": True, "joined": joined})


@app.route("/api/tasks", methods=["GET"])
def get_tasks():
    """
    tasks_config.py-এর TASKS লিস্ট থেকে সব চালু টাস্ক + প্রতিটার বর্তমান স্ট্যাটাস (আজ কতবার হয়েছে) পাঠায়।
    নতুন টাস্ক শুধু tasks_config.py-তে যোগ করলেই এখানে এমনিতেই দেখা যাবে, এই ফাংশন বদলাতে হবে না।
    """
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    if not require_channel_member(user_id):
        return jsonify({"error": "channel_not_joined", "channel": config.REQUIRED_CHANNEL}), 403

    result = []
    for t in tasks_config.get_enabled_tasks():
        if t["limit_type"] == "daily":
            done_count = db.get_task_completion_count_today(user_id, t["id"])
        else:  # "once"
            done_count = db.get_task_completion_count_ever(user_id, t["id"])
        limit_count = t["limit_count"]
        result.append({
            "id": t["id"],
            "title_key": t["title_key"],
            "icon": t["icon"],
            "icon_class": t["icon_class"],
            "reward": t["reward"],
            "limit_type": t["limit_type"],
            "limit_count": limit_count,
            "done_count": done_count,
            "maxed": done_count >= limit_count,
            "action_type": t["action_type"],
            "sdk_src": t.get("sdk_src"),
            "sdk_zone": t.get("sdk_zone"),
            "sdk_function": t.get("sdk_function"),
            "link_url": t.get("link_url"),
            "wait_seconds": t.get("wait_seconds"),
        })
    return jsonify({"tasks": result})


@app.route("/api/task/<task_id>/claim", methods=["POST"])
def claim_task(task_id):
    """
    যেকোনো টাস্ক ক্লেইম করার একটামাত্র সাধারণ (generic) এন্ডপয়েন্ট।
    tasks_config.py থেকে টাস্কের তথ্য পড়ে লিমিট চেক করে, তারপর রিওয়ার্ড দেয়।
    """
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    if not require_channel_member(user_id):
        return jsonify({"error": "channel_not_joined", "channel": config.REQUIRED_CHANNEL}), 403

    task = tasks_config.get_task_by_id(task_id)
    if not task or not task.get("enabled", True):
        return jsonify({"error": "task_not_found"}), 404

    if task["limit_type"] == "daily":
        done_count = db.get_task_completion_count_today(user_id, task_id)
    else:
        done_count = db.get_task_completion_count_ever(user_id, task_id)

    if done_count >= task["limit_count"]:
        return jsonify({"error": "daily_limit_reached"}), 400

    reward = task["reward"]
    db.add_balance(user_id, reward)
    db.log_task_completion(user_id, task_id, reward)
    distribute_referral_commission(user_id, reward)

    user = db.get_user(user_id)
    new_count = done_count + 1
    return jsonify({
        "ok": True, "reward": reward, "new_balance": user["balance"],
        "done_count": new_count, "limit_count": task["limit_count"],
        "maxed": new_count >= task["limit_count"]
    })


@app.route("/api/cpalead/config", methods=["GET"])
def cpalead_config():
    """
    Mini App-এর ফ্রন্টএন্ড (ইউজারের নিজের ফোনে চলা ব্রাউজার) এখান থেকে publisher_id
    আর subid নিয়ে, তারপর CPAlead-এর Offers API-কে *সরাসরি নিজে থেকেই* কল করবে
    (country=user&device=user দিয়ে) - যাতে CPAlead সত্যিকারের ইউজার IP/User-Agent
    দেখে সঠিক দেশ ও ডিভাইস অনুযায়ী offer ফেরত দেয়। আমাদের সার্ভার থেকে এই কল করলে
    CPAlead আমাদের সার্ভারের IP/লোকেশন দেখত, যেটা ভুল হতো।

    subid হিসেবে আমরা ইউজারের নিজস্ব verified Telegram user_id পাঠাচ্ছি (initData
    দিয়ে ভেরিফাই করা, ক্লায়েন্টের দেওয়া কোনো মান না) - যাতে postback ফিরে এলে
    আমরা নিশ্চিতভাবে সঠিক ইউজারকে ক্রেডিট দিতে পারি।
    """
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    if not require_channel_member(user_id):
        return jsonify({"error": "channel_not_joined", "channel": config.REQUIRED_CHANNEL}), 403

    if not config.CPALEAD_PUBLISHER_ID:
        return jsonify({"enabled": False})

    return jsonify({
        "enabled": True,
        "publisher_id": config.CPALEAD_PUBLISHER_ID,
        "subid": str(user_id),
        "easy_max_reward": config.CPALEAD_EASY_MAX_REWARD,
        "user_share_percent": config.CPALEAD_USER_SHARE_PERCENT,
    })


@app.route("/api/cpalead/postback", methods=["GET"])
def cpalead_postback():
    """
    CPAlead-এর সার্ভার এই এন্ডপয়েন্টে কল করবে যখন কোনো offer সত্যিকারের conversion
    (verified completion) হিসেবে গণ্য হয়। এটা ইউজারের ব্রাউজার থেকে না, CPAlead-এর
    নিজের সার্ভার থেকে সরাসরি কল হয় (server-to-server) - তাই Telegram initData
    ভেরিফিকেশন এখানে প্রযোজ্য না, বরং password প্যারামিটার দিয়ে যাচাই করা হয়।

    CPAlead dashboard-এ (Settings > Global Postback / Offerwall Postback) এই URL বসাতে হবে:
    {MINI_APP_BACKEND_URL}/api/cpalead/postback?subid={subid}&lead_id={lead_id}&campaign_id={campaign_id}&campaign_name={campaign_name}&payout={payout}&password=YOUR_SECRET
    """
    password = request.args.get("password", "")
    if not config.CPALEAD_POSTBACK_PASSWORD or password != config.CPALEAD_POSTBACK_PASSWORD:
        return jsonify({"status": "error", "message": "invalid_password"}), 403

    subid = request.args.get("subid", "").strip()
    lead_id = request.args.get("lead_id", "").strip()
    offer_id = request.args.get("campaign_id", "").strip()
    campaign_name = request.args.get("campaign_name", "").strip()
    payout_raw = request.args.get("payout", "0")

    if not subid or not lead_id:
        return jsonify({"status": "error", "message": "missing_subid_or_lead_id"}), 400

    try:
        user_id = int(subid)
    except ValueError:
        return jsonify({"status": "error", "message": "invalid_subid"}), 400

    try:
        payout = float(payout_raw)
    except (TypeError, ValueError):
        payout = 0.0

    if payout <= 0:
        return jsonify({"status": "error", "message": "invalid_payout"}), 400

    user = db.get_user(user_id)
    if not user:
        # যাকে ক্রেডিট দেওয়ার কথা সেই ইউজারই আমাদের সিস্টেমে নেই - স্প্যাম/ভুল subid,
        # কোনো balance যোগ করা হবে না।
        return jsonify({"status": "error", "message": "user_not_found"}), 404

    is_new = db.record_cpalead_conversion(lead_id, user_id, offer_id, campaign_name, payout)
    if not is_new:
        # আগেই প্রসেস হয়ে গেছে - দ্বিতীয়বার টাকা যোগ হবে না, কিন্তু CPAlead-কে 2xx-ই
        # পাঠানো হচ্ছে যাতে ওরা এটাকে ব্যর্থ ধরে বারবার রিট্রাই না করে।
        return jsonify({"status": "success", "duplicate": True})

    # CPAlead যে পুরো payout পাঠিয়েছে, তার একটা অংশই ইউজারকে দেওয়া হয় (config.py-তে
    # CPALEAD_USER_SHARE_PERCENT দিয়ে ঠিক করা) - পুরো payout এমনিতেই আপনার নিজের
    # CPAlead অ্যাকাউন্টে জমা হয়ে যায়, এটা শুধু অ্যাপের ভেতরের ভাগ ঠিক করে।
    user_share = payout * (config.CPALEAD_USER_SHARE_PERCENT / 100)

    db.add_balance(user_id, user_share)
    db.log_task_completion(user_id, f"cpalead_{offer_id}" if offer_id else "cpalead", user_share)
    distribute_referral_commission(user_id, user_share)

    return jsonify({"status": "success", "duplicate": False})


@app.route("/api/cpalead/track_start", methods=["POST"])
def cpalead_track_start():
    """
    ইউজার কোনো CPAlead offer-এ 'Start' চাপলে ফ্রন্টএন্ড এটা কল করে - এখান থেকেই
    Tasks পেজে 'Pending' লিস্ট তৈরি হয়, যতক্ষণ না postback দিয়ে verify হয়।
    """
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    if not require_channel_member(user_id):
        return jsonify({"error": "channel_not_joined", "channel": config.REQUIRED_CHANNEL}), 403

    body = request.get_json(silent=True) or {}
    offer_id = str(body.get("offer_id", "")).strip()
    title = str(body.get("title", "")).strip()[:200]
    try:
        amount = float(body.get("amount", 0))
    except (TypeError, ValueError):
        amount = 0.0

    if not offer_id:
        return jsonify({"error": "missing_offer_id"}), 400

    db.record_cpalead_start(user_id, offer_id, title, amount)
    return jsonify({"ok": True})


@app.route("/api/cpalead/pending", methods=["GET"])
def cpalead_pending():
    """ইউজার যেসব CPAlead offer শুরু করেছে কিন্তু এখনো verify হয়নি, তার তালিকা।"""
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    pending = db.get_pending_cpalead(user_id)
    return jsonify({"pending": pending})


@app.route("/api/referral", methods=["GET"])
def referral_info():
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    bot_info = requests.get(f"{TELEGRAM_API}/getMe", timeout=5).json()
    bot_username = bot_info.get("result", {}).get("username", "")
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    l1, l2 = db.get_referral_counts(user_id)
    breakdown = db.get_referral_breakdown(user_id)

    return jsonify({
        "link": link,
        "level1": l1, "level2": l2,
        "rates": [config.REFERRAL_LEVEL_1, config.REFERRAL_LEVEL_2],
        "breakdown": breakdown,
    })


@app.route("/api/withdraw", methods=["POST"])
def request_withdraw():
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    if not require_channel_member(user_id):
        return jsonify({"error": "channel_not_joined", "channel": config.REQUIRED_CHANNEL}), 403

    method = request.json.get("method")
    account_info = request.json.get("account_info", "").strip()
    amount_raw = request.json.get("amount")

    if method not in ("bkash", "usdt", "faucetpay") or not account_info:
        return jsonify({"error": "invalid_request"}), 400

    try:
        amount = float(amount_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_amount"}), 400

    if amount <= 0:
        return jsonify({"error": "invalid_amount"}), 400

    user = db.get_user(user_id)
    min_amount = {
        "bkash": config.MIN_WITHDRAW_BKASH,
        "usdt": config.MIN_WITHDRAW_USDT,
        "faucetpay": config.MIN_WITHDRAW_FAUCETPAY,
    }[method]

    if amount < min_amount:
        return jsonify({"error": "insufficient_balance", "min_required": min_amount}), 400

    if amount > user["balance"]:
        return jsonify({"error": "invalid_amount"}), 400

    db.deduct_balance(user_id, amount)
    db.create_withdrawal(user_id, method, amount, account_info)

    for admin_id in config.ADMIN_IDS:
        try:
            requests.post(f"{TELEGRAM_API}/sendMessage", json={
                "chat_id": admin_id,
                "text": f"🔔 New withdrawal\nUser: {user_id}\nMethod: {method}\n"
                        f"Amount: ${amount:.4f}\nAccount: {account_info}"
            }, timeout=5)
        except Exception:
            pass

    updated_user = db.get_user(user_id)
    return jsonify({"ok": True, "amount": amount, "new_balance": updated_user["balance"]})


# ================= TELEGRAM BOT (WEBHOOK) =================
# Polling-এর বদলে Telegram নিজে থেকেই এই URL-এ প্রতিটা মেসেজ/বাটন-ক্লিক পাঠাবে।
# সিকিউরিটির জন্য URL-এর মধ্যেই বট টোকেন ব্যবহার করা হয়েছে (অনুমান করা কঠিন করতে)।

def tg_send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=5)
    except Exception:
        pass


def is_channel_member(user_id):
    try:
        resp = requests.get(f"{TELEGRAM_API}/getChatMember", params={
            "chat_id": config.REQUIRED_CHANNEL, "user_id": user_id
        }, timeout=5).json()
        status = resp.get("result", {}).get("status")
        return status in ("member", "administrator", "creator")
    except Exception:
        return False


def send_open_app_message(chat_id, start_param=""):
    web_app_url = config.MINI_APP_URL
    if start_param:
        web_app_url = f"{config.MINI_APP_URL}?start_param={start_param}"
    reply_markup = {
        "inline_keyboard": [[
            {"text": "🚀 Open EarnHive", "web_app": {"url": web_app_url}}
        ]]
    }
    tg_send_message(chat_id, "🎉 EarnHive-এ স্বাগতম!\n\nনিচের বাটনে ক্লিক করে অ্যাপ খুলুন এবং আয় শুরু করুন।", reply_markup)


def send_join_channel_message(chat_id, user_id, start_param=""):
    channel_username = config.REQUIRED_CHANNEL.lstrip("@")
    reply_markup = {
        "inline_keyboard": [
            [{"text": "📢 চ্যানেলে জয়েন করুন", "url": f"https://t.me/{channel_username}"}],
            [{"text": "✅ জয়েন করেছি - ভেরিফাই করুন", "callback_data": f"checkjoin_{start_param}"}]
        ]
    }
    tg_send_message(
        chat_id,
        "🎉 EarnHive-এ স্বাগতম!\n\n"
        "অ্যাপ ব্যবহার শুরু করার আগে আমাদের অফিসিয়াল চ্যানেলে জয়েন করতে হবে।\n\n"
        "1️⃣ নিচে চ্যানেলে জয়েন করুন\n"
        "2️⃣ তারপর \"জয়েন করেছি\" বাটনে ক্লিক করুন",
        reply_markup
    )


def handle_start_command(message):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = message.get("text", "")
    parts = text.split(maxsplit=1)
    start_param = parts[1] if len(parts) > 1 else ""

    if is_channel_member(user_id):
        send_open_app_message(chat_id, start_param)
    else:
        send_join_channel_message(chat_id, user_id, start_param)


def handle_pending_command(message):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]

    if user_id not in config.ADMIN_IDS:
        return

    pending = db.get_pending_withdrawals()
    if not pending:
        tg_send_message(chat_id, "কোনো পেন্ডিং উইথড্র নেই।")
        return

    for w in pending:
        reply_markup = {
            "inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": f"approve_{w['id']}"},
                {"text": "❌ Reject", "callback_data": f"reject_{w['id']}"}
            ]]
        }
        text = (f"ID: {w['id']}\nUser: {w['user_id']}\nMethod: {w['method']}\n"
                f"Amount: ${w['amount_usd']:.4f}\nAccount: {w['account_info']}\n"
                f"Requested: {w['requested_at']}")
        tg_send_message(chat_id, text, reply_markup)


def handle_callback_query(callback_query):
    from_user_id = callback_query["from"]["id"]
    data = callback_query["data"]
    callback_id = callback_query["id"]
    message_id = callback_query["message"]["message_id"]
    chat_id = callback_query["message"]["chat"]["id"]

    if data.startswith("checkjoin_"):
        start_param = data.replace("checkjoin_", "", 1)
        if is_channel_member(from_user_id):
            try:
                requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": callback_id}, timeout=5)
                requests.post(f"{TELEGRAM_API}/deleteMessage", json={
                    "chat_id": chat_id, "message_id": message_id
                }, timeout=5)
            except Exception:
                pass
            send_open_app_message(chat_id, start_param)
        else:
            try:
                requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json={
                    "callback_query_id": callback_id,
                    "text": "❌ এখনো চ্যানেলে জয়েন করেননি! আগে জয়েন করুন।",
                    "show_alert": True
                }, timeout=5)
            except Exception:
                pass
        return

    try:
        requests.post(f"{TELEGRAM_API}/answerCallbackQuery", json={"callback_query_id": callback_id}, timeout=5)
    except Exception:
        pass

    if from_user_id not in config.ADMIN_IDS:
        return
    if not (data.startswith("approve_") or data.startswith("reject_")):
        return

    action, wid = data.split("_")
    wid = int(wid)
    withdrawal = db.get_withdrawal(wid)

    if not withdrawal or withdrawal["status"] != "pending":
        requests.post(f"{TELEGRAM_API}/editMessageText", json={
            "chat_id": chat_id, "message_id": message_id,
            "text": "এই রিকোয়েস্টটি ইতিমধ্যে প্রসেস করা হয়েছে।"
        }, timeout=5)
        return

    if action == "approve":
        db.update_withdrawal_status(wid, "approved")
        tg_send_message(withdrawal["user_id"], f"🎉 আপনার ${withdrawal['amount_usd']:.4f} উইথড্র সম্পন্ন হয়েছে!")
        result_text = f"✅ Approved: ID {wid}"
    else:
        db.update_withdrawal_status(wid, "rejected")
        db.add_balance(withdrawal["user_id"], withdrawal["amount_usd"])
        tg_send_message(withdrawal["user_id"], "❌ দুঃখিত, আপনার উইথড্র রিকোয়েস্ট বাতিল করা হয়েছে। ব্যালেন্স ফেরত দেওয়া হয়েছে।")
        result_text = f"❌ Rejected: ID {wid}"

    try:
        requests.post(f"{TELEGRAM_API}/editMessageText", json={
            "chat_id": chat_id, "message_id": message_id, "text": result_text
        }, timeout=5)
    except Exception:
        pass


@app.route(f"/webhook/{config.BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    update = request.get_json(silent=True) or {}

    if "message" in update:
        message = update["message"]
        text = message.get("text", "")
        if text.startswith("/start"):
            handle_start_command(message)
        elif text.startswith("/pending"):
            handle_pending_command(message)

    elif "callback_query" in update:
        handle_callback_query(update["callback_query"])

    return jsonify({"ok": True})


@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    """
    এটা ডিপ্লয়ের পর একবার ব্রাউজারে খুলতে হবে (Render/Railway URL পাওয়ার পর) —
    এটাই Telegram-কে জানাবে কোথায় আপডেট পাঠাতে হবে।
    উদাহরণ: https://your-app.onrender.com/set_webhook
    """
    webhook_url = f"{config.MINI_APP_BACKEND_URL}/webhook/{config.BOT_TOKEN}"
    resp = requests.get(f"{TELEGRAM_API}/setWebhook", params={"url": webhook_url}, timeout=10).json()
    return jsonify(resp)


@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "EarnHive backend is running"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

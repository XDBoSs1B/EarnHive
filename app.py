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
        start_param = auth_user.get("start_param")
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

    return jsonify({
        "user_id": existing["user_id"],
        "username": existing["username"],
        "language": existing["language"],
        "balance": existing["balance"],
        "total_earned": existing["total_earned"],
        "referrals": {"level1": l1, "level2": l2},
        "config": {
            "referral_rates": [config.REFERRAL_LEVEL_1, config.REFERRAL_LEVEL_2],
            "min_withdraw_bkash": config.MIN_WITHDRAW_BKASH,
            "min_withdraw_usdt": config.MIN_WITHDRAW_USDT,
            "reward_channel_join": config.REWARD_CHANNEL_JOIN,
            "reward_ad_view": config.REWARD_AD_VIEW,
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
            try:
                requests.post(f"{TELEGRAM_API}/sendMessage", json={
                    "chat_id": referrer_id,
                    "text": f"🎉 আপনার Level {level+1} রেফার থেকে ${commission:.4f} কমিশন পেয়েছেন!"
                }, timeout=5)
            except Exception:
                pass


@app.route("/api/task/channel/verify", methods=["POST"])
def verify_channel_task():
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    if db.has_completed_task_today(user_id, "channel_join"):
        return jsonify({"error": "already_completed"}), 400

    try:
        resp = requests.get(f"{TELEGRAM_API}/getChatMember", params={
            "chat_id": config.REQUIRED_CHANNEL, "user_id": user_id
        }, timeout=5).json()
        status = resp.get("result", {}).get("status")
        if status not in ("member", "administrator", "creator"):
            return jsonify({"error": "not_joined"}), 400
    except Exception:
        return jsonify({"error": "verification_failed"}), 500

    reward = config.REWARD_CHANNEL_JOIN
    db.add_balance(user_id, reward)
    db.log_task_completion(user_id, "channel_join", reward)
    distribute_referral_commission(user_id, reward)

    user = db.get_user(user_id)
    return jsonify({"ok": True, "reward": reward, "new_balance": user["balance"]})


@app.route("/api/task/ad/claim", methods=["POST"])
def claim_ad_task():
    """NOTE: CPAlead/AdsGram/Monetag SDK-এর "reward" ইভেন্ট থেকে এটা কল হবে।"""
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    if db.has_completed_task_today(user_id, "ad_view"):
        return jsonify({"error": "already_completed"}), 400

    reward = config.REWARD_AD_VIEW
    db.add_balance(user_id, reward)
    db.log_task_completion(user_id, "ad_view", reward)
    distribute_referral_commission(user_id, reward)

    user = db.get_user(user_id)
    return jsonify({"ok": True, "reward": reward, "new_balance": user["balance"]})


@app.route("/api/task/status", methods=["GET"])
def task_status():
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    return jsonify({
        "channel_join": db.has_completed_task_today(user_id, "channel_join"),
        "ad_view": db.has_completed_task_today(user_id, "ad_view"),
    })


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

    return jsonify({
        "link": link,
        "level1": l1, "level2": l2,
        "rates": [config.REFERRAL_LEVEL_1, config.REFERRAL_LEVEL_2],
    })


@app.route("/api/withdraw", methods=["POST"])
def request_withdraw():
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    method = request.json.get("method")
    account_info = request.json.get("account_info", "").strip()

    if method not in ("bkash", "usdt") or not account_info:
        return jsonify({"error": "invalid_request"}), 400

    user = db.get_user(user_id)
    min_amount = config.MIN_WITHDRAW_BKASH if method == "bkash" else config.MIN_WITHDRAW_USDT

    if user["balance"] < min_amount:
        return jsonify({"error": "insufficient_balance", "min_required": min_amount}), 400

    amount = user["balance"]
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

    return jsonify({"ok": True, "amount": amount})


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


def handle_start_command(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    parts = text.split(maxsplit=1)
    start_param = parts[1] if len(parts) > 1 else ""

    web_app_url = config.MINI_APP_URL
    if start_param:
        web_app_url = f"{config.MINI_APP_URL}?start_param={start_param}"

    reply_markup = {
        "inline_keyboard": [[
            {"text": "🚀 Open EarnHive", "web_app": {"url": web_app_url}}
        ]]
    }
    tg_send_message(chat_id, "🎉 EarnHive-এ স্বাগতম!\n\nনিচের বাটনে ক্লিক করে অ্যাপ খুলুন এবং আয় শুরু করুন।", reply_markup)


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

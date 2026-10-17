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
        # t-এর সব ফিল্ড (id, title_key, desc_key, reward, link_url ইত্যাদি - যা কিছু
        # tasks_config.py-তে লেখা থাকুক) এমনিতেই এখানে চলে আসে - নতুন কোনো ফিল্ড
        # (যেমন ভবিষ্যতে "video_url" বা অন্য কিছু) tasks_config.py-তে যোগ করলে এই
        # ফাংশন আর বদলাতে হবে না, শুধু "enabled" ফ্ল্যাগটা বাদ দেওয়া হচ্ছে কারণ
        # সেটা শুধু সার্ভারের নিজের হিসাব, ফ্রন্টএন্ডের দরকার নেই।
        task_data = {k: v for k, v in t.items() if k != "enabled"}
        task_data["done_count"] = done_count
        task_data["maxed"] = done_count >= limit_count
        # ম্যানুয়াল রিভিউ টাস্কে ইউজারের অ্যাডমিন-রিভিউর অপেক্ষায় থাকা সাবমিশন আছে কিনা -
        # থাকলে ফ্রন্টএন্ড "Pending review" দেখাবে (অন্য কোনো টাস্কের আচরণ এতে বদলায় না)।
        if t.get("action_type") == "manual_review":
            task_data["pending_review"] = db.count_pending_manual_review(user_id, t["id"]) > 0
        result.append(task_data)
    return jsonify({"tasks": result})


@app.route("/api/task/<task_id>/start", methods=["POST"])
def start_task(task_id):
    """
    'wait_seconds' থাকা (external_link) টাস্কে ইউজার লিংক ওপেন করার সময় ফ্রন্টএন্ড এটা
    কল করে - সার্ভারে একটা সময়-স্ট্যাম্প রেকর্ড হয়, যেটা claim_task পরে যাচাই করবে।
    শুধু ক্লায়েন্টের timer বিশ্বাস না করে সার্ভার নিজেই বাস্তব সময় গণনা করবে, তাই
    কেউ ক্লিক করেই সাথে সাথে ফিরে এসে claim করার চেষ্টা করলে সেটা প্রত্যাখ্যাত হবে।
    """
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    task = tasks_config.get_task_by_id(task_id)
    if not task or not task.get("enabled", True) or not task.get("wait_seconds"):
        return jsonify({"error": "task_not_found"}), 404

    db.record_task_start(user_id, task_id)
    return jsonify({"ok": True})


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

    # wait_seconds থাকা (external_link) টাস্কের জন্য সার্ভার-সাইড সময় যাচাই -
    # ইউজার সত্যিই কমপক্ষে wait_seconds সময় অপেক্ষা করেছে কিনা, ক্লায়েন্টের কথায় না।
    if task.get("wait_seconds"):
        started = db.get_task_start(user_id, task_id)
        if not started:
            return jsonify({"error": "not_started"}), 400
        elapsed = (datetime.utcnow() - started).total_seconds()
        if elapsed < task["wait_seconds"]:
            return jsonify({"error": "wait_not_complete", "remaining": round(task["wait_seconds"] - elapsed, 1)}), 400

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
    if task.get("wait_seconds"):
        db.clear_task_start(user_id, task_id)

    user = db.get_user(user_id)
    new_count = done_count + 1
    return jsonify({
        "ok": True, "reward": reward, "new_balance": user["balance"],
        "done_count": new_count, "limit_count": task["limit_count"],
        "maxed": new_count >= task["limit_count"]
    })


# =====================================================================
# LINK LOCKER — নিজের বানানো ২-ধাপের বিজ্ঞাপন পেজ
# ফ্লো: বটে Start চাপা -> token তৈরি -> /l/<task_id>?t=token (পেজ ১, ১৫সে + বিজ্ঞাপন)
#      -> Go বাটন -> /l/<task_id>/step2?t=token (পেজ ২, ১৫সে + বিজ্ঞাপন)
#      -> Get Link বাটন -> সার্ভার token verify করে reward দেয় -> আসল লিংকে redirect
# এই পুরো ফ্লো Telegram-এর বাইরে (আলাদা ব্রাউজারে) হয়, তাই Telegram initData পাওয়া
# যায় না - token-ই একমাত্র প্রমাণ যে এটা কোন ইউজারের জন্য গণ্য হবে।
# =====================================================================

def _linklocker_page_html(title, wait_seconds, next_url, button_text, is_final=False):
    """
    Link Locker-এর একটা ধাপের (step) HTML পেজ বানায় - কাউন্টডাউন + বিজ্ঞাপনের
    placeholder জায়গা + ওয়েট শেষে বাটন। Adsterra (বা অন্য) বিজ্ঞাপন কোড পেলে
    নিচের AD_SLOT_1 / AD_SLOT_2 কমেন্টের জায়গায় বসিয়ে দিলেই চলবে।
    """
    button_action = (
        f"completeAndRedirect('{next_url}')" if is_final
        else f"window.location.href='{next_url}'"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  body{{ background:#0d0f1c; color:#fff; font-family:sans-serif; text-align:center;
        margin:0; padding:24px 16px; min-height:100vh; box-sizing:border-box; }}
  h2{{ margin-top:10px; }}
  .ad-slot{{ background:rgba(255,255,255,0.06); border:1px dashed rgba(255,255,255,0.2);
             border-radius:12px; padding:40px 12px; margin:20px auto; max-width:420px;
             color:rgba(255,255,255,0.4); font-size:13px; }}
  .timer{{ font-size:42px; font-weight:800; margin:20px 0; color:#ff7a54; }}
  .go-btn{{ display:none; background:linear-gradient(135deg,#4dd8e6,#ffb37a); color:#1a0e08;
            border:none; padding:16px 40px; border-radius:14px; font-weight:800;
            font-size:16px; cursor:pointer; }}
  .go-btn.show{{ display:inline-block; }}
</style>
</head>
<body>
  <h2>⏳ Please wait...</h2>

  <!-- AD_SLOT_1: এখানে Adsterra/অন্য নেটওয়ার্কের বিজ্ঞাপন কোড বসবে -->
  <div class="ad-slot">Ad Space 1</div>

  <div class="timer" id="countdown">{wait_seconds}</div>

  <!-- AD_SLOT_2: এখানেও আরেকটা বিজ্ঞাপন কোড বসানো যাবে -->
  <div class="ad-slot">Ad Space 2</div>

  <button class="go-btn" id="goBtn">{button_text}</button>

  <script>
    let remaining = {wait_seconds};
    const el = document.getElementById("countdown");
    const btn = document.getElementById("goBtn");
    const timer = setInterval(()=>{{
      remaining -= 1;
      el.textContent = remaining;
      if(remaining <= 0){{
        clearInterval(timer);
        el.style.display = "none";
        btn.classList.add("show");
      }}
    }}, 1000);

    btn.addEventListener("click", ()=>{{
      btn.disabled = true;
      btn.textContent = "...";
      {button_action}
    }});

    async function completeAndRedirect(tokenParam){{
      try{{
        const res = await fetch(tokenParam);
        const data = await res.json();
        if(data.ok){{
          window.location.href = data.destination;
        }} else {{
          document.body.innerHTML = "<h2>এই লিংকটি ব্যবহার করা হয়ে গেছে বা মেয়াদ শেষ।</h2>";
        }}
      }} catch(e){{
        document.body.innerHTML = "<h2>একটা সমস্যা হয়েছে, আবার চেষ্টা করুন।</h2>";
      }}
    }}
  </script>
</body>
</html>"""


@app.route("/api/linktask/<task_id>/start", methods=["POST"])
def linktask_start(task_id):
    """বটের ভেতর থেকে Link Locker টাস্কে 'Start' চাপলে এটা কল হয় - একটা টোকেন
    তৈরি করে দেয়, যেটা দিয়ে বাইরের বিজ্ঞাপন-পেজের লিংক বানানো হবে।"""
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    if not require_channel_member(user_id):
        return jsonify({"error": "channel_not_joined", "channel": config.REQUIRED_CHANNEL}), 403

    task = tasks_config.get_task_by_id(task_id)
    if not task or not task.get("enabled", True) or task.get("action_type") != "link_locker":
        return jsonify({"error": "task_not_found"}), 404

    if task["limit_type"] == "daily":
        done_count = db.get_task_completion_count_today(user_id, task_id)
    else:
        done_count = db.get_task_completion_count_ever(user_id, task_id)
    if done_count >= task["limit_count"]:
        return jsonify({"error": "daily_limit_reached"}), 400

    token = db.create_link_token(user_id, task_id)
    url = f"{config.MINI_APP_BACKEND_URL}/l/{task_id}?t={token}"
    return jsonify({"ok": True, "url": url})


@app.route("/l/<task_id>", methods=["GET"])
def linklocker_step1(task_id):
    """Link Locker-এর প্রথম পেজ - Telegram-এর বাইরে সাধারণ ব্রাউজারে খোলে।"""
    token = request.args.get("t", "")
    row = db.get_link_token(token)
    if not row or row["task_id"] != task_id or row["used"]:
        return "<h2 style='color:#fff;background:#0d0f1c;padding:40px;font-family:sans-serif;'>এই লিংকটি অবৈধ বা মেয়াদোত্তীর্ণ।</h2>", 400

    next_url = f"/l/{task_id}/step2?t={token}"
    html = _linklocker_page_html("Step 1", 15, next_url, "Go →", is_final=False)
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/l/<task_id>/step2", methods=["GET"])
def linklocker_step2(task_id):
    """Link Locker-এর দ্বিতীয়/শেষ পেজ - এই পেজের বাটনেই reward credit হয় এবং
    আসল destination লিংকে redirect করা হয়।"""
    token = request.args.get("t", "")
    row = db.get_link_token(token)
    if not row or row["task_id"] != task_id or row["used"]:
        return "<h2 style='color:#fff;background:#0d0f1c;padding:40px;font-family:sans-serif;'>এই লিংকটি অবৈধ বা মেয়াদোত্তীর্ণ।</h2>", 400

    complete_url = f"/api/linktask/complete?t={token}"
    html = _linklocker_page_html("Step 2", 15, complete_url, "Get Link", is_final=True)
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/linktask/complete", methods=["GET"])
def linktask_complete():
    """
    Link Locker-এর শেষ পেজের বাটন এটা কল করে - token verify করে, দৈনিক লিমিট চেক
    করে, তারপরই reward যোগ করে আর আসল destination URL ফেরত দেয় (redirect করার জন্য)।
    """
    token = request.args.get("t", "")
    row = db.get_link_token(token)
    if not row or row["used"]:
        return jsonify({"ok": False, "error": "invalid_token"}), 400

    # ৩০ মিনিটের বেশি পুরনো টোকেন গ্রহণযোগ্য না (কেউ লিংক সংরক্ষণ করে অনেক পরে
    # ব্যবহার করার চেষ্টা করলে সেটা ঠেকানোর জন্য)
    created = datetime.fromisoformat(row["created_at"])
    if (datetime.utcnow() - created).total_seconds() > 1800:
        return jsonify({"ok": False, "error": "token_expired"}), 400

    task_id = row["task_id"]
    user_id = row["user_id"]
    task = tasks_config.get_task_by_id(task_id)
    if not task or task.get("action_type") != "link_locker":
        return jsonify({"ok": False, "error": "task_not_found"}), 404

    if task["limit_type"] == "daily":
        done_count = db.get_task_completion_count_today(user_id, task_id)
    else:
        done_count = db.get_task_completion_count_ever(user_id, task_id)
    if done_count >= task["limit_count"]:
        return jsonify({"ok": False, "error": "daily_limit_reached"}), 400

    if not require_channel_member(user_id):
        return jsonify({"ok": False, "error": "channel_not_joined"}), 403

    reward = task["reward"]
    db.add_balance(user_id, reward)
    db.log_task_completion(user_id, task_id, reward)
    distribute_referral_commission(user_id, reward)
    db.mark_link_token_used(token)

    return jsonify({"ok": True, "destination": task["destination_url"]})


# =====================================================================
# URL SHORTENER ("৫ নম্বর ঘর")
# ইউজার নিজের লিংক শর্ট করে, যে কেউ (Telegram ছাড়াই, যেকোনো ব্রাউজার থেকে) সেটাতে
# ক্লিক করলে ৩-ধাপের বিজ্ঞাপন পেজ দেখে, শেষে আসল লিংকে পৌঁছায় - আর যে বানিয়েছিল
# তার balance-এ reward যোগ হয় (একই IP ২৪ ঘণ্টায় দ্বিতীয়বার গণনা হয় না)।
# =====================================================================

def get_client_ip():
    """Render-এর মতো প্রক্সির পেছনে থাকলে real client IP আনার জন্য।"""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr or "unknown"


@app.route("/api/shortlink/create", methods=["POST"])
def shortlink_create():
    """বটের Shortener পেজ থেকে নতুন লিংক তৈরি করার জন্য।"""
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    if not require_channel_member(user_id):
        return jsonify({"error": "channel_not_joined", "channel": config.REQUIRED_CHANNEL}), 403

    body = request.get_json(silent=True) or {}
    destination_url = str(body.get("destination_url", "")).strip()

    if not destination_url or not (destination_url.startswith("http://") or destination_url.startswith("https://")):
        return jsonify({"error": "invalid_url"}), 400
    if len(destination_url) > 2000:
        return jsonify({"error": "url_too_long"}), 400

    code = db.create_short_link(user_id, destination_url)
    short_url = f"{config.MINI_APP_BACKEND_URL}/s/{code}"
    return jsonify({"ok": True, "code": code, "short_url": short_url})


@app.route("/api/shortlink/list", methods=["GET"])
def shortlink_list():
    """ইউজারের তৈরি করা সব লিংক + সারাংশ (মোট লিংক/view/আয়) ফেরত দেয়।"""
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    links = db.get_user_short_links(user_id)
    total_views = sum(l["views"] for l in links)
    total_earned = sum(l["total_reward"] for l in links)

    for l in links:
        l["short_url"] = f"{config.MINI_APP_BACKEND_URL}/s/{l['code']}"

    return jsonify({
        "links": links,
        "summary": {
            "total_links": len(links),
            "total_views": total_views,
            "total_earned": round(total_earned, 4),
        }
    })


def _shortlink_step_html(step, total_steps, code, wait_seconds, next_action, button_text, is_final=False):
    """৩-ধাপের বিজ্ঞাপন পেজের একটা ধাপ। Adsterra-এর ৩টা অ্যাড কোড এখানে বসানো
    আছে (native banner, ব্যানার, সোশ্যাল বার) - যদি ads দেখা না যায়, সেটা এই
    কোডের সমস্যা না; Adsterra ড্যাশবোর্ডে ডোমেইন approve হয়েছে কিনা, বা
    Telegram-এর in-app browser ব্লক করছে কিনা - এই দুটো আগে চেক করুন।"""
    action_js = f"completeAndRedirect('{next_action}')" if is_final else f"window.location.href='{next_action}'"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EarnHive Link</title>
<script src="https://pl30996895.profitableratecpmnetwork.com/8a/22/7c/8a227cb28dda795e2e8406c24441d3fb.js"></script>
<style>
  body{{ background:#0a0c1c; color:#fff; font-family:sans-serif; text-align:center;
        margin:0; padding:24px 16px; min-height:100vh; box-sizing:border-box; }}
  .step-label{{ font-size:12px; color:rgba(255,255,255,0.4); text-transform:uppercase;
                letter-spacing:0.06em; margin-bottom:6px; }}
  h2{{ margin-top:4px; }}
  .ad-slot{{ background:rgba(255,255,255,0.06); border:1px dashed rgba(255,255,255,0.2);
             border-radius:12px; padding:40px 12px; margin:16px auto; max-width:420px;
             color:rgba(255,255,255,0.4); font-size:13px; }}
  .timer{{ font-size:42px; font-weight:800; margin:16px 0; color:#ffb37a; }}
  .go-btn{{ display:none; background:linear-gradient(135deg,#4dd8e6,#ffb37a); color:#1a0e08;
            border:none; padding:16px 40px; border-radius:14px; font-weight:800;
            font-size:16px; cursor:pointer; }}
  .go-btn.show{{ display:inline-block; }}
  .btn-zone{{ margin:22px 0; padding:10px 0; }}
</style>
</head>
<body>
  <div class="step-label">STEP {step} / {total_steps}</div>
  <h2>⏳ Please wait...</h2>
  <div class="ad-slot" id="container-8452aed23c6fa87e0c37f7318a905dcb"></div>
  <script async="async" data-cfasync="false" src="https://pl30996897.profitableratecpmnetwork.com/8452aed23c6fa87e0c37f7318a905dcb/invoke.js"></script>
  <div class="timer" id="countdown">{wait_seconds}</div>
  <div class="ad-slot"><script type="text/javascript">
	atOptions = {{
		'key' : 'ca59a32229f38b0f1d3f353b0d6a216a',
		'format' : 'iframe',
		'height' : 250,
		'width' : 300,
		'params' : {{}}
	}};
	document.write('<scr' + 'ipt src="https://www.profitableratecpmnetwork.com/ca59a32229f38b0f1d3f353b0d6a216a/invoke.js"></scr' + 'ipt>');
  </script></div>
  <div class="btn-zone">
  <button class="go-btn" id="goBtn">{button_text}</button>
  </div>
  <script>
    let remaining = {wait_seconds};
    const el = document.getElementById("countdown");
    const btn = document.getElementById("goBtn");
    const timer = setInterval(()=>{{
      remaining -= 1;
      el.textContent = remaining;
      if(remaining <= 0){{
        clearInterval(timer);
        el.style.display = "none";
        btn.classList.add("show");
      }}
    }}, 1000);
    btn.addEventListener("click", ()=>{{
      btn.disabled = true;
      btn.textContent = "...";
      {action_js}
    }});
    async function completeAndRedirect(url){{
      try{{
        const res = await fetch(url);
        const data = await res.json();
        if(data.ok){{
          window.location.href = data.destination;
        }} else {{
          document.body.innerHTML = "<h2>এই লিংকটি খুঁজে পাওয়া যায়নি।</h2>";
        }}
      }} catch(e){{
        document.body.innerHTML = "<h2>একটা সমস্যা হয়েছে, আবার চেষ্টা করুন।</h2>";
      }}
    }}
  </script>
  <script src="https://pl30996896.profitableratecpmnetwork.com/7c/35/6c/7c356ced4f6eed4fcd21c38d261ee60d.js"></script>
</body>
</html>"""


@app.route("/s/<code>", methods=["GET"])
def shortlink_step1(code):
    link = db.get_short_link(code)
    if not link:
        return "<h2 style='color:#fff;background:#0a0c1c;padding:40px;font-family:sans-serif;'>এই লিংকটি খুঁজে পাওয়া যায়নি।</h2>", 404

    # একই IP ২৪ ঘণ্টায় আগেই এই লিংক দেখে থাকলে - বিজ্ঞাপন পেজ আবার না দেখিয়ে
    # সরাসরি destination-এ পাঠানো হচ্ছে, যাতে duplicate impression গিয়ে CPM/account
    # trust নষ্ট না হয় (reward তো এমনিতেই দ্বিতীয়বার যেত না, কিন্তু বিজ্ঞাপনও
    # অহেতুক দেখানো ঠিক না)।
    ip = get_client_ip()
    if db.has_viewed_recently(code, ip):
        return "", 302, {"Location": link["destination_url"]}

    html = _shortlink_step_html(1, 3, code, 10, f"/s/{code}/step2", "Go →")
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/s/<code>/step2", methods=["GET"])
def shortlink_step2(code):
    link = db.get_short_link(code)
    if not link:
        return "<h2 style='color:#fff;background:#0a0c1c;padding:40px;font-family:sans-serif;'>এই লিংকটি খুঁজে পাওয়া যায়নি।</h2>", 404
    html = _shortlink_step_html(2, 3, code, 10, f"/s/{code}/step3", "Go →")
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/s/<code>/step3", methods=["GET"])
def shortlink_step3(code):
    link = db.get_short_link(code)
    if not link:
        return "<h2 style='color:#fff;background:#0a0c1c;padding:40px;font-family:sans-serif;'>এই লিংকটি খুঁজে পাওয়া যায়নি।</h2>", 404
    html = _shortlink_step_html(3, 3, code, 10, f"/api/shortlink/view/{code}", "Get Link", is_final=True)
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/shortlink/view/<code>", methods=["GET"])
def shortlink_view_complete(code):
    """
    ৩-ধাপ শেষে এটা কল হয় - একই IP ২৪ ঘণ্টায় দ্বিতীয়বার হলে reward দেওয়া হয় না
    (fraud protection), কিন্তু viewer তবুও ঠিকভাবে destination-এ পৌঁছাবে।
    """
    link = db.get_short_link(code)
    if not link:
        return jsonify({"ok": False, "error": "not_found"}), 404

    ip = get_client_ip()
    if not db.has_viewed_recently(code, ip):
        reward = config.SHORTLINK_REWARD_PER_VIEW
        db.record_short_link_view(code, ip, reward)
        db.add_balance(link["user_id"], reward)
        distribute_referral_commission(link["user_id"], reward)

    return jsonify({"ok": True, "destination": link["destination_url"]})


@app.route("/api/adsgram/postback", methods=["GET"])
def adsgram_postback():
    """
    AdsGram-এর সার্ভার ইউজার একটা Rewarded Video সম্পূর্ণ দেখে ফেললে এই এন্ডপয়েন্টে
    কল করে। AdsGram partner ড্যাশবোর্ডে (Ad Block > Rewarded URL) এই লিংক বসাতে হয়:

    {MINI_APP_BACKEND_URL}/api/adsgram/postback?userid=[userId]&secret=YOUR_SECRET

    AdsGram নিজে থেকেই [userId] অংশটা আসল Telegram user id দিয়ে replace করে পাঠায়।
    """
    secret = request.args.get("secret", "")
    if not config.ADSGRAM_POSTBACK_SECRET or secret != config.ADSGRAM_POSTBACK_SECRET:
        return jsonify({"status": "error", "message": "invalid_secret"}), 403

    userid_raw = request.args.get("userid", "").strip()
    if not userid_raw:
        return jsonify({"status": "error", "message": "missing_userid"}), 400

    try:
        user_id = int(userid_raw)
    except ValueError:
        return jsonify({"status": "error", "message": "invalid_userid"}), 400

    user = db.get_user(user_id)
    if not user:
        return jsonify({"status": "error", "message": "user_not_found"}), 404

    today_count = db.count_today_adsgram_views(user_id)
    if today_count >= config.ADSGRAM_DAILY_LIMIT:
        # লিমিট শেষ - AdsGram-কে তবুও success (2xx) পাঠানো হচ্ছে যাতে ওরা retry না করে,
        # কিন্তু কোনো balance যোগ হবে না।
        return jsonify({"status": "success", "limit_reached": True})

    reward = config.ADSGRAM_REWARD_PER_VIEW
    db.record_adsgram_view(user_id, reward)
    db.add_balance(user_id, reward)
    db.log_task_completion(user_id, "adsgram_view", reward)
    distribute_referral_commission(user_id, reward)

    return jsonify({"status": "success", "reward": reward})


# =====================================================================
# CPX RESEARCH — সার্ভে অফারওয়াল
# ফ্লো: Mini App-এ ইউজার টাস্কে চাপলে /api/cpx/link থেকে স্বাক্ষরিত (secure_hash)
# ব্যক্তিগত অফারওয়াল URL পাওয়া যায় -> সেটা bot-এর বাইরে (Telegram openLink) খোলা
# হয় -> ইউজার সার্ভে শেষ করলে CPX নিজে থেকে আমাদের /api/cpx/postback কল করে ->
# সেখানেই hash verify করে reward credit হয় (ক্লায়েন্ট কোনো claim কল করে না,
# ঠিক AdsGram-এর মতোই async postback প্যাটার্ন)।
# =====================================================================

def cpx_signed_link(user_id, username=""):
    h = hashlib.md5(f"{user_id}-{config.CPX_SECRET}".encode()).hexdigest()
    url = (
        "https://offers.cpx-research.com/index.php"
        f"?app_id={config.CPX_APP_ID}&ext_user_id={user_id}&secure_hash={h}"
    )
    # CPX ডকুমেন্টেশনে username "recommended" - ইউজার চেনার সুবিধার জন্য (থাকলে যোগ হয়)
    if username:
        from urllib.parse import quote
        url += f"&username={quote(str(username), safe='')}"
    return url


@app.route("/api/cpx/link", methods=["GET"])
def cpx_link_endpoint():
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    if not config.CPX_SECRET:
        return jsonify({"error": "not_configured"}), 503
    return jsonify({"ok": True, "url": cpx_signed_link(auth_user["user_id"], auth_user.get("username") or "")})


@app.route("/api/cpx/postback", methods=["GET"])
def cpx_postback():
    """
    CPX ড্যাশবোর্ডের Postback Settings-এ এই URL বসাতে হবে:

    {MINI_APP_BACKEND_URL}/api/cpx/postback?user_id={user_id}&amount={amount}
        &trans_id={trans_id}&status={status}&secure_hash={secure_hash}

    CPX নিজে থেকেই {user_id}/{amount}/{trans_id}/{status}/{secure_hash}
    অংশগুলো আসল মান দিয়ে replace করে কল করে।
    """
    if not config.CPX_SECRET:
        return jsonify({"status": "error", "message": "not_configured"}), 503

    uid_raw = request.args.get("user_id", "").strip()
    given_hash = request.args.get("secure_hash", "").lower().strip()
    expected_hash = hashlib.md5(f"{uid_raw}-{config.CPX_SECRET}".encode()).hexdigest()
    if not hmac.compare_digest(given_hash, expected_hash):
        return jsonify({"status": "error", "message": "bad_hash"}), 403

    if not uid_raw.isdigit():
        return jsonify({"status": "error", "message": "bad_user_id"}), 400
    user_id = int(uid_raw)

    trans_id = request.args.get("trans_id", "").strip()
    if not trans_id:
        return jsonify({"status": "error", "message": "missing_trans_id"}), 400

    user = db.get_user(user_id)
    if not user:
        return jsonify({"status": "error", "message": "user_not_found"}), 404

    # status=2 মানে CPX পরে এই লেনদেনটা বাতিল/রিভার্স করেছে (যেমন ইউজার সার্ভেতে মিথ্যা
    # তথ্য দিয়েছিল ধরা পড়লে) - আগে credited থাকলে সেই পরিমাণ ব্যালেন্স থেকে ফেরত নেওয়া হয়।
    if request.args.get("status", "1") == "2":
        db.reverse_cpx_transaction(trans_id)
        return jsonify({"status": "success", "reversed": True})

    try:
        usd = float(request.args.get("amount", "0"))
    except ValueError:
        return jsonify({"status": "error", "message": "bad_amount"}), 400

    credited = round(usd * config.CPX_USER_SHARE, 4)
    inserted = db.record_cpx_transaction(trans_id, user_id, usd, credited)
    if not inserted:
        # একই trans_id আগে প্রসেস হয়ে গেছে (CPX মাঝে মাঝে postback retry করে) -
        # দ্বিতীয়বার আর balance যোগ করা হচ্ছে না।
        return jsonify({"status": "success", "duplicate": True})

    db.add_balance(user_id, credited)
    db.log_task_completion(user_id, "cpx_survey", credited)
    distribute_referral_commission(user_id, credited)
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": user_id,
            "text": f"🎉 সার্ভে সম্পন্ন হয়েছে! +${credited:.4f} যোগ হয়েছে।"
        }, timeout=5)
    except Exception:
        pass

    return jsonify({"status": "success", "credited": credited})


# =====================================================================
# ম্যানুয়াল রিভিউ টাস্ক (স্ক্রিনশট আপলোড -> "Ck" চ্যানেলে Approve/Cancel)
# ফ্লো: Mini App-এ ইউজার স্ক্রিনশট জমা দেয় -> এখানে ছবিগুলো প্রাইভেট চ্যানেলে পাঠানো হয় ->
# একটা কন্ট্রোল মেসেজে Approve/Cancel বাটন আসে -> অ্যাডমিন চাপলে handle_callback_query
# (নিচে) reward দেয় বা বাতিল করে। reward শুধু Approve-এর সময়ই যোগ হয়।
# =====================================================================

MANUAL_REVIEW_MAX_FILE_BYTES = 8 * 1024 * 1024  # প্রতিটা ছবি সর্বোচ্চ ৮ MB


@app.route("/api/manual_review/<task_id>/submit", methods=["POST"])
def manual_review_submit(task_id):
    auth_user = get_authed_user()
    if not auth_user:
        return jsonify({"error": "invalid_init_data"}), 401
    user_id = auth_user["user_id"]

    if not require_channel_member(user_id):
        return jsonify({"error": "channel_not_joined", "channel": config.REQUIRED_CHANNEL}), 403

    task = tasks_config.get_task_by_id(task_id)
    if not task or not task.get("enabled", True) or task.get("action_type") != "manual_review":
        return jsonify({"error": "task_not_found"}), 404

    user = db.get_user(user_id)
    if not user:
        return jsonify({"error": "user_not_found"}), 404

    # লিমিট: অ্যাপ্রুভ হওয়া + এখনো pending থাকা মিলিয়ে limit_count ছাড়ালে নতুন সাবমিশন নেওয়া হবে না
    if task["limit_type"] == "daily":
        done_count = db.get_task_completion_count_today(user_id, task_id)
    else:
        done_count = db.get_task_completion_count_ever(user_id, task_id)
    pending = db.count_pending_manual_review(user_id, task_id)
    if pending > 0:
        return jsonify({"error": "already_pending"}), 400
    if done_count >= task["limit_count"]:
        return jsonify({"error": "daily_limit_reached"}), 400

    # ফাইল যাচাই - কতগুলো স্লট দরকার (screenshot_labels-এর সংখ্যা, না থাকলে ১টা)
    required = len(task.get("screenshot_labels", [])) or 1
    required = min(required, config.MANUAL_REVIEW_MAX_SCREENSHOTS)
    files = [f for f in request.files.getlist("screenshots") if f and f.filename]
    if len(files) != required:
        return jsonify({"error": "wrong_screenshot_count", "required": required}), 400

    blobs = []
    for f in files:
        data = f.read()
        if not data or len(data) > MANUAL_REVIEW_MAX_FILE_BYTES:
            return jsonify({"error": "bad_file_size"}), 400
        if not (f.mimetype or "").startswith("image/"):
            return jsonify({"error": "not_an_image"}), 400
        blobs.append((f.filename, data, f.mimetype))

    note = (request.form.get("note") or "").strip()[:500]
    chat_id = config.MANUAL_REVIEW_CHANNEL_ID
    username = user.get("username") or "-"
    caption = f"📝 ম্যানুয়াল রিভিউ\nটাস্ক: {task_id}\nUser: {user_id} (@{username})"
    if note:
        caption += f"\nনোট: {note}"

    # ১) ছবিগুলো চ্যানেলে পাঠানো (একটা হলে sendPhoto, একাধিক হলে অ্যালবাম)
    file_ids = []
    try:
        if len(blobs) == 1:
            name, data, mime = blobs[0]
            resp = requests.post(f"{TELEGRAM_API}/sendPhoto",
                                 data={"chat_id": chat_id, "caption": caption},
                                 files={"photo": (name, data, mime)}, timeout=30).json()
            if not resp.get("ok"):
                return jsonify({"error": "telegram_send_failed"}), 502
            file_ids.append(resp["result"]["photo"][-1]["file_id"])
        else:
            media, files_payload = [], {}
            for i, (name, data, mime) in enumerate(blobs):
                key = f"file{i}"
                item = {"type": "photo", "media": f"attach://{key}"}
                if i == 0:
                    item["caption"] = caption
                media.append(item)
                files_payload[key] = (name, data, mime)
            resp = requests.post(f"{TELEGRAM_API}/sendMediaGroup",
                                 data={"chat_id": chat_id, "media": json.dumps(media)},
                                 files=files_payload, timeout=60).json()
            if not resp.get("ok"):
                return jsonify({"error": "telegram_send_failed"}), 502
            for m in resp["result"]:
                file_ids.append(m["photo"][-1]["file_id"])
    except Exception:
        return jsonify({"error": "telegram_send_failed"}), 502

    # ২) ছবি পৌঁছানোর পরই DB-তে pending রেকর্ড (ছবি না গেলে কোনো রেকর্ড তৈরি হয় না)
    submission_id = db.create_manual_review_submission(user_id, task_id, note, file_ids, task["reward"])

    # ৩) Approve/Cancel বাটনসহ কন্ট্রোল মেসেজ
    reply_markup = {"inline_keyboard": [[
        {"text": "✅ Approve", "callback_data": f"mrapprove_{submission_id}"},
        {"text": "❌ Cancel", "callback_data": f"mrreject_{submission_id}"},
    ]]}
    try:
        ctrl = requests.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": f"রিভিউ ID: {submission_id}\nটাস্ক: {task_id}\nUser: {user_id} (@{username})\n"
                    f"রিওয়ার্ড: ${task['reward']:.4f}",
            "reply_markup": reply_markup,
        }, timeout=10).json()
        if not ctrl.get("ok"):
            db.update_manual_review_status(submission_id, "error")
            return jsonify({"error": "telegram_send_failed"}), 502
    except Exception:
        db.update_manual_review_status(submission_id, "error")
        return jsonify({"error": "telegram_send_failed"}), 502

    return jsonify({"ok": True, "submission_id": submission_id})


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

def tg_send_message(chat_id, text, reply_markup=None, parse_mode=None):
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
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


WELCOME_TEXTS = {
    "en": {
        "open_title": "🐝 <b>Welcome to EarnHive!</b>",
        "open_body": "Complete simple tasks, watch ads, take surveys and earn real money — withdraw anytime.\n\n"
                     "📢 Don't miss updates, payment proofs and new tasks — stay with our <b>official channel</b>.",
        "btn_open": "🚀 Open EarnHive",
        "btn_channel": "📢 Official Channel",
        "join_body": "To use EarnHive you must join our <b>official channel</b> first.\n\n"
                     "1️⃣ Tap <b>Join Official Channel</b>\n2️⃣ Then tap <b>I've Joined - Verify</b>",
        "btn_join": "📢 Join Official Channel",
        "btn_verify": "✅ I've Joined - Verify",
    },
    "bn": {
        "open_title": "🐝 <b>EarnHive-এ স্বাগতম!</b>",
        "open_body": "সহজ টাস্ক, বিজ্ঞাপন দেখা আর সার্ভে করে আসল টাকা আয় করুন — যখন খুশি উইথড্র করুন।\n\n"
                     "📢 নতুন টাস্ক, আপডেট আর পেমেন্ট প্রুফ পেতে আমাদের <b>অফিসিয়াল চ্যানেলে</b> থাকুন।",
        "btn_open": "🚀 EarnHive খুলুন",
        "btn_channel": "📢 অফিসিয়াল চ্যানেল",
        "join_body": "EarnHive ব্যবহার করতে আগে আমাদের <b>অফিসিয়াল চ্যানেলে</b> জয়েন করতে হবে।\n\n"
                     "1️⃣ <b>অফিসিয়াল চ্যানেলে জয়েন করুন</b> বাটনে চাপ দিন\n2️⃣ তারপর <b>জয়েন করেছি - ভেরিফাই</b> চাপ দিন",
        "btn_join": "📢 অফিসিয়াল চ্যানেলে জয়েন করুন",
        "btn_verify": "✅ জয়েন করেছি - ভেরিফাই",
    },
    "ar": {
        "open_title": "🐝 <b>مرحبًا بك في EarnHive!</b>",
        "open_body": "أكمل مهام بسيطة، شاهد الإعلانات وأجب عن الاستبيانات واربح مالًا حقيقيًا — واسحب في أي وقت.\n\n"
                     "📢 لا تفوّت التحديثات وإثباتات الدفع والمهام الجديدة — ابقَ مع <b>قناتنا الرسمية</b>.",
        "btn_open": "🚀 افتح EarnHive",
        "btn_channel": "📢 القناة الرسمية",
        "join_body": "لاستخدام EarnHive يجب أن تنضم إلى <b>قناتنا الرسمية</b> أولاً.\n\n"
                     "1️⃣ اضغط <b>انضم إلى القناة الرسمية</b>\n2️⃣ ثم اضغط <b>انضممت - تحقق</b>",
        "btn_join": "📢 انضم إلى القناة الرسمية",
        "btn_verify": "✅ انضممت - تحقق",
    },
}


def _welcome_lang(user_id):
    """ইউজারের সেভ করা ভাষা (না পেলে en)।"""
    try:
        u = db.get_user(user_id)
        lang = (u or {}).get("language") or "en"
        return lang if lang in WELCOME_TEXTS else "en"
    except Exception:
        return "en"


def send_open_app_message(chat_id, start_param=""):
    web_app_url = config.MINI_APP_URL
    if start_param:
        web_app_url = f"{config.MINI_APP_URL}?start_param={start_param}"
    channel_username = config.REQUIRED_CHANNEL.lstrip("@")
    t = WELCOME_TEXTS[_welcome_lang(chat_id)]
    reply_markup = {
        "inline_keyboard": [
            [{"text": t["btn_open"], "web_app": {"url": web_app_url}}],
            [{"text": t["btn_channel"], "url": f"https://t.me/{channel_username}"}],
        ]
    }
    tg_send_message(chat_id, f"{t['open_title']}\n\n{t['open_body']}", reply_markup, parse_mode="HTML")


def send_join_channel_message(chat_id, user_id, start_param=""):
    channel_username = config.REQUIRED_CHANNEL.lstrip("@")
    t = WELCOME_TEXTS[_welcome_lang(user_id)]
    reply_markup = {
        "inline_keyboard": [
            [{"text": t["btn_join"], "url": f"https://t.me/{channel_username}"}],
            [{"text": t["btn_verify"], "callback_data": f"checkjoin_{start_param}"}]
        ]
    }
    tg_send_message(chat_id, f"{t['open_title']}\n\n{t['join_body']}", reply_markup, parse_mode="HTML")


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
                    "text": "❌ You haven't joined the channel yet! Please join first.",
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

    if data.startswith("mrapprove_") or data.startswith("mrreject_"):
        approve = data.startswith("mrapprove_")
        try:
            submission_id = int(data.split("_", 1)[1])
        except ValueError:
            return
        submission = db.get_manual_review_submission(submission_id)

        if not submission or submission["status"] != "pending":
            try:
                requests.post(f"{TELEGRAM_API}/editMessageText", json={
                    "chat_id": chat_id, "message_id": message_id,
                    "text": "এই সাবমিশনটি ইতিমধ্যে প্রসেস করা হয়েছে।"
                }, timeout=5)
            except Exception:
                pass
            return

        if approve:
            reward = submission["reward"]
            db.update_manual_review_status(submission_id, "approved")
            db.add_balance(submission["user_id"], reward)
            db.log_task_completion(submission["user_id"], submission["task_id"], reward)
            distribute_referral_commission(submission["user_id"], reward)
            tg_send_message(submission["user_id"], f"🎉 আপনার সাবমিশন Approve হয়েছে! ${reward:.4f} যোগ হয়েছে।")
            result_text = f"✅ Approved: ID {submission_id} (${reward:.4f})"
        else:
            db.update_manual_review_status(submission_id, "rejected")
            tg_send_message(submission["user_id"], "❌ দুঃখিত, আপনার সাবমিশনটি বাতিল করা হয়েছে। নির্দেশনা অনুযায়ী আবার চেষ্টা করুন।")
            result_text = f"❌ Cancelled: ID {submission_id}"

        try:
            requests.post(f"{TELEGRAM_API}/editMessageText", json={
                "chat_id": chat_id, "message_id": message_id, "text": result_text
            }, timeout=5)
        except Exception:
            pass
        return

    if data.startswith("photoapprove_") or data.startswith("photoreject_"):
        action = "photoapprove" if data.startswith("photoapprove_") else "photoreject"
        submission_id = int(data.split("_")[1])
        submission = db.get_photo_submission(submission_id)

        if not submission or submission["status"] != "pending":
            requests.post(f"{TELEGRAM_API}/editMessageCaption", json={
                "chat_id": chat_id, "message_id": message_id,
                "caption": "এই ছবিটি ইতিমধ্যে প্রসেস করা হয়েছে।"
            }, timeout=5)
            return

        if action == "photoapprove":
            reward = config.PHOTO_UPLOAD_REWARD
            db.update_photo_submission_status(submission_id, "approved", reward)
            db.add_balance(submission["user_id"], reward)
            db.log_task_completion(submission["user_id"], "photo_upload", reward)
            tg_send_message(submission["user_id"], f"🎉 আপনার ছবি Approve হয়েছে! ${reward:.4f} যোগ হয়েছে।")
            result_caption = f"✅ Approved: ID {submission_id} (${reward:.4f})"
        else:
            db.update_photo_submission_status(submission_id, "rejected", 0)
            tg_send_message(submission["user_id"], "❌ দুঃখিত, আপনার ছবিটি গ্রহণযোগ্য মানের না হওয়ায় বাতিল করা হয়েছে।")
            result_caption = f"❌ Rejected: ID {submission_id}"

        try:
            requests.post(f"{TELEGRAM_API}/editMessageCaption", json={
                "chat_id": chat_id, "message_id": message_id, "caption": result_caption
            }, timeout=5)
        except Exception:
            pass
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


def handle_photo_message(message):
    """
    ইউজার বটে ছবি পাঠালে এটা কল হয়। দৈনিক লিমিট চেক করে, সাবমিশন সেভ করে,
    ইউজারকে কনফার্মেশন পাঠায়, আর অ্যাডমিনকে Approve/Reject বাটনসহ ছবিটা ফরওয়ার্ড করে -
    ঠিক উইথড্র রিকোয়েস্টের মতোই।
    """
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]

    # অ্যাডমিন নিজে ছবি পাঠালে এটা টাস্ক-সাবমিশন হিসেবে গণ্য হবে না
    if user_id in config.ADMIN_IDS:
        return

    user = db.get_user(user_id)
    if not user:
        return

    today_count = db.count_today_photo_submissions(user_id)
    if today_count >= config.PHOTO_UPLOAD_DAILY_LIMIT:
        tg_send_message(
            chat_id,
            f"⚠️ আজকের ছবি আপলোডের লিমিট ({config.PHOTO_UPLOAD_DAILY_LIMIT}টা) শেষ। "
            f"কাল আবার চেষ্টা করুন।"
        )
        return

    # সবচেয়ে বড় রেজোলিউশনের ভার্সনটা নেওয়া হয় (Telegram একই ছবির কয়েকটা সাইজ পাঠায়)
    photos = message.get("photo")
    if not photos:
        return
    file_id = photos[-1]["file_id"]

    submission_id = db.create_photo_submission(user_id, file_id)

    tg_send_message(
        chat_id,
        f"✅ ছবি পাওয়া গেছে (ID: {submission_id})।\n"
        f"২৪ ঘণ্টার মধ্যে রিভিউ করে জানানো হবে। অ্যাপ্রুভ হলে ব্যালেন্সে যোগ হয়ে যাবে।"
    )

    reply_markup = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"photoapprove_{submission_id}"},
            {"text": "❌ Reject", "callback_data": f"photoreject_{submission_id}"}
        ]]
    }
    caption = (
        f"🖼 নতুন ছবি সাবমিশন\nID: {submission_id}\nUser: {user_id}"
        f" (@{user.get('username') or '-'})\nআজকে জমা: {today_count + 1}/{config.PHOTO_UPLOAD_DAILY_LIMIT}"
    )
    for admin_id in config.ADMIN_IDS:
        try:
            requests.post(f"{TELEGRAM_API}/sendPhoto", json={
                "chat_id": admin_id,
                "photo": file_id,
                "caption": caption,
                "reply_markup": reply_markup
            }, timeout=10)
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
        elif "photo" in message:
            handle_photo_message(message)

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

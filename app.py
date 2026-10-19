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
    ver = getattr(config, "MINI_APP_VERSION", "")
    web_app_url = f"{config.MINI_APP_URL}?v={ver}" if ver else config.MINI_APP_URL
    if start_param:
        web_app_url = f"{config.MINI_APP_URL}?start_param={start_param}" + (f"&v={ver}" if ver else "")
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


# Mini App-এর index.html এখানে সংকুচিত (zlib+base64) আকারে বসানো আছে, তাই শুধু app.py আপলোড করলেই
# Render থেকে নতুন অ্যাপের পর্দা পাওয়া যায় (আলাদা index.html ফাইল/ফোল্ডার নিয়ে ঝামেলা নেই)।
# index.html বদলালে এই অংশটা আবার তৈরি করে দিতে হয় (আমি করে দেব)।
_MINI_APP_HTML_B64 = (
    "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"
)


@app.route("/app", methods=["GET"])
def serve_mini_app():
    """
    Mini App-এর পর্দা সরাসরি এই Render সার্ভার থেকে দেয় (Netlify লাগে না)।
    ঠিকানা: https://<render-url>/app
    """
    import base64
    import zlib
    from flask import make_response
    html = zlib.decompress(base64.b64decode("".join(_MINI_APP_HTML_B64))).decode("utf-8")
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

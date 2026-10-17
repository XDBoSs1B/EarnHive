# =========================================
# EarnHive Bot - Configuration
# সব সেটিংস এখানে থেকে বদলাতে পারবেন
# =========================================

# --- Bot Token (BotFather থেকে পাওয়া) ---
BOT_TOKEN = "8806039653:AAGEmdjdumzUXELbnT4OZ90Trv9bwh7eOv4"

# --- Admin User ID(s) ---
ADMIN_IDS = [6669633686]

# --- প্রোমোশন চ্যানেল (জয়েন টাস্কের জন্য) ---
# বটকে অবশ্যই এই চ্যানেলের Admin বানাতে হবে
REQUIRED_CHANNEL = "@Earn_Hive67"

# --- Mini App ও Backend-এর লাইভ URL (ডিপ্লয়ের পর বসাতে হবে) ---
MINI_APP_URL = "https://fabulous-banoffee-808e08.netlify.app"     # Frontend (Netlify)
MINI_APP_BACKEND_URL = "https://earnhive-lnlq.onrender.com"       # Backend (Render) - আসল লাইভ URL

# --- রেফারেল কমিশন রেট (%) ---
REFERRAL_LEVEL_1 = 10   # Direct
REFERRAL_LEVEL_2 = 5    # Indirect

# --- টাস্কের রিওয়ার্ড/লিমিট এখন এখানে না, তাদের নিজস্ব ফাইলে ---
# tasks_config.py দেখুন - নতুন টাস্ক (অ্যাড নেটওয়ার্ক, অফার, সার্ভে ইত্যাদি)
# যোগ/পরিবর্তন করতে শুধু ওই ফাইলটাই এডিট করুন, এখানে কিছু বদলাতে হবে না।

# --- উইথড্র মিনিমাম (সবসময় $ এ) ---
MIN_WITHDRAW_BKASH = 2.00
MIN_WITHDRAW_USDT = 5.00
MIN_WITHDRAW_FAUCETPAY = 3.00

# --- bKash কনভার্সন রেট ($1 = কত টাকা) ---
# এডমিন প্যানেল থেকেও বদলানো যাবে, এটা শুধু ডিফল্ট
USD_TO_BDT_RATE = 120

# --- উইথড্র প্রসেসিং সময় (মেসেজে দেখানোর জন্য) ---
WITHDRAW_PROCESSING_TEXT = {
    "bn": "১২-২৪ ঘন্টার মধ্যে পেমেন্ট করা হবে",
    "en": "Payment will be processed within 12-24 hours",
    "ar": "سيتم الدفع خلال 12-24 ساعة",
}

# --- ডাটাবেস (Supabase PostgreSQL - স্থায়ী স্টোরেজ) ---
# ⚠️ নিরাপত্তার জন্য এই লিংক সরাসরি কোডে না লিখে Render-এর "Environment Variables"
# সেকশনে DATABASE_URL নামে বসাতে হবে (কারণ এতে ডাটাবেসের পাসওয়ার্ড থাকে, GitHub-এ
# পাবলিক কোডে পাসওয়ার্ড রাখা উচিত না)।
import os
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# --- সাপোর্টেড ভাষা ---
LANGUAGES = ["bn", "en", "ar"]
DEFAULT_LANGUAGE = "en"

# =========================================
# AdsGram (Rewarded Video Ad Network)
# =========================================
# Postback URL-এ যাচাইয়ের জন্য secret (URL অনুমান করে ভুয়া রিকোয়েস্ট ঠেকাতে)
ADSGRAM_POSTBACK_SECRET = "earnhive_ads_secret_2026"

# প্রতিটা সম্পূর্ণ দেখা (verified) ভিডিও অ্যাডের জন্য reward (USD এ)
ADSGRAM_REWARD_PER_VIEW = 0.0003

# প্রতিদিন একজন ইউজার সর্বোচ্চ কতটা AdsGram ভিডিও দেখে reward পেতে পারবে
ADSGRAM_DAILY_LIMIT = 20

# =========================================
# ছবি আপলোড টাস্ক (Admin রিভিউ করে Approve করলে reward দেওয়া হয়)
# =========================================
# প্রতিটা Approve হওয়া ছবির জন্য reward (USD এ)
PHOTO_UPLOAD_REWARD = 0.008

# প্রতিদিন একজন ইউজার সর্বোচ্চ কতটা ছবি জমা দিতে পারবে (pending+approved+rejected মিলিয়ে)
PHOTO_UPLOAD_DAILY_LIMIT = 10

# =========================================
# URL Shortener ("৫ নম্বর ঘর")
# =========================================
# প্রতিটা সত্যিকারের (duplicate না) view-এর জন্য শর্ট লিংক তৈরিকারী ইউজার কত পাবে।
# এখনো বিজ্ঞাপন (Adsterra ইত্যাদি) যোগ করা হয়নি বলে এটা এই মুহূর্তে একটা fixed
# সংখ্যা - real বিজ্ঞাপন postback যোগ হলে এটাকেও dynamic করা যাবে।
SHORTLINK_REWARD_PER_VIEW = 0.0003

# =========================================
# CPX Research (সার্ভে অফারওয়াল)
# =========================================
# CPX ড্যাশবোর্ডে কনফার্ম করা আসল App ID (Earn_Hive অ্যাপের পেজ থেকে নেওয়া)
CPX_APP_ID = os.environ.get("CPX_APP_ID", "37157").strip()

# CPX ড্যাশবোর্ডের Secure Hash Key (Earn_Hive অ্যাপের Frame Integration কোড থেকে নেওয়া)।
# চাইলে পরে Render-এর Environment Variables-এ CPX_SECRET নামে বসিয়ে এই ডিফল্ট মান
# ওভাররাইড করা যাবে, কিন্তু এখন env var না থাকলেও এই ডিফল্ট মান দিয়েই কাজ করবে।
CPX_SECRET = os.environ.get("CPX_SECRET", "tZKCxTVk6hcigmRpGzsFxxApEk7geZjI").strip()

# CPX থেকে আসা মোট আয়ের কত অংশ ইউজার পাবে (0.5 = ৫০%)
CPX_USER_SHARE = float(os.environ.get("CPX_USER_SHARE", "0.5"))

# =========================================
# ম্যানুয়াল রিভিউ টাস্ক সিস্টেম (স্ক্রিনশট আপলোড -> প্রাইভেট চ্যানেলে Approve/Cancel)
# =========================================
# "Ck" প্রাইভেট চ্যানেলের ID (স্ক্রিনশটে দেখানো আসল ID: 3734081719)।
# Telegram Bot API-তে চ্যানেলে মেসেজ পাঠাতে হলে chat_id-এর শুরুতে -100 বসাতে হয়,
# তাই এখানে পুরো ফরম্যাট করা মান রাখা হলো। বটকে অবশ্যই এই চ্যানেলের Admin হতে হবে
# (তুমি কনফার্ম করেছ বট ইতিমধ্যে Admin আছে)।
MANUAL_REVIEW_CHANNEL_ID = int(os.environ.get("MANUAL_REVIEW_CHANNEL_ID", "-1003734081719"))

# একটা সাবমিশনে সর্বোচ্চ কতগুলো স্ক্রিনশট আপলোড করা যাবে
MANUAL_REVIEW_MAX_SCREENSHOTS = 3

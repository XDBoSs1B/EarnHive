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
MIN_WITHDRAW_BKASH = 1.00
MIN_WITHDRAW_USDT = 5.00

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

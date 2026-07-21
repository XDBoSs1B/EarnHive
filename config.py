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
MINI_APP_BACKEND_URL = "https://earnhive.onrender.com"            # Backend (Render) - ডিপ্লয়ের পর সঠিক URL বসাতে হবে

# --- রেফারেল কমিশন রেট (%) ---
REFERRAL_LEVEL_1 = 10   # Direct
REFERRAL_LEVEL_2 = 5    # Indirect

# --- টাস্ক রিওয়ার্ড (উদাহরণ, ইচ্ছেমতো বদলান) ---
REWARD_CHANNEL_JOIN = 0.01     # চ্যানেল জয়েন করলে $
REWARD_AD_VIEW = 0.0002        # প্রতিটা অ্যাড দেখলে $ (Monetag)
MAX_AD_VIEWS_PER_DAY = 10      # একজন ইউজার দিনে সর্বোচ্চ এতগুলো অ্যাড দেখে রিওয়ার্ড পাবে
MONETAG_ZONE_ID = "11346798"   # Monetag zone ID (EarnHive-এর নিজস্ব)

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

# --- ডাটাবেস ফাইল ---
DB_PATH = "earnhive.db"

# --- সাপোর্টেড ভাষা ---
LANGUAGES = ["bn", "en", "ar"]
DEFAULT_LANGUAGE = "en"

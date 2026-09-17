# ============================================================
# EarnHive - টাস্ক রেজিস্ট্রি
# নতুন কোনো অ্যাড নেটওয়ার্ক / অফার / ওয়েবসাইট টাস্ক যোগ করতে হলে
# শুধু নিচের TASKS লিস্টে একটা নতুন এন্ট্রি যোগ করুন।
# app.py বা database.py আলাদা করে বদলাতে হবে না।
#
# টাস্কের নিচে ছোট নির্দেশনা/শর্ত দেখাতে চাইলে "desc" ফিল্ডে তিনটা ভাষাতেই
# (bn/en/ar) টেক্সট লিখে দিন - নিচের install_getblock/adsgram_view দেখুন।
# ইউজারের অ্যাপের ভাষা অনুযায়ী সঠিক ভাষারটা এমনিতেই দেখানো হবে।
# এর জন্য index.html-এ আলাদা কিছু যোগ করা লাগবে না, সব এই ফাইলেই থাকে।
# ============================================================

# limit_type এর সম্ভাব্য মান:
#   "daily"    -> প্রতিদিন limit_count বার পর্যন্ত রিওয়ার্ড দেওয়া যাবে (যেমন: Watch Ad)
#   "once"     -> সারাজীবনে মাত্র একবার রিওয়ার্ড দেওয়া হবে (যেমন: নতুন সার্ভে/অফার)
#
# action_type এর সম্ভাব্য মান:
#   "ad_sdk"        -> Monetag/AdsGram এর মতো ইন-অ্যাপ রিওয়ার্ডেড ভিডিও SDK
#   "external_link" -> Adsterra/HilltopAds/অন্য যেকোনো বাইরের লিংকে পাঠিয়ে অপেক্ষার পর claim করানো

TASKS = [
    {
        "id": "ad",
        "title_key": "task_ad",
        "icon": "📺",
        "icon_class": "ad",
        "reward": 0.0001,
        "limit_type": "daily",
        "limit_count": 20,
        "action_type": "ad_sdk",
        "sdk_src": "//libtl.com/sdk.js",
        "sdk_zone": "11346798",
        "sdk_function": "show_11346798",
    },
    {
        "id": "website",
        "title_key": "task_website",
        "icon": "🌐",
        "icon_class": "web",
        "reward": 0.0002,
        "limit_type": "daily",
        "limit_count": 1,
        "action_type": "external_link",
        "link_url": "https://www.effectivecpmnetwork.com/pma1vx5qa?key=0e5832111948b8ea7bea11db254a8a6a",
        "wait_seconds": 15,
    },
    {
        "id": "website2",
        "title_key": "task_website2",
        "icon": "🌐",
        "icon_class": "web",
        "reward": 0.0001,
        "limit_type": "daily",
        "limit_count": 3,  # প্রতিদিন সর্বোচ্চ ৩ বার
        "action_type": "external_link",
        "link_url": "https://ouo.io/5D7GjQ",
        "wait_seconds": 15,
    },
    {
        "id": "website3",
        "title_key": "task_website3",
        "icon": "🌐",
        "icon_class": "web",
        "reward": 0.0007,
        "limit_type": "daily",
        "limit_count": 1,
        "action_type": "external_link",
        "link_url": "https://adurl.io/HDe4j",
        "wait_seconds": 15,
    },

    {
        "id": "hilltop_direct",
        "title_key": "task_click",
        "icon": "🖱️",
        "icon_class": "web",
        "reward": 0.0001,
        "limit_type": "daily",
        "limit_count": 10,
        "action_type": "external_link",
        "link_url": "https://affectionatestorage.com/MezkUF",
        "wait_seconds": 5,
    },

    {
        "id": "install_getblock",
        "title_key": "task_install_getblock",
        "desc": {
            "bn": "লিংকে ক্লিক করে অ্যাপ ইনস্টল করুন, Google দিয়ে সাইন আপ করে ২ মিনিট অ্যাপে থাকুন।",
            "en": "Tap the link to install the app, sign up with Google, and stay in the app for 2 minutes.",
            "ar": "انقر على الرابط لتثبيت التطبيق، وسجّل الدخول عبر Google، وابقَ داخل التطبيق لمدة دقيقتين.",
        },
        "icon": "📲",
        "icon_class": "ad",
        "reward": 0.055,
        "limit_type": "once",
        "limit_count": 1,
        "action_type": "external_link",
        "link_url": "https://getblock.me/u/30551780",
        "wait_seconds": 120,
    },

    {
        "id": "adsgram_view",
        "title_key": "task_ad",
        "desc": {
            "bn": "যেকোনো একটা এডে ক্লিক করুন, তারপর ৫ সেকেন্ড অপেক্ষা করুন।",
            "en": "Tap any ad, then wait 5 seconds.",
            "ar": "انقر على أي إعلان، ثم انتظر 5 ثوانٍ.",
        },
        "icon": "🎬",
        "icon_class": "ad",
        "reward": 0.0003,   # শুধু UI-তে দেখানোর জন্য - আসল reward config.py-এর
                            # ADSGRAM_REWARD_PER_VIEW দিয়ে দেওয়া হয় (postback থেকে), দুটো মান মিলিয়ে রাখুন
        "limit_type": "daily",
        "limit_count": 20,  # config.py-এর ADSGRAM_DAILY_LIMIT-এর সাথে মিলিয়ে রাখুন
        "action_type": "adsgram",
    },

    # ============================================================
    # নতুন টাস্ক যোগ করার উদাহরণ (এখন বন্ধ - "enabled": False):
    # নিচেরটা কপি করে enabled: True করে, তথ্য বদলে নতুন টাস্ক চালু করুন।
    # ============================================================
    # {
    #     "id": "survey1",
    #     "title_key": "task_survey",
    #     "icon": "📋",
    #     "icon_class": "web",
    #     "reward": 0.005,
    #     "limit_type": "once",
    #     "limit_count": 1,
    #     "action_type": "external_link",
    #     "link_url": "https://example-offer-link.com/xxxx",
    #     "wait_seconds": 20,
    #     "enabled": False,
    # },
]


def get_enabled_tasks():
    """শুধু চালু (enabled) থাকা টাস্কগুলো রিটার্ন করে।"""
    return [t for t in TASKS if t.get("enabled", True)]


def get_task_by_id(task_id):
    for t in TASKS:
        if t["id"] == task_id:
            return t
    return None

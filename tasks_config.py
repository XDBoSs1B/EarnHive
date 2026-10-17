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
#   "cpx_wall"      -> CPX Research সার্ভে অফারওয়াল - রিওয়ার্ড ভ্যারিয়েবল, postback থেকে অটো ক্রেডিট হয়
#   "manual_review" -> Telegram API দিয়ে স্বয়ংক্রিয়ভাবে যাচাই করা যায় না এমন টাস্ক - ইউজার
#                      স্ক্রিনশট জমা দেয়, অ্যাডমিন "Ck" চ্যানেলে Approve/Cancel করলে তখনই
#                      reward দেওয়া হয়। এই action_type-এ "instructions" (ধাপে ধাপে কী করতে
#                      হবে, bn/en/ar) আর "screenshot_labels" (প্রতিটা স্ক্রিনশট স্লটের
#                      নিজস্ব লেবেল, bn/en/ar) ফিল্ড দুটো লাগবে।

# ============================================================
# নতুন "website" টাইপ টাস্ক (CPX Research ও ভবিষ্যতে এই ধরনেরগুলো) সবসময় এই
# লিস্টের একদম শুরুতে বসবে। এর নিচের বিদ্যমান টাস্কগুলো (ad/website/website2/...)
# এর জায়গা/ক্রম অপরিবর্তিত থাকবে। ভবিষ্যতে যোগ হওয়া "অন্য ধরনের" নতুন টাস্ক
# (যেমন bot-start ভেরিফিকেশন টাস্কগুলো) লিস্টের একদম শেষে বসবে।
# ============================================================

TASKS = [
    {
        "id": "cpx_survey",
        "title_key": "task_cpx",
        "desc": {
            "bn": "সার্ভে পূরণ করুন, রিওয়ার্ড সার্ভে অনুযায়ী ভিন্ন হয় এবং স্বয়ংক্রিয়ভাবে যোগ হয়।",
            "en": "Complete surveys — reward varies per survey and is credited automatically.",
            "ar": "أكمل الاستبيانات - تختلف المكافأة حسب الاستبيان وتُضاف تلقائيًا.",
        },
        "icon": "📊",
        "icon_class": "web",
        "reward": 0,  # ভ্যারিয়েবল - আসল রিওয়ার্ড CPX-এর postback থেকে ঠিক হয় (UI-তে fixed amount দেখানো হয় না)
        "limit_type": "daily",
        "limit_count": 500,  # শুধু UI maxed আটকানোর জন্য একটা বড় সংখ্যা - আসল সার্ভে উপলব্ধতা CPX নিজেই নিয়ন্ত্রণ করে
        "action_type": "cpx_wall",
        # ব্যানার কার্ডে দেখানোর জন্য: logo = index.html-এর পাশে আপলোড করা লোগো ফাইলের নাম
        # (ফাইল না থাকলে বা লোড না হলে banner_name লেখা দেখায়), badge = উপরের ছোট ট্যাগ
        "logo": "cpx-logo.png",
        "banner_name": "CPX Research",
        "badge": "Hot",
    },

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
            "bn": "অ্যাপ ইনস্টল করে প্রোফাইল ও পয়েন্টের স্ক্রিনশট জমা দিন, অ্যাডমিন রিভিউ করে রিওয়ার্ড দিবে।",
            "en": "Install the app and submit screenshots of your profile and points — reward is credited after admin review.",
            "ar": "ثبّت التطبيق وأرسل لقطات شاشة لملفك الشخصي ونقاطك - تُضاف المكافأة بعد مراجعة المسؤول.",
        },
        "icon": "📲",
        "icon_class": "ad",
        "reward": 0.05,
        "limit_type": "once",
        "limit_count": 1,
        "action_type": "manual_review",
        "link_url": "https://getblock.me/u/30551780",
        # ম্যানুয়াল রিভিউ সিটে দেখানো পূর্ণ নির্দেশনা (ধাপে ধাপে কী করতে হবে)
        "instructions": {
            "bn": "১. নিচের লিংকে ক্লিক করে App টি ডাউনলোড করুন।\n📥 https://getblock.me/u/30551780\n\n⚠️ গুরুত্বপূর্ণ: কাজ সম্পন্ন করার পর কমপক্ষে ৪৮ ঘণ্টা App টি ডিলিট করবেন না। নির্ধারিত সময়ের আগে App ডিলিট করলে পেমেন্ট প্রযোজ্য নাও হতে পারে।\n\n2️⃣ Google দিয়ে অ্যাকাউন্ট খুলুন\nApp ওপেন করে Google ব্যবহার করে একটি অ্যাকাউন্ট তৈরি করুন।\n\n3️⃣ Profile সম্পূর্ণ করুন\nMy Profile অপশনে গিয়ে একটি Profile Picture এবং আপনার নাম যোগ করুন।\n\n4️⃣ Face Verification সম্পন্ন করুন\nনির্দেশনা অনুযায়ী Face Verification সম্পন্ন করুন।\n\n5️⃣ গেম খেলে 200+ পয়েন্ট করুন।",
            "en": "1. Tap the link below to download the App.\n📥 https://getblock.me/u/30551780\n\n⚠️ Important: Do not delete the App for at least 48 hours after completing the task. Deleting it earlier may make you ineligible for payment.\n\n2️⃣ Create an account with Google\nOpen the App and create an account using Google.\n\n3️⃣ Complete your Profile\nGo to My Profile and add a profile picture and your name.\n\n4️⃣ Complete Face Verification\nFollow the instructions to complete Face Verification.\n\n5️⃣ Play games and earn 200+ points.",
            "ar": "١. انقر على الرابط أدناه لتنزيل التطبيق.\n📥 https://getblock.me/u/30551780\n\n⚠️ هام: لا تحذف التطبيق لمدة 48 ساعة على الأقل بعد إكمال المهمة. قد يؤدي حذفه مبكرًا إلى عدم استحقاق الدفع.\n\n2️⃣ أنشئ حسابًا باستخدام Google\nافتح التطبيق وأنشئ حسابًا باستخدام Google.\n\n3️⃣ أكمل ملفك الشخصي\nاذهب إلى My Profile وأضف صورة شخصية واسمك.\n\n4️⃣ أكمل التحقق من الوجه\nاتبع التعليمات لإكمال Face Verification.\n\n5️⃣ العب الألعاب واحصل على 200+ نقطة.",
        },
        # প্রতিটা স্ক্রিনশট স্লটের নিজস্ব লেবেল (জেনেরিক "স্ক্রিনশট ১/২" না, নির্দিষ্ট করে বলা)
        "screenshot_labels": [
            {
                "bn": "আপনার প্রোফাইলের একটি ছবি দিন",
                "en": "Upload a photo of your profile",
                "ar": "ارفع صورة لملفك الشخصي",
            },
            {
                "bn": "200+ পয়েন্ট খেলেছেন সেটার একটা ছবি দিন",
                "en": "Upload a screenshot showing you've played 200+ points",
                "ar": "ارفع لقطة شاشة تُظهر حصولك على 200+ نقطة",
            },
        ],
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

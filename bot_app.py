"""
EarnHive - Telegram Bot (Mini App Launcher + Admin Panel)
এই বট এখন শুধু Mini App খোলার বাটন দেখায় এবং এডমিন উইথড্র অ্যাপ্রুভাল হ্যান্ডেল করে।
মূল লজিক (ব্যালেন্স, টাস্ক, রেফারেল) এখন server.py (Flask API) + Mini App-এ চলে যায়।
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

import config
import database as db

# ⚠️ এখানে আপনার হোস্ট করা Mini App-এর HTTPS লিংক বসান (Vercel/Railway ইত্যাদি থেকে পাওয়া)
MINI_APP_URL = "https://your-miniapp-url.vercel.app"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    start_param = args[0] if args else ""

    # রেফারেল প্যারামিটার সহ Mini App খোলার লিংক পাঠানো
    web_app_url = MINI_APP_URL
    if start_param:
        web_app_url = f"{MINI_APP_URL}?start_param={start_param}"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Open EarnHive", web_app=WebAppInfo(url=web_app_url))]
    ])
    await update.message.reply_text(
        "🎉 EarnHive-এ স্বাগতম!\n\nনিচের বাটনে ক্লিক করে অ্যাপ খুলুন এবং আয় শুরু করুন।",
        reply_markup=keyboard
    )


async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.ADMIN_IDS:
        return

    pending = db.get_pending_withdrawals()
    if not pending:
        await update.message.reply_text("কোনো পেন্ডিং উইথড্র নেই।")
        return

    for w in pending:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{w['id']}"),
             InlineKeyboardButton("❌ Reject", callback_data=f"reject_{w['id']}")]
        ])
        await update.message.reply_text(
            f"ID: {w['id']}\nUser: {w['user_id']}\nMethod: {w['method']}\n"
            f"Amount: ${w['amount_usd']:.4f}\nAccount: {w['account_info']}\n"
            f"Requested: {w['requested_at']}",
            reply_markup=keyboard
        )


async def admin_approve_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in config.ADMIN_IDS:
        return

    action, wid = query.data.split("_")
    wid = int(wid)
    withdrawal = db.get_withdrawal(wid)

    if not withdrawal or withdrawal["status"] != "pending":
        await query.edit_message_text("এই রিকোয়েস্টটি ইতিমধ্যে প্রসেস করা হয়েছে।")
        return

    if action == "approve":
        db.update_withdrawal_status(wid, "approved")
        await context.bot.send_message(
            withdrawal["user_id"],
            f"🎉 আপনার ${withdrawal['amount_usd']:.4f} উইথড্র সম্পন্ন হয়েছে!"
        )
        await query.edit_message_text(f"✅ Approved: ID {wid}")
    else:
        db.update_withdrawal_status(wid, "rejected")
        db.add_balance(withdrawal["user_id"], withdrawal["amount_usd"])
        await context.bot.send_message(
            withdrawal["user_id"],
            "❌ দুঃখিত, আপনার উইথড্র রিকোয়েস্ট বাতিল করা হয়েছে। ব্যালেন্স ফেরত দেওয়া হয়েছে।"
        )
        await query.edit_message_text(f"❌ Rejected: ID {wid}")


def main():
    db.init_db()
    app = Application.builder().token(config.BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pending", admin_pending))
    app.add_handler(CallbackQueryHandler(admin_approve_reject, pattern="^(approve|reject)_"))

    print("EarnHive Bot (Mini App launcher) is running...")
    app.run_polling()


if __name__ == "__main__":
    main()

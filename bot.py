import os
import re
import asyncio
import json
import logging
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ===============================
#          الإعدادات
# ===============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TIKTOK_URL = os.getenv("TIKTOK_URL", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ يجب ضبط TELEGRAM_TOKEN في لوحة Koyeb")

if not TIKTOK_URL:
    raise RuntimeError("❌ يجب ضبط TIKTOK_URL في لوحة Koyeb")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tiktok_live_bot")

bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

SUBS_FILE = Path("subscribers.json")
CHECK_INTERVAL = 30
last_live_state = None
last_room_id = None


# ===============================
#   إدارة المشتركين
# ===============================

def is_admin(uid):
    return uid == ADMIN_ID

def load_subs():
    if SUBS_FILE.exists():
        try:
            return set(json.loads(SUBS_FILE.read_text()))
        except:
            return set()
    return set()

def save_subs(subs):
    SUBS_FILE.write_text(json.dumps(list(subs)))

async def add_subscriber(chat_id):
    subs = load_subs()
    subs.add(chat_id)
    save_subs(subs)

async def remove_subscriber(chat_id):
    subs = load_subs()
    if chat_id in subs:
        subs.remove(chat_id)
        save_subs(subs)


# ===============================
#   قائمة الأوامر (أزرار)
# ===============================

def get_main_menu(is_admin_user=False):
    kb = InlineKeyboardMarkup()

    kb.add(InlineKeyboardButton("📢 تفعيل الإشعارات", callback_data="cmd_start"))
    kb.add(InlineKeyboardButton("❌ إيقاف الإشعارات", callback_data="cmd_stop"))
    kb.add(InlineKeyboardButton("🔎 حالة البث", callback_data="cmd_status"))

    if is_admin_user:
        kb.add(InlineKeyboardButton("👥 عدد المشتركين", callback_data="admin_users"))
        kb.add(InlineKeyboardButton("📨 إرسال للجميع", callback_data="admin_broadcast"))
        kb.add(InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats"))
        kb.add(InlineKeyboardButton("🔔 تنبيه تجريبي", callback_data="admin_test"))
        kb.add(InlineKeyboardButton("♻️ إعادة تشغيل", callback_data="admin_reboot"))

    return kb


# ===============================
#   جلب HTML واستخراج room_id
# ===============================

async def fetch_live_html():
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120 Safari/537.36"
        )
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(TIKTOK_URL, headers=headers) as resp:
            return await resp.text()


def extract_room_id_from_html(html: str):
    patterns = [
        r'"roomId"\s*:\s*"(\d+)"',
        r'"room_id"\s*:\s*"(\d+)"',
        r'roomId\s*[:=]\s*"(\d+)"'
    ]
    for p in patterns:
        m = re.search(p, html)
        if m:
            return m.group(1)
    return None


# ===============================
#   فحص حالة البث عبر Webcast API
# ===============================

async def check_live_pro():
    global last_room_id, last_live_state

    try:
        html = await fetch_live_html()
    except:
        return last_live_state if last_live_state is not None else False, last_room_id

    room_id = extract_room_id_from_html(html)
    if room_id:
        last_room_id = room_id
    else:
        room_id = last_room_id

    if not room_id:
        # fallback HTML
        if '"isLive":true' in html or "liveRoom" in html:
            return True, None
        return False, None

    api_url = f"https://webcast.tiktok.com/webcast/room/info/?aid=1988&room_id={room_id}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as resp:
                data = json.loads(await resp.text())
    except:
        if '"isLive":true' in html or "liveRoom" in html:
            return True, room_id
        return False, room_id

    room = data.get("data", {}).get("room_info", {})
    status = room.get("status")

    if status in (1, "1", "live"):
        return True, room_id
    if status in (0, "0", "ended"):
        return False, room_id

    if '"isLive":true' in html:
        return True, room_id
    return False, room_id


# ===============================
#        إرسال الإشعارات
# ===============================

async def notify_all(text, button=True):
    subs = load_subs()
    if not subs:
        return

    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("🎥 فتح البث الآن", url=TIKTOK_URL)
    ) if button else None

    for uid in subs:
        try:
            await bot.send_message(uid, text, reply_markup=kb)
        except:
            pass


async def notify_admin(text):
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID, f"👑 <b>للمدير:</b>\n{text}")
        except:
            pass


# ===============================
#         أوامر المستخدم
# ===============================

@dp.message_handler(commands=["start", "help"])
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    await add_subscriber(uid)

    txt = "🔥 <b>مرحباً بك! اختر ما تريد من القائمة:</b>"
    await message.answer(txt, reply_markup=get_main_menu(is_admin(uid)))


@dp.message_handler(commands=["stop"])
async def cmd_stop(message):
    await remove_subscriber(message.chat.id)
    await message.answer("❌ تم إيقاف الإشعارات.")


@dp.message_handler(commands=["status"])
async def cmd_status(message):
    live, room_id = await check_live_pro()
    extra = f"\n🆔 room_id: <code>{room_id}</code>" if room_id else ""

    if live:
        await message.answer(f"🔴 <b>البث شغال الآن</b>\n{TIKTOK_URL}{extra}")
    else:
        await message.answer(f"⚪ <b>لا يوجد بث الآن</b>\n{TIKTOK_URL}{extra}")


# ===============================
#         أوامر المدير
# ===============================

@dp.callback_query_handler()
async def callbacks(call: types.CallbackQuery):
    uid = call.from_user.id

    # ========================
    #   أوامر المستخدم
    # ========================

    if call.data == "cmd_start":
        await add_subscriber(uid)
        return await call.message.edit_text("📢 تم تفعيل الإشعارات.", reply_markup=get_main_menu(is_admin(uid)))

    if call.data == "cmd_stop":
        await remove_subscriber(uid)
        return await call.message.edit_text("❌ تم إيقاف الإشعارات.", reply_markup=get_main_menu(is_admin(uid)))

    if call.data == "cmd_status":
        live, room_id = await check_live_pro()
        extra = f"\n🆔 <code>{room_id}</code>" if room_id else ""
        msg = f"🔴 <b>البث شغال</b>\n{TIKTOK_URL}{extra}" if live else f"⚪ <b>لا يوجد بث</b>\n{TIKTOK_URL}{extra}"
        return await call.message.edit_text(msg, reply_markup=get_main_menu(is_admin(uid)))

    # ========================
    #   حماية المدير
    # ========================

    if not is_admin(uid):
        return await call.answer("❗ هذه القائمة للمدير فقط.", show_alert=True)

    # ========================
    #   أوامر المدير
    # ========================

    if call.data == "admin_users":
        subs = load_subs()
        return await call.message.edit_text(f"👥 عدد المشتركين: <b>{len(subs)}</b>", reply_markup=get_main_menu(True))

    if call.data == "admin_broadcast":
        return await call.message.edit_text("✍️ أرسل الرسالة التي تريد إرسالها للجميع.")

    if call.data == "admin_stats":
        subs = load_subs()
        msg = (
            "📊 <b>إحصائيات البوت</b>\n\n"
            f"👥 المشتركين: {len(subs)}\n"
            f"🔗 تيك توك: {TIKTOK_URL}\n"
            f"⏱ الفحص كل {CHECK_INTERVAL} ثانية"
        )
        return await call.message.edit_text(msg, reply_markup=get_main_menu(True))

    if call.data == "admin_test":
        return await call.message.edit_text("🔔 تنبيه تجريبي.", reply_markup=get_main_menu(True))

    if call.data == "admin_reboot":
        await call.message.edit_text("♻️ إعادة التشغيل…")
        os._exit(0)


# ===============================
#   راصد البث PRO
# ===============================

async def tiktok_watcher():
    global last_live_state

    await notify_admin("🚀 Bot PRO بدأ العمل")

    live, _ = await check_live_pro()
    last_live_state = live

    while True:
        try:
            live, room_id = await check_live_pro()

            if last_live_state is False and live is True:
                msg = (
                    "🔴 <b>تم بدء البث الآن!</b>\n"
                    f"🎥 {TIKTOK_URL}\n\n"
                    "📣 سارع بالدخول!"
                )
                await notify_all(msg)
                await notify_admin("🔴 بث جديد بدأ")

            elif last_live_state is True and live is False:
                msg = (
                    "⚪ <b>انتهى البث الآن.</b>\n"
                    f"🎥 {TIKTOK_URL}\n"
                    "📌 سيتم تنبيهك عند بث جديد."
                )
                await notify_all(msg, button=False)
                await notify_admin("⚪ البث انتهى")

            last_live_state = live

        except Exception as e:
            logger.error(f"❌ خطأ في الراصد: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


async def on_startup(dp):
    asyncio.create_task(tiktok_watcher())


# ===============================
#   تشغيل البوت
# ===============================

def main():
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)


if __name__ == "__main__":
    main()

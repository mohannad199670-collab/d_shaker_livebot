import os
import asyncio
import json
import logging
from pathlib import Path
import aiohttp
from aiogram import Bot, Dispatcher, executor, types

# ==========================
#       الإعدادات
# ==========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TIKTOK_URL = os.getenv("TIKTOK_URL", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))  # ← ضع ID المدير هنا داخل Koyeb

if not TELEGRAM_TOKEN:
    raise RuntimeError("يجب ضبط TELEGRAM_TOKEN في إعدادات Koyeb")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

SUBS_FILE = Path("subscribers.json")
CHECK_INTERVAL = 60
last_live_state = None


# ==========================
#      دوال مساعدة
# ==========================

def is_admin(user_id):
    return user_id == ADMIN_ID

def load_subs():
    if not SUBS_FILE.exists():
        return set()
    try:
        return set(json.loads(SUBS_FILE.read_text(encoding="utf-8")))
    except:
        return set()

def save_subs(s):
    SUBS_FILE.write_text(json.dumps(list(s)), encoding="utf-8")


async def check_live():
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession() as session:
            async with session.get(TIKTOK_URL, headers=headers) as resp:
                html = await resp.text()
                if '"is_live":true' in html or "webcast_url" in html:
                    return True
                return False
    except:
        return False


async def broadcast(msg):
    subs = load_subs()
    for cid in list(subs):
        try:
            await bot.send_message(cid, msg)
        except:
            pass


# ==========================
#       أوامر عامة
# ==========================

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    subs = load_subs()
    subs.add(message.chat.id)
    save_subs(subs)
    await message.answer("🎉 تم تفعيل إشعارات بث الدكتور شاكر.\nسأخبرك عند بداية ونهاية البث.")


@dp.message_handler(commands=["stop"])
async def stop(message: types.Message):
    subs = load_subs()
    if message.chat.id in subs:
        subs.remove(message.chat.id)
        save_subs(subs)
        await message.answer("❌ تم إلغاء الاشتراك في إشعارات البث.")
    else:
        await message.answer("أنت غير مشترك أصلاً.")


@dp.message_handler(commands=["الحالة"])
async def status_cmd(message: types.Message):
    live = await check_live()
    txt = "🔴 البث شغااال الآن!" if live else "⚪️ لا يوجد بث مباشر حالياً."
    await message.answer(txt)


# ==========================
#     أوامر المدير فقط
# ==========================

@dp.message_handler(commands=["مدير"])
async def admin_menu(message: types.Message):
    if not is_admin(message.from_user.id):
        return  # صامت
    text = (
        "🛡 <b>قائمة أوامر المدير</b>\n\n"
        "/مشتركين → عرض عدد المشتركين\n"
        "/ارسال → إرسال رسالة لكل المشتركين\n"
        "/احصائيات → إحصائيات كاملة\n"
        "/تنبيه → اختبار إشعار\n"
        "/اعادة → إعادة تشغيل البوت\n"
    )
    await message.answer(text)


@dp.message_handler(commands=["مشتركين"])
async def users_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    subs = load_subs()
    await message.answer(f"📊 عدد المشتركين: {len(subs)}")


@dp.message_handler(commands=["ارسال"])
async def sendall_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    args = message.text.replace("/ارسال", "").strip()
    if not args:
        return await message.answer("اكتب هكذا:\n\n/ارسال الرسالة التي تريد إرسالها")

    subs = load_subs()
    for cid in subs:
        try:
            await bot.send_message(cid, args)
        except:
            pass

    await message.answer("📢 تم إرسال الرسالة للجميع.")


@dp.message_handler(commands=["احصائيات"])
async def stats_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    subs = load_subs()
    await message.answer(
        f"📊 <b>إحصائيات النظام</b>\n\n"
        f"🔹 عدد المشتركين: {len(subs)}\n"
        f"🔹 رابط البث: {TIKTOK_URL}\n"
    )


@dp.message_handler(commands=["تنبيه"])
async def testalert(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🔔 هذا تنبيه تجريبي — يعمل بنجاح!")


@dp.message_handler(commands=["اعادة"])
async def reboot_cmd(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🔄 جاري إعادة تشغيل البوت...")
    os._exit(0)


# ==========================
#   مهمة مراقبة البث
# ==========================

async def watcher():
    global last_live_state
    last_live_state = await check_live()

    while True:
        live = await check_live()

        if live and not last_live_state:
            await broadcast("🔴 <b>بدأ الآن بث الدكتور شاكر!</b>")
        elif not live and last_live_state:
            await broadcast("⚪️ <b>انتهى الآن بث الدكتور شاكر.</b>")

        last_live_state = live
        await asyncio.sleep(CHECK_INTERVAL)


async def on_start(dp):
    asyncio.create_task(watcher())
    logger.info("البوت يعمل ✔️")


def main():
    executor.start_polling(dp, on_startup=on_start, skip_updates=True)


if __name__ == "__main__":
    main()

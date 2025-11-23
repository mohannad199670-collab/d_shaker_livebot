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
TIKTOK_URL = os.getenv("TIKTOK_URL", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # ضع ADMIN_ID من المتغيرات في Koyeb

if not TELEGRAM_TOKEN:
    raise RuntimeError("يجب ضبط TELEGRAM_TOKEN في إعدادات Koyeb")

if not TIKTOK_URL:
    raise RuntimeError("يجب ضبط TIKTOK_URL في إعدادات Koyeb")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

SUBS_FILE = Path("subscribers.json")
CHECK_INTERVAL = 60  # الفاصل بين كل فحص للبث (ثوانٍ)
last_live_state = None  # حالة البث السابقة


# ==========================
#   دوال المشتركين
# ==========================

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def load_subs() -> set:
    if not SUBS_FILE.exists():
        return set()
    try:
        data = json.loads(SUBS_FILE.read_text(encoding="utf-8"))
        return set(data)
    except Exception:
        return set()

def save_subs(subs: set):
    SUBS_FILE.write_text(json.dumps(list(subs), ensure_ascii=False), encoding="utf-8")


async def add_subscriber(chat_id: int):
    subs = load_subs()
    subs.add(chat_id)
    save_subs(subs)


async def remove_subscriber(chat_id: int):
    subs = load_subs()
    if chat_id in subs:
        subs.remove(chat_id)
        save_subs(subs)


# ==========================
#   فحص بث تيك توك
# ==========================

async def check_tiktok_live_flag() -> bool:
    """
    يحاول اكتشاف إذا كان الحساب لايف الآن من صفحة تيك توك.
    (هذه طريقة غير رسمية وقد تتغير لو تيك توك غيّر الكود)
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(TIKTOK_URL, headers=headers, timeout=15) as resp:
                html = await resp.text()

        # كلمات تدل أن الحساب في بث مباشر
        live_keywords = [
            '"is_live":true',
            '"isLive":true',
            '"LIVE_NOW"',
            'webcast_url',
            '"liveRoomId"',
        ]
        for kw in live_keywords:
            if kw in html:
                return True

        return False
    except Exception as e:
        logger.error(f"خطأ أثناء فحص تيك توك: {e}")
        # عند الخطأ نرجّع False حتى لا نرسل تنبيهات وهمية
        return False


# ==========================
#   إرسال الإشعارات
# ==========================

async def broadcast_message(text: str):
    """
    يرسل رسالة نصية فقط (إشعار عادي من تيليجرام)
    """
    subs = load_subs()
    if not subs:
        logger.info("لا يوجد مشتركين لإرسال الإشعار.")
        return

    logger.info(f"إرسال الإشعار إلى {len(subs)} مشترك.")
    for chat_id in list(subs):
        try:
            await bot.send_message(chat_id, text, disable_web_page_preview=True)
            await asyncio.sleep(0.05)  # لتخفيف الضغط
        except Exception as e:
            logger.error(f"خطأ أثناء إرسال الرسالة إلى {chat_id}: {e}")


# ==========================
#   أوامر عامة للمستخدمين
# ==========================

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    await add_subscriber(user_id)

    welcome = (
        "🎉 <b>تم تفعيل إشعارات بث الدكتور شاكر توفيق العاروري.</b>\n\n"
        "📢 سيتم تنبيهك تلقائيًا عند <b>بداية البث</b> وعند <b>انتهائه</b> على تيك توك.\n\n"
        "الأوامر المتاحة:\n"
        "▫️ /start — الاشتراك/إعادة التفعيل\n"
        "▫️ /stop — إيقاف الإشعارات\n"
        "▫️ /status — معرفة حالة البث الآن\n"
    )

    if is_admin(user_id):
        welcome += "\n👑 <i>ملاحظة: أنت مُدرِس البوت (مدير)، أوامر الإدارة مفعلة لك فقط.</i>"

    await message.answer(welcome)


@dp.message_handler(commands=["stop"])
async def cmd_stop(message: types.Message):
    await remove_subscriber(message.chat.id)
    await message.answer("❌ تم إيقاف إرسال إشعارات البث لهذا الحساب.")


@dp.message_handler(commands=["status", "الحالة"])
async def cmd_status(message: types.Message):
    live = await check_tiktok_live_flag()
    if live:
        txt = "🔴 <b>حاليًا الدكتور على بث مباشر في تيك توك.</b>\n"
    else:
        txt = "⚪️ <b>حاليًا لا يوجد بث مباشر للدكتور على تيك توك.</b>\n"

    txt += f"\nرابط الحساب:\n{TIKTOK_URL}"
    await message.answer(txt)


# ==========================
#   أوامر المدير (بالعربي – صامتة لغيره)
# ==========================

@dp.message_handler(commands=["مدير"])
async def cmd_admin_menu(message: types.Message):
    if not is_admin(message.from_user.id):
        return  # صامت لغير المدير

    text = (
        "🛡 <b>قائمة أوامر المدير</b>\n\n"
        "/مشتركين — عرض عدد المشتركين\n"
        "/ارسال — إرسال رسالة لكل المشتركين\n"
        "/احصائيات — إحصائيات عامة\n"
        "/تنبيه — تنبيه تجريبي لنفسك\n"
        "/اعادة — إعادة تشغيل البوت\n"
    )
    await message.answer(text)


@dp.message_handler(commands=["مشتركين"])
async def cmd_users(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    subs = load_subs()
    await message.answer(f"📊 عدد المشتركين الحاليين في إشعارات البث: <b>{len(subs)}</b>")


@dp.message_handler(commands=["ارسال"])
async def cmd_sendall(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    # الرسالة بعد الأمر
    content = message.text.replace("/ارسال", "", 1).strip()
    if not content:
        await message.answer("اكتب هكذا:\n\n<code>/ارسال نص الرسالة هنا</code>", parse_mode="HTML")
        return

    subs = load_subs()
    count = 0
    for chat_id in list(subs):
        try:
            await bot.send_message(chat_id, content)
            count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"خطأ أثناء الإرسال إلى {chat_id}: {e}")

    await message.answer(f"📢 تم إرسال الرسالة إلى <b>{count}</b> مشترك.")


@dp.message_handler(commands=["احصائيات"])
async def cmd_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    subs = load_subs()
    txt = (
        "📊 <b>إحصائيات البوت</b>\n\n"
        f"👥 عدد المشتركين: <b>{len(subs)}</b>\n"
        f"🔗 رابط الحساب في تيك توك:\n{TIKTOK_URL}\n"
        f"⏱ فترة فحص البث: كل {CHECK_INTERVAL} ثانية\n"
    )
    await message.answer(txt)


@dp.message_handler(commands=["تنبيه"])
async def cmd_test_alert(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("🔔 هذا تنبيه تجريبي، إن وصلك كإشعار فكل شيء يعمل بشكل صحيح.")


@dp.message_handler(commands=["اعادة"])
async def cmd_reboot(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("♻️ جاري إعادة تشغيل البوت...")
    # إنهاء العملية، كوييب يعيد تشغيلها تلقائيًا
    os._exit(0)


# ==========================
#   مراقبة البث في الخلفية
# ==========================

async def tiktok_watcher():
    global last_live_state
    logger.info("بدء مهمة مراقبة بث تيك توك...")
    last_live_state = await check_tiktok_live_flag()

    while True:
        try:
            live = await check_tiktok_live_flag()

            # أول مرة لا نرسل إشعار، فقط نخزن الحالة
            if last_live_state is None:
                last_live_state = live

            # من غير لايف → لايف (بدأ البث)
            elif last_live_state is False and live is True:
                logger.info("تم اكتشاف بداية البث 🔴")
                await broadcast_message(
                    "🔴 <b>بدأ الآن بث مباشر للدكتور شاكر توفيق العاروري على تيك توك!</b>\n\n"
                    f"رابط الحساب:\n{TIKTOK_URL}"
                )
                last_live_state = True

            # من لايف → غير لايف (انتهى البث)
            elif last_live_state is True and live is False:
                logger.info("تم اكتشاف انتهاء البث ⚪️")
                await broadcast_message(
                    "⚪️ <b>انتهى الآن البث المباشر للدكتور شاكر توفيق العاروري.</b>\n\n"
                    f"رابط الحساب:\n{TIKTOK_URL}"
                )
                last_live_state = False

            else:
                last_live_state = live

        except Exception as e:
            logger.error(f"خطأ في مهمة مراقبة البث: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


async def on_startup(dp: Dispatcher):
    asyncio.create_task(tiktok_watcher())
    logger.info("البوت بدأ العمل ✅")


def main():
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)


if __name__ == "__main__":
    main()

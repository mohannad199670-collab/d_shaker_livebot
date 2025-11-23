import asyncio
import json
import logging
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher, executor, types
from aiogram.utils.exceptions import BotBlocked, ChatNotFound

import os

# ========= الإعدادات من المتغيرات البيئية =========
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TIKTOK_URL = os.getenv("TIKTOK_URL", "").strip()

if not TELEGRAM_TOKEN:
    raise RuntimeError("يجب ضبط متغير البيئة TELEGRAM_TOKEN في لوحة Koyeb")

if not TIKTOK_URL:
    raise RuntimeError("يجب ضبط متغير البيئة TIKTOK_URL في لوحة Koyeb")

# ========= إعدادات عامة =========
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

SUBS_FILE = Path("subscribers.json")
CHECK_INTERVAL = 60  # عدد الثواني بين كل فحص وفحص للبث
tiktok_live_prev_state = None  # حالة البث السابقة (None / True / False)


# ========= دوال مساعدة لحفظ المشتركين =========
def load_subscribers() -> set:
    if not SUBS_FILE.exists():
        return set()
    try:
        data = json.loads(SUBS_FILE.read_text(encoding="utf-8"))
        return set(data)
    except Exception:
        return set()


def save_subscribers(subs: set):
    SUBS_FILE.write_text(json.dumps(list(subs)), encoding="utf-8")


async def add_subscriber(chat_id: int):
    subs = load_subscribers()
    subs.add(chat_id)
    save_subscribers(subs)


async def remove_subscriber(chat_id: int):
    subs = load_subscribers()
    if chat_id in subs:
        subs.remove(chat_id)
        save_subscribers(subs)


# ========= أوامر البوت =========
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    await add_subscriber(message.chat.id)
    text = (
        "أهلاً بك في بوت إشعارات بث الدكتور شاكر العاروري ❤️\n\n"
        "📢 سأقوم بتنبيهك تلقائيًا عندما يبدأ البث على تيك توك، "
        "وإخبارك عند انتهائه أيضًا.\n\n"
        "الأوامر المتاحة:\n"
        "▫️ /start  ➜ الاشتراك في الإشعارات\n"
        "▫️ /stop   ➜ إيقاف الإشعارات\n"
        "▫️ /status ➜ معرفة حالة البث الآن\n"
    )
    await message.answer(text)


@dp.message_handler(commands=["stop"])
async def cmd_stop(message: types.Message):
    await remove_subscriber(message.chat.id)
    await message.answer("✅ تم إيقاف إرسال إشعارات البث لهذا الحساب.")


@dp.message_handler(commands=["status"])
async def cmd_status(message: types.Message):
    is_live = await check_tiktok_live()
    if is_live:
        txt = "🔴 الدكتور شاكر <b>حالياً على البث المباشر</b> على تيك توك.\n"
    else:
        txt = "⚪️ حالياً <b>لا يوجد بث مباشر</b> للدكتور شاكر على تيك توك.\n"

    txt += f"\nرابط الحساب:\n{TIKTOK_URL}"
    await message.answer(txt)


# ========= فحص حالة البث في تيك توك =========
async def fetch_tiktok_page(session: aiohttp.ClientSession) -> str:
    headers = {
        # تظبيط بسيط للهيدر حتى لا يمنعنا تيك توك
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    }
    async with session.get(TIKTOK_URL, headers=headers) as resp:
        resp.raise_for_status()
        return await resp.text()


async def check_tiktok_live() -> bool:
    """
    يحاول تخمين إن كان الحساب لايف من خلال محتوى الصفحة.
    هذه الطريقة ليست رسمية وقد تتوقف إذا غيّر تيك توك الكود الداخلي.
    """
    try:
        async with aiohttp.ClientSession() as session:
            html = await fetch_tiktok_page(session)

            keywords_live = [
                '"is_live":true',
                '"LIVE_NOW"',
                "webcast_url",
                "LIVE_NOW_BADGE",
            ]

            for kw in keywords_live:
                if kw in html:
                    return True

            return False
    except Exception as e:
        logger.error(f"خطأ أثناء فحص تيك توك: {e}")
        # في حالة الخطأ نرجع False حتى لا نرسل إنذارات وهمية
        return False


# ========= إرسال الإشعارات للمشتركين =========
async def broadcast_message(text: str):
    subs = load_subscribers()
    if not subs:
        logger.info("لا يوجد مشتركين لإرسال الإشعار.")
        return

    logger.info(f"إرسال الإشعار إلى {len(subs)} مشترك.")
    for chat_id in list(subs):
        try:
            await bot.send_message(chat_id, text, disable_web_page_preview=True)
            await asyncio.sleep(0.05)  # تخفيف الضغط على التليجرام
        except (BotBlocked, ChatNotFound):
            # نحذف المشتركين الذين حظروا البوت أو لم يعودوا متاحين
            await remove_subscriber(chat_id)
        except Exception as e:
            logger.error(f"خطأ أثناء إرسال الرسالة إلى {chat_id}: {e}")


# ========= مهمة خلفية لمراقبة تيك توك =========
async def tiktok_watcher():
    global tiktok_live_prev_state

    logger.info("بدء مهمة مراقبة بث تيك توك...")
    # أول فحص
    tiktok_live_prev_state = await check_tiktok_live()

    while True:
        try:
            is_live_now = await check_tiktok_live()

            # أول مرة فقط لا نرسل إشعار، فقط نخزن الحالة
            if tiktok_live_prev_state is None:
                tiktok_live_prev_state = is_live_now

            # من غير لايف --> لايف  (بدأ البث)
            elif tiktok_live_prev_state is False and is_live_now is True:
                logger.info("تم اكتشاف بدء البث 🚨")
                await broadcast_message(
                    "🔴 <b>بدأ الآن بث الدكتور شاكر توفيق العاروري على تيك توك!</b>\n\n"
                    f"رابط البث / الحساب:\n{TIKTOK_URL}"
                )
                tiktok_live_prev_state = True

            # من لايف --> غير لايف (انتهى البث)
            elif tiktok_live_prev_state is True and is_live_now is False:
                logger.info("تم اكتشاف انتهاء البث ⚪️")
                await broadcast_message(
                    "⚪️ <b>انتهى الآن بث الدكتور شاكر توفيق العاروري على تيك توك.</b>\n\n"
                    f"رابط الحساب:\n{TIKTOK_URL}"
                )
                tiktok_live_prev_state = False

            # لا تغيير بالحالة
            else:
                tiktok_live_prev_state = is_live_now

        except Exception as e:
            logger.error(f"خطأ في مهمة المراقبة: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


# ========= تشغيل البوت =========
async def on_startup(dp: Dispatcher):
    # تشغيل مهمة مراقبة تيك توك في الخلفية
    asyncio.create_task(tiktok_watcher())
    logger.info("البوت بدأ العمل ✅")


def main():
    executor.start_polling(dp, on_startup=on_startup, skip_updates=True)


if __name__ == "__main__":
    main()

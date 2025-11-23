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
CHECK_INTERVAL = 30  # فحص كل 30 ثانية
last_live_state = None  # None أول مرة، ثم True/False
last_room_id = None     # لحفظ آخر room_id معروف


# ===============================
#   إدارة المشتركين
# ===============================

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
    SUBS_FILE.write_text(json.dumps(list(subs)), encoding="utf-8")

async def add_subscriber(chat_id: int):
    subs = load_subs()
    subs.add(chat_id)
    save_subs(subs)

async def remove_subscriber(chat_id: int):
    subs = load_subs()
    if chat_id in subs:
        subs.remove(chat_id)
        save_subs(subs)


# ===============================
#   جلب HTML واستخراج room_id
# ===============================

async def fetch_live_html() -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Referer": "https://www.google.com"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(TIKTOK_URL, headers=headers, timeout=20) as resp:
            return await resp.text()

def extract_room_id_from_html(html: str) -> str | None:
    """
    نحاول استخراج room_id من الـ HTML بعدّة طرق.
    """
    # 1) "roomId":"123456789"
    m = re.search(r'"roomId"\s*:\s*"(\d+)"', html)
    if m:
        return m.group(1)

    # 2) "room_id":"123456789"
    m = re.search(r'"room_id"\s*:\s*"(\d+)"', html)
    if m:
        return m.group(1)

    # 3) roomId: "123456789"
    m = re.search(r'roomId\s*[:=]\s*"(\d+)"', html)
    if m:
        return m.group(1)

    return None


# ===============================
#   فحص حالة البث عبر Webcast API
# ===============================

async def check_live_pro() -> tuple[bool, str | None]:
    """
    تعيد:
      (is_live, room_id)
    - is_live: True إذا البث شغّال، False إذا لا.
    - room_id: آخر room_id مُكتشف (أو None).
    """

    global last_room_id

    try:
        html = await fetch_live_html()
    except Exception as e:
        logger.error(f"❌ خطأ في جلب HTML من تيك توك: {e}")
        # في حالة الخطأ، نُبقي الحالة كما هي ولا نغيرها
        return (last_live_state if last_live_state is not None else False, last_room_id)

    # نحاول استخراج room_id من الـ HTML
    room_id = extract_room_id_from_html(html)
    if room_id:
        last_room_id = room_id
    else:
        room_id = last_room_id  # استخدم آخر room_id معروف إن وجد

    # لو لم نجد room_id إطلاقًا نستعمل فحص HTML فقط (تقريب)
    if not room_id:
        logger.info("⚠ لم يتم العثور على room_id صريح، استخدام فحص HTML تقريبي…")
        live_keywords = [
            '"isLive":true',
            '"is_live":true',
            '"status":1',
            '"liveRoom"',
            '"webcast"',
        ]
        for kw in live_keywords:
            if kw in html:
                return True, None
        return False, None

    # إن وُجد room_id نستخدم Webcast API
    api_url = f"https://webcast.tiktok.com/webcast/room/info/?aid=1988&room_id={room_id}"

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            ),
            "Referer": TIKTOK_URL,
            "Accept": "application/json, text/plain, */*",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=headers, timeout=20) as resp:
                text = await resp.text()
                try:
                    data = json.loads(text)
                except Exception:
                    logger.warning("⚠ فشل في قراءة JSON من Webcast، العودة لفحص HTML فقط.")
                    # fallback: فقط كلمات HTML
                    live_keywords = ['"isLive":true', '"is_live":true', 'liveRoom']
                    for kw in live_keywords:
                        if kw in html:
                            return True, room_id
                    return False, room_id

        # نحاول قراءة حالة البث من JSON
        # ملاحظة: هذا تخمين مبني على أشكال شائعة للـ API، قد تحتاج تعديل حسب استجابة تيك توك الفعلية
        is_live = False

        # بعض الاستجابات قد تحوي data->room_info->status
        try:
            room_info = (
                data.get("data", {})
                .get("room_info", {})
            )
            status = room_info.get("status", None)
            # في بعض النسخ: 1 = حي (لايف), 0 = مغلق
            if status in (1, "1", "live", "running"):
                is_live = True
            elif status in (0, "0", "ended", "stop"):
                is_live = False
        except Exception:
            pass

        # لو ما قدرنا نحدد من status، نرجع نستخدم HTML كمرجع إضافي
        if not is_live:
            live_keywords = ['"isLive":true', '"is_live":true', 'liveRoom']
            for kw in live_keywords:
                if kw in html:
                    is_live = True
                    break

        return is_live, room_id

    except Exception as e:
        logger.error(f"❌ خطأ أثناء فحص Webcast API: {e}")
        # fallback: فحص HTML فقط
        live_keywords = ['"isLive":true', '"is_live":true', 'liveRoom']
        for kw in live_keywords:
            if kw in html:
                return True, room_id
        return False, room_id


# ===============================
#        إرسال الإشعارات
# ===============================

async def notify_all(message: str, with_button: bool = True):
    subs = load_subs()
    if not subs:
        logger.info("لا يوجد مشتركون لإرسال الإشعارات إليهم.")
        return

    if with_button:
        keyboard = InlineKeyboardMarkup().add(
            InlineKeyboardButton("🎥 فتح البث الآن", url=TIKTOK_URL)
        )
    else:
        keyboard = None

    for chat_id in subs:
        try:
            await bot.send_message(
                chat_id,
                message,
                disable_web_page_preview=True,
                reply_markup=keyboard
            )
            await asyncio.sleep(0.03)
        except Exception as e:
            logger.error(f"خطأ أثناء إرسال الإشعار إلى {chat_id}: {e}")


async def notify_admin(text: str):
    """إرسال رسالة خاصة للمدير (لوج داخلي)."""
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID, f"👑 <b>للمدير:</b>\n{text}")
        except Exception:
            pass


# ===============================
#         أوامر المستخدم
# ===============================

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    await add_subscriber(uid)

    txt = (
        "🎉 <b>تم تفعيل إشعارات بث تيك توك.</b>\n\n"
        "🔥 سيتم تنبيهك تلقائيًا عند <b>بدء البث</b> و <b>انتهائه</b>.\n\n"
        "الأوامر:\n"
        "/start — تفعيل/إعادة التفعيل\n"
        "/stop — إيقاف الإشعارات\n"
        "/status — معرفة حالة البث الآن\n"
    )

    if is_admin(uid):
        txt += "\n👑 <i>أنت مدير البوت — أوامر الإدارة متاحة لك.</i>"

    await message.answer(txt)


@dp.message_handler(commands=["stop"])
async def cmd_stop(message: types.Message):
    await remove_subscriber(message.chat.id)
    await message.answer("❌ تم إيقاف إشعارات البث لهذا الحساب.")


@dp.message_handler(commands=["status"])
async def cmd_status(message: types.Message):
    live, room_id = await check_live_pro()
    extra = ""
    if room_id:
        extra = f"\n\n🆔 room_id المكتشف: <code>{room_id}</code>"

    if live:
        await message.answer(
            f"🔴 <b>البث شغّال الآن!</b>\n\n"
            f"🎥 رابط البث:\n{TIKTOK_URL}"
            f"{extra}"
        )
    else:
        await message.answer(
            f"⚪ <b>لا يوجد بث مباشر حاليًا.</b>\n\n"
            f"📌 رابط الحساب/البث:\n{TIKTOK_URL}"
            f"{extra}"
        )


# ===============================
#         أوامر المدير
# ===============================

@dp.message_handler(commands=["مدير"])
async def admin_menu(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    text = (
        "🛡 <b>أوامر المدير:</b>\n\n"
        "/مشتركين — عدد المشتركين\n"
        "/ارسال — إرسال رسالة للجميع\n"
        "/احصائيات — معلومات عامة\n"
        "/تنبيه — تنبيه تجريبي لنفسك\n"
        "/اعادة — إعادة تشغيل البوت\n"
    )
    await message.answer(text)


@dp.message_handler(commands=["مشتركين"])
async def cmd_users(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    subs = load_subs()
    await message.answer(f"👥 عدد المشتركين: <b>{len(subs)}</b>")


@dp.message_handler(commands=["ارسال"])
async def cmd_sendall(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    content = message.text.replace("/ارسال", "", 1).strip()
    if not content:
        return await message.answer("❗ اكتب هكذا:\n<code>/ارسال نص الرسالة هنا</code>")

    await notify_all(content, with_button=False)
    await message.answer("📢 تم إرسال الرسالة لكل المشتركين.")


@dp.message_handler(commands=["احصائيات"])
async def cmd_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    subs = load_subs()
    txt = (
        "📊 <b>إحصائيات البوت</b>\n\n"
        f"👥 عدد المشتركين: <b>{len(subs)}</b>\n"
        f"🔗 رابط تيك توك المستخدم:\n{TIKTOK_URL}\n"
        f"⏱ فترة الفحص: كل {CHECK_INTERVAL} ثانية\n"
    )
    await message.answer(txt)


@dp.message_handler(commands=["تنبيه"])
async def cmd_test(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    keyboard = InlineKeyboardMarkup().add(
        InlineKeyboardButton("🎥 فتح البث (اختبار)", url=TIKTOK_URL)
    )
    await message.answer("🔔 هذا تنبيه تجريبي — إن وصلك كإشعار فكل شيء تمام ✔", reply_markup=keyboard)


@dp.message_handler(commands=["اعادة"])
async def cmd_reboot(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("♻️ سيتم إعادة تشغيل البوت الآن…")
    await notify_admin("♻️ تم طلب إعادة تشغيل البوت من المدير.")
    os._exit(0)


# ===============================
#      راصد البث PRO (تلقائي)
# ===============================

async def tiktok_watcher():
    global last_live_state

    await notify_admin("🚀 تم تشغيل بوت مراقبة البث (نظام PRO).")
    # أول قراءة للحالة
    is_live, _ = await check_live_pro()
    last_live_state = is_live

    while True:
        try:
            is_live, room_id = await check_live_pro()

            # انتقال من غير لايف → لايف
            if last_live_state is False and is_live is True:
                msg = (
                    "🔴 <b>تم بدء البث الآن!</b>\n\n"
                    f"🎥 رابط البث:\n{TIKTOK_URL}\n\n"
                    "📣 سارع بالدخول قبل أن يفوتك البث!"
                )
                await notify_all(msg, with_button=True)
                await notify_admin("🔴 تم اكتشاف بدء بث جديد وإرسال تنبيهات للمشتركين.")

            # انتقال من لايف → غير لايف
            elif last_live_state is True and is_live is False:
                msg = (
                    "⚪ <b>انتهى البث الآن.</b>\n\n"
                    f"🎥 كان البث على:\n{TIKTOK_URL}\n\n"
                    "📌 سيتم تنبيهك عند بدء بث جديد بإذن الله."
                )
                await notify_all(msg, with_button=False)
                await notify_admin("⚪ تم اكتشاف انتهاء البث وإرسال تنبيه للمشتركين.")

            last_live_state = is_live

        except Exception as e:
            logger.error(f"❌ خطأ في tiktok_watcher: {e}")

        await asyncio.sleep(CHECK_INTERVAL)


async def on_startup(dp: Dispatcher):
    asyncio.create_task(tiktok_watcher())
    logger.info("✅ البوت بدأ العمل (نظام PRO مفعّل).")


# ===============================
#         تشغيل البوت
# ===============================

def main():
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)


if __name__ == "__main__":
    main()

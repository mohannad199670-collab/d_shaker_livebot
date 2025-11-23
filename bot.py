import os
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

BOT_TOKEN = os.getenv("BOT_TOKEN")
TIKTOK_URL = os.getenv("TIKTOK_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    await message.answer("أهلاً بك في بوت إشعارات بث الدكتور شاكر العاروري ❤️")

@dp.message_handler(commands=['live'])
async def live(message: types.Message):
    await message.answer(f"رابط صفحة البث:\n{TIKTOK_URL}")

async def on_startup(_):
    print("BOT IS RUNNING...")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)

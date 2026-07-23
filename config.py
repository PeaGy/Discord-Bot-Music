import os

from dotenv import load_dotenv


load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "Thiếu DISCORD_TOKEN trong file .env hoặc biến môi trường hệ thống."
    )

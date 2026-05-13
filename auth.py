from telethon.sync import TelegramClient
import os
from dotenv import load_dotenv

load_dotenv()

client = TelegramClient(
    'session',
    int(os.getenv('API_ID')),
    os.getenv('API_HASH')
)

with client:
    print("✅ Авторизован! session.session создан.")
    print(f"Я: {client.get_me().first_name}")
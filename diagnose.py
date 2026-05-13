import asyncio
import os
from telethon import TelegramClient
from dotenv import load_dotenv

load_dotenv()

api_id = int(os.getenv('API_ID'))
api_hash = os.getenv('API_HASH')
phone_number = os.getenv('PHONE_NUMBER')


async def main():
    client = TelegramClient('session_test', api_id, api_hash)

    try:
        print('🔍 Диагностика подключения...\n')

        # Шаг 1: Подключаемся
        print('1️⃣  Попытка подключиться к Telegram...')
        await client.connect()
        print('✅ Подключение успешно\n')

        # Шаг 2: Проверяем авторизацию
        print('2️⃣  Проверка авторизации...')
        is_auth = await client.is_user_authorized()
        print(f'   Авторизован: {is_auth}\n')

        if not is_auth:
            print('3️⃣  Отправляем запрос на код...')
            print(f'   Номер: {phone_number}')
            print(f'   API ID: {api_id}')

            try:
                result = await client.send_code_request(phone_number)
                print(f'✅ Запрос отправлен успешно!')
                print(f'   Результат: {result}')
                print(f'   Тип: {type(result)}\n')

                # Выводим все атрибуты
                print('📋 Информация из result:')
                for attr in dir(result):
                    if not attr.startswith('_'):
                        try:
                            value = getattr(result, attr)
                            if not callable(value):
                                print(f'   {attr}: {value}')
                        except:
                            pass

                print('\n⏳ Код должен прийти в течение 1-2 минут на:')
                print(f'   📱 SMS на номер {phone_number}')
                print(f'   📧 Email')
                print(f'   💬 Telegram приложение')
                print('\n⚠️  Если код не пришёл, попробуйте:')
                print('   1. Проверить спам в Email')
                print('   2. Открыть Telegram приложение')
                print('   3. Убедиться что номер правильный')
                print('   4. Подождать 5 минут и повторить')

            except Exception as send_error:
                print(f'❌ Ошибка при отправке запроса: {send_error}')
                print(f'   Тип ошибки: {type(send_error).__name__}')
                print(f'\n⚠️  Возможные причины:')
                print('   1. Неверный API_ID или API_HASH')
                print('   2. Неверный номер телефона')
                print('   3. Аккаунт заблокирован')
                print('   4. Слишком частые попытки (flood wait)')
                import traceback
                traceback.print_exc()
        else:
            print('✅ Вы уже авторизованы!')
            me = await client.get_me()
            print(f'   Пользователь: {me.first_name}')

    except Exception as e:
        print(f'❌ Ошибка подключения: {e}')
        print(f'   Тип: {type(e).__name__}')
        import traceback
        traceback.print_exc()

    finally:
        await client.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
# QR Warehouse Notes - Telegram Bot

Flask веб-сервер для Telegram бота для управления заметками с QR-кодами.

## Установка

1. Установите зависимости:
```bash
pip install -r requirements.txt
```

2. Создайте файл `.env` на основе `.env.example`:
```bash
cp .env.example .env
```

3. Заполните переменные окружения в `.env`:
- `TOKEN` - токен вашего Telegram бота (получите у @BotFather)
- `USER_ID` - ваш Telegram User ID (можно узнать у @userinfobot)
- `SECRET_KEY` - секретный ключ для Flask (любая случайная строка)

## Запуск

```bash
python app.py
```

Сервер запустится на `http://localhost:5000`

## Настройка Webhook

После запуска сервера, настройте webhook для Telegram бота:

```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=https://your-domain.com/webhook/<YOUR_BOT_TOKEN>"
```

Или используйте polling (нужно изменить код для использования polling вместо webhook).

## Команды бота

- `/start` - приветствие
- `/qr <текст или ссылка>` - генерирует QR-код с текстом/ссылкой
- `/note` - управление заметками (создание, просмотр списка)
- `/view <id>` - просмотр конкретной заметки по ID

## Структура проекта

```
.
├── app.py              # Основной файл приложения
├── requirements.txt    # Зависимости Python
├── .env.example        # Пример файла с переменными окружения
├── README.md          # Документация
├── uploads/           # Папка для загруженных фото (создается автоматически)
└── qr_warehouse.db    # SQLite база данных (создается автоматически)
```

## API Endpoints

- `GET /` - статус сервиса
- `POST /webhook/<token>` - webhook для Telegram Bot API
- `GET /qr?data=<текст>` - генерация QR-кода
- `GET /note/<id>` - просмотр заметки через веб-интерфейс
- `GET /uploads/<filename>` - получение загруженных файлов

## Особенности

- SQLite база данных для хранения заметок
- Загрузка до 5 фото на заметку
- Генерация QR-кодов в формате PNG
- Защита доступа по USER_ID
- Безопасное хранение загруженных файлов





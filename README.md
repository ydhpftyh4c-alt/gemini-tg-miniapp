# 🤖 Gemini Telegram Mini App

Телеграм-бот с Mini App интерфейсом для общения с Google Gemini AI.

## Что внутри

```
gemini-tg-miniapp/
├── main.py           # Сервер (FastAPI + Telegram бот + Gemini API)
├── static/
│   └── index.html    # Mini App — красивый чат-интерфейс
├── .env.example      # Шаблон настроек
├── requirements.txt  # Зависимости Python
├── start.bat         # Запуск одной кнопкой (Windows)
└── README.md         # Эта инструкция
```

## Быстрый старт (5 минут)

### Шаг 1. Получи ключи

| Что | Где взять |
|-----|-----------|
| **Gemini API Key** | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) → Create API Key |
| **Telegram Bot Token** | [@BotFather](https://t.me/BotFather) → /newbot → скопируй токен |

### Шаг 2. Настрой `.env`

```bash
copy .env.example .env
```

Открой `.env` и вставь свои ключи:

```env
GEMINI_API_KEY=AIzaSy...ваш_ключ
BOT_TOKEN=7123456789:AAH...ваш_токен
WEBAPP_URL=https://xxxx.ngrok-free.app
GEMINI_MODEL=gemini-2.5-flash
```

### Шаг 3. Установи ngrok (для HTTPS)

Telegram Mini App требует HTTPS. Ngrok даёт бесплатный HTTPS-туннель:

1. Скачай: [ngrok.com/download](https://ngrok.com/download)
2. Зарегистрируйся (бесплатно) и добавь authtoken:
   ```
   ngrok config add-authtoken YOUR_TOKEN
   ```
3. Запусти туннель:
   ```
   ngrok http 8080
   ```
4. Скопируй HTTPS-адрес (например `https://abc123.ngrok-free.app`) в `.env` → `WEBAPP_URL`

### Шаг 4. Запусти

Двойной клик на `start.bat` или в терминале:

```bash
pip install -r requirements.txt
python main.py
```

### Шаг 5. Пользуйся!

1. Открой своего бота в Telegram
2. Нажми `/start`
3. Нажми кнопку **"💬 Открыть чат с Gemini"**
4. Общайся с Gemini через красивый Mini App! 🎉

## Доступные модели

В `.env` можно указать любую модель:

| Модель | `GEMINI_MODEL` |
|--------|----------------|
| Gemini 2.5 Flash (быстрая) | `gemini-2.5-flash` |
| Gemini 2.5 Pro (умная) | `gemini-2.5-pro` |

## FAQ

**Q: Нет Google Cloud проекта для API Key?**
→ Зайди на [console.cloud.google.com/projectcreate](https://console.cloud.google.com/projectcreate), создай проект, потом вернись за ключом.

**Q: Можно без ngrok?**
→ Да, если развернёшь на сервере с HTTPS (VPS, Railway, Render и т.д.)

**Q: Бесплатно?**
→ Да! Gemini API бесплатный (15 RPM для Flash, 2 RPM для Pro). Ngrok бесплатный.

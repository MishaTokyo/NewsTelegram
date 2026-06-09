# News bot → Telegram

Краткий дайджест 2 раза в день (07:00 и 19:00 JST): **два отдельных сообщения** — 📰 новости (🇯🇵 + 🌍) и 🥇 металлы (цены + анализ + перевод под спойлером).

## Настройка (5 мин)

### 1. Telegram

1. [@BotFather](https://t.me/BotFather) → `/newbot` → скопируйте `BOT_TOKEN`
2. Напишите боту любое сообщение
3. Откройте `https://api.telegram.org/bot<TOKEN>/getUpdates` → найдите `"chat":{"id":...}`

### 2. Gemini (бесплатно)

[Google AI Studio](https://aistudio.google.com/apikey) → Create API Key

### 3. Локальный тест

```bash
cp .env.example .env
# заполните TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY
pip install -r requirements.txt
python main.py
```

### 4. GitHub Actions (автозапуск)

1. Создайте репозиторий, запушьте код
2. Settings → Secrets → Actions → добавьте:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `GEMINI_API_KEY`
3. Actions → Japan news brief → Run workflow (тест)

Расписание: **07:00 и 19:00 JST** (японское время).

## Формат — два сообщения

**Сообщение 1 — новости** (только если есть новые заголовки):

> 📰 09.06.2026 · 07:00 JST  
> 🇯🇵 日本 … 🌍 World …

**Сообщение 2 — металлы** (всегда отдельно):

> 🥇 Metals · 09.06.2026 · 07:00 JST  
> ──────────────────  
> Gold: $4,284/oz (-1.8%)  
> Silver: $47.20/oz (+0.5%)  
> [нейтральный анализ: почему такие цены, перспективы week/month/year]  
> ············  
> Перевод  
> *[скрыто блюром]*

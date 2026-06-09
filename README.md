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

**Сообщение 2 — металлы** (отдельно, только новые заголовки):

> 🥇 Metals · 09.06.2026 · 07:00 JST  
> Gold $4,284 (-1.8%) · Silver $47 (+0.5%)
>
> Gold  
> ──────────────────  
> Citi cut gold target to $4,000. COMEX holdings rose. Fed minutes cited rate path.
>
> Silver  
> ──────────────────  
> Silver rebounded to 200-day average. Industrial demand from solar sector cited.
>
> [анализ]  
> Sprott: debt cycle supports gold. Fed: rates on hold. 7d/30d/1y — только из источников.
>
> ············  
> Перевод  
> *[скрыто блюром]*

Gold/Silver — из новостных заголовков. [анализ] — только Fed, ECB, аналитики (≤3 предложения, 7/30/365 дней).

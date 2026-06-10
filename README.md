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

Расписание: **07:00 и 19:00 по Японии (JST, Asia/Tokyo)**. GitHub Actions cron настроен под этот часовой пояс.

## Формат — два сообщения

**Сообщение 1 — новости** (только если есть новые заголовки):

> 📰 10.06.2026 · 07:00 JST
>
> 『日本』  
> 石川県（いしかわけん）で震度5強。日銀は政策金利を据え置き。
>
> Перевод:  
> *[скрыто блюром]*
>
> ----------------
>
> [World News]  
> Day 38 of the war. Oil at $126/barrel.
>
> Перевод  
> *[скрыто блюром]*

**Сообщение 2 — металлы** (отдельно, на русском, без перевода):

> 🥇 10.06.2026 · 07:00 JST  
> Gold $4,284 (-1.8%) · Silver $47 (+0.5%)
>
> [Золото]:  
> Сегодня Citi снизила прогноз до $4,000. Приток в ETF COMEX.
>
> [Серебро]:  
> Сегодня отскок к 200-дневной средней. Спрос со стороны солнечной энергетики.
>
> [Анализ]:  
> Новости указывают на давление на золото со стороны укрепления доллара. 7 дней — боковик. 30 дней — ключ CPI. Год — баланс ETF и ставок ФРС.

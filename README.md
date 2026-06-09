# News bot → Telegram

Краткий дайджест 2 раза в день (07:00 и 19:00 JST): **🇯🇵 Япония** (на японском) + **🌍 Мир** (на английском) + перевод. Бесплатно: RSS + Gemini + GitHub Actions.

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

## Формат сообщения

> 📰 09.06.2026 · 07:00 JST
>
> 🇯🇵 日本  
> ──────────────────  
> 石川県（いしかわけん）で震度5強。日銀（にっぽんぎんこう）は政策金利を据え置き。
>
> ············  
> Перевод  
> *[скрыто блюром — нажмите, чтобы открыть]*
>
> ──────────────────
>
> 🌍 World  
> ──────────────────  
> Day 38 of the war. Oil at $126/barrel.
>
> ············  
> Перевод  
> *[скрыто блюром — нажмите, чтобы открыть]*

Чтения в скобках после префектур и имён. Настоящая фуригана в Telegram недоступна — только так.

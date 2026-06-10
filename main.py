#!/usr/bin/env python3
"""Japan + World news and separate Metals brief → Telegram."""

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import feedparser
import google.generativeai as genai
import requests
from dotenv import load_dotenv

load_dotenv()

# Always Japan time — 07:00 and 19:00 JST scheduled in .github/workflows/japan-news.yml
TZ = ZoneInfo("Asia/Tokyo")
CACHE_FILE = os.getenv("SENT_CACHE_FILE", "sent_cache.json")
CACHE_TTL_HOURS = 24
MAX_ITEMS = 20
METALS_MAX_ITEMS = 12
TELEGRAM_LIMIT = 4096
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; news-bot/1.0)"}

JAPAN_FEEDS = [
    ("NHK", "https://www3.nhk.or.jp/rss/news/cat0.xml"),
    ("NHK 社会", "https://www3.nhk.or.jp/rss/news/cat1.xml"),
    ("NHK 経済", "https://www3.nhk.or.jp/rss/news/cat2.xml"),
]

WORLD_FEEDS = [
    ("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Reuters", "https://feeds.reuters.com/reuters/topNews"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
]

METALS_FEEDS = [
    ("Mining.com", "https://www.mining.com/feed/"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("Reuters", "https://feeds.reuters.com/reuters/businessNews"),
]

# Central banks + institutions for [анализ] section
ANALYSIS_FEEDS = [
    ("Fed", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("ECB", "https://www.ecb.europa.eu/rss/press.html"),
    ("Mining.com", "https://www.mining.com/feed/"),
    ("Reuters", "https://feeds.reuters.com/reuters/businessNews"),
    ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
]

GOLD_KW = re.compile(r"\b(gold|xau|bullion)\b", re.I)
SILVER_KW = re.compile(r"\b(silver|xag)\b", re.I)
METALS_KEYWORDS = re.compile(
    r"gold|silver|xau|xag|precious.?metal|bullion|comex|platinum|palladium",
    re.I,
)
ANALYSIS_METALS_KW = re.compile(
    r"gold|silver|xau|xag|precious|bullion|comex|platinum|palladium",
    re.I,
)
ANALYSIS_OPINION_KW = re.compile(
    r"outlook|forecast|target|expects|sees|warns|cut|raise|analyst|"
    r"sprott|goldman|citi|jpmorgan|ubs|barclays|deutsche|hsbc|"
    r"world gold council|imf|treasury|comex|etf",
    re.I,
)
ANALYSIS_MACRO_KW = re.compile(
    r"rate|inflation|cpi|yield|monetary|policy|dollar|fed|ecb|boj|"
    r"central bank|fomc|minutes|powell|lagarde|interest",
    re.I,
)
CB_SOURCES = frozenset({"Fed", "ECB"})

# flash-lite first: higher free-tier daily quota (see aistudio.google.com/rate-limit)
GEMINI_MODELS = ["gemini-2.5-flash-lite", "gemini-2.0-flash-lite", "gemini-2.5-flash"]

FOREIGN_MARKERS = re.compile(
    r"アメリカ|米国|トランプ|バイデン|中国|ロシア|ウクライナ|イスラエル|"
    r"ガザ|イラン|欧州|NATO|中東|韓国|北朝鮮|台湾|フィリピン|"
    r"インド|パキスタン|シリア|イラク|アフガン|フランク|ドイツ|"
    r"英首相|米大統領|国連総会|海外|外国|グローバル|国際情勢"
)

SECTION_SEP = "· · · · · · · · · ·"

NEWS_PROMPT_RULES = """Rules:
- Cover only the headlines listed below (all are NEW since the last digest).
- Japan block: ONLY Japan-domestic news. ZERO mentions of other countries.
- Japanese readings: after prefectures, cities, and personal names in kanji, add reading in parentheses: 石川県（いしかわけん）、高市早苗（たかいちさなえ）.
- Japan text MUST start with「本日は」.
- No emoji flags. No long underline lines.
- Section separator between Japan and World: exactly "{sep}" on its own line.
- Russian translations: professional newsroom style, concise.
- Skip minor crime unless nationally significant.
- Do NOT invent facts.
- No extra text outside the structure shown."""

JAPAN_BLOCK = """[Япония]:
本日は<one short paragraph, 4–6 sentences, JAPANESE only. Must begin with 本日は. Telegraphic style.>

[Перевод]:
<professional Russian translation. Same facts, concise.>"""

WORLD_BLOCK = """[World News]:
<one short paragraph, 4–6 sentences, ENGLISH only. Telegraphic style.>

[Перевод]:
<professional Russian translation. Same facts, concise.>"""

METALS_PROMPT = """Собери дайджест по драгметаллам на РУССКОМ. Источники — только заголовки ниже.

Формат (строго):

{prices}

[Золото]:
Сегодня <3–5 коротких фактов из GOLD headlines. Деловой стиль Reuters. Только факты из заголовков.>

[Серебро]:
Сегодня <3–5 коротких фактов из SILVER headlines. Только факты из заголовков.>

[Анализ]:
<3–4 предложения на русском. Как эти новости и мнения институтов (ANALYSIS SOURCES) влияют на цену золота и серебра. Укажи перспективу: ~7 дней / ~30 дней / ~1 год. Нейтрально, без хайпа. Упоминай источники: «ФРС: ...», «Sprott: ...». Только из источников, ничего не выдумывай. Не инвестиционная рекомендация.>

Правила:
- Весь текст на русском.
- Без подчёркиваний, линий, эмодзи внутри блоков.
- Без блока «Перевод».
- [Золото] и [Серебро] — только из соответствующих заголовков.
- [Анализ] — из новостей + ANALYSIS SOURCES.
- Если заголовков нет: «Существенных новостей нет.»
- Цены — первая строка, как в SPOT PRICES.

SPOT PRICES:
{prices}

GOLD headlines:
{gold_headlines}

SILVER headlines:
{silver_headlines}

ANALYSIS SOURCES:
{analysis_headlines}
"""


def require_env(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        print(f"Missing {name}", file=sys.stderr)
        sys.exit(1)
    return val


def fetch_rss(feeds: list[tuple[str, str]]) -> list[dict]:
    items: list[dict] = []
    for source, url in feeds:
        try:
            feed = feedparser.parse(url, agent=HTTP_HEADERS["User-Agent"])
            for entry in feed.entries[:8]:
                title = (entry.get("title") or "").strip()
                if title:
                    items.append({"source": source, "title": title})
        except Exception as e:
            print(f"RSS {source}: {e}", file=sys.stderr)
    return items


def fetch_metals_headlines() -> list[dict]:
    items = fetch_rss(METALS_FEEDS)
    filtered = [it for it in items if METALS_KEYWORDS.search(it["title"])]
    if not filtered:
        filtered = [it for it in items if ANALYSIS_METALS_KW.search(it["title"])]
    return dedupe(filtered, limit=METALS_MAX_ITEMS)


def is_analysis_headline(item: dict) -> bool:
    title = item["title"]
    source = item["source"]
    if source in CB_SOURCES and ANALYSIS_MACRO_KW.search(title):
        return True
    if ANALYSIS_METALS_KW.search(title) and ANALYSIS_OPINION_KW.search(title):
        return True
    if ANALYSIS_METALS_KW.search(title) and re.search(
        r"sprott|goldman|citi|jpmorgan|ubs|barclays|deutsche|hsbc|"
        r"world gold council|imf|treasury|analyst|fund",
        title,
        re.I,
    ):
        return True
    return False


def fetch_analysis_headlines() -> list[dict]:
    items = fetch_rss(ANALYSIS_FEEDS)
    filtered = [it for it in items if is_analysis_headline(it)]
    return dedupe(filtered, limit=15)


def fetch_metals_prices() -> str:
    parts: list[str] = []
    for symbol, label in [("GC=F", "Gold"), ("SI=F", "Silver")]:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
            r = requests.get(url, headers=HTTP_HEADERS, timeout=15)
            r.raise_for_status()
            meta = r.json()["chart"]["result"][0]["meta"]
            price = float(meta["regularMarketPrice"])
            prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
            chg_pct = (price - prev) / prev * 100 if prev else 0.0
            parts.append(f"{label} ${price:,.0f} ({chg_pct:+.1f}%)")
        except Exception as e:
            print(f"Price {symbol}: {e}", file=sys.stderr)
    return " · ".join(parts) if parts else "Prices unavailable"


def item_key(title: str) -> str:
    return re.sub(r"\W+", "", title.lower())[:80]


def dedupe(items: list[dict], limit: int = MAX_ITEMS) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        key = item_key(it["title"])
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out[:limit]


def load_sent_cache() -> dict[str, str]:
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except (json.JSONDecodeError, OSError) as e:
        print(f"Cache read: {e}", file=sys.stderr)
        return {}


def save_sent_cache(cache: dict[str, str]) -> None:
    cutoff = (datetime.now(TZ) - timedelta(hours=CACHE_TTL_HOURS)).isoformat()
    pruned = {k: v for k, v in cache.items() if v >= cutoff}
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(pruned, f, ensure_ascii=False, indent=2)


def filter_new(items: list[dict], cache: dict[str, str]) -> list[dict]:
    new = [it for it in items if item_key(it["title"]) not in cache]
    if len(items) - len(new):
        print(f"Skipped {len(items) - len(new)} already-sent headlines", file=sys.stderr)
    return new


def mark_sent(items: list[dict], cache: dict[str, str], now: datetime) -> None:
    ts = now.isoformat()
    for it in items:
        cache[item_key(it["title"])] = ts


def format_headlines(items: list[dict]) -> str:
    return "\n".join(f"[{it['source']}] {it['title']}" for it in items)


def call_gemini(prompt: str) -> str:
    genai.configure(api_key=require_env("GEMINI_API_KEY"))
    last_err: Exception | None = None
    for name in GEMINI_MODELS:
        for attempt in range(3):
            try:
                resp = genai.GenerativeModel(name).generate_content(prompt)
                text = (resp.text or "").strip()
                if text:
                    print(f"Gemini model: {name}", file=sys.stderr)
                    return text
            except Exception as e:
                last_err = e
                print(f"Gemini {name} (try {attempt + 1}): {e}", file=sys.stderr)
                if "429" in str(e) and attempt < 2:
                    time.sleep(40 * (attempt + 1))
                    continue
                break
    raise RuntimeError(f"All Gemini models failed: {last_err}")


def prefilter_japan(items: list[dict]) -> list[dict]:
    domestic = [it for it in items if not FOREIGN_MARKERS.search(it["title"])]
    return domestic if domestic else items


def filter_japan_headlines(items: list[dict]) -> list[dict]:
    filtered = prefilter_japan(items)
    print(f"Japan filter: {len(items)} → {len(filtered)}", file=sys.stderr)
    return dedupe(filtered, limit=MAX_ITEMS)


def clean_brief(text: str) -> str:
    text = text.replace("🇷🇺", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


TRANSLATION_RE = re.compile(
    r"(\[Перевод\]:\s*\n)(.+?)(?=\n\n· · ·|\n\n\[World News\]|\n\n\[Япония\]|\Z)",
    re.DOTALL,
)


def to_telegram_html(text: str) -> str:
    out: list[str] = []
    pos = 0
    for m in TRANSLATION_RE.finditer(text):
        out.append(escape_html(text[pos : m.start()]))
        out.append(escape_html(m.group(1)))
        out.append(f"<tg-spoiler>{escape_html(m.group(2).strip())}</tg-spoiler>")
        pos = m.end()
    out.append(escape_html(text[pos:]))
    return "".join(out)


def build_news_prompt(japan_items: list[dict], world_items: list[dict]) -> str:
    blocks: list[str] = []
    if japan_items:
        blocks.append(JAPAN_BLOCK)
    if japan_items and world_items:
        blocks.append("----------------")
    if world_items:
        blocks.append(WORLD_BLOCK)

    parts = [
        "Write a news brief from the NEW headlines below.",
        "Output format (follow EXACTLY):\n",
        "\n\n".join(blocks),
        NEWS_PROMPT_RULES,
    ]
    if japan_items:
        parts.append(f"JAPAN headlines:\n{format_headlines(japan_items)}")
    if world_items:
        parts.append(f"WORLD headlines:\n{format_headlines(world_items)}")
    return "\n\n".join(parts)


def summarize_news(japan_items: list[dict], world_items: list[dict]) -> str:
    return clean_brief(call_gemini(build_news_prompt(japan_items, world_items)))


def split_gold_silver(items: list[dict]) -> tuple[list[dict], list[dict]]:
    gold = [it for it in items if GOLD_KW.search(it["title"])]
    silver = [it for it in items if SILVER_KW.search(it["title"])]
    return gold, silver


def summarize_metals(
    metals_headlines: list[dict],
    analysis_headlines: list[dict],
    prices: str,
) -> str:
    gold, silver = split_gold_silver(metals_headlines)
    prompt = METALS_PROMPT.format(
        prices=prices,
        gold_headlines=format_headlines(gold) or "(none)",
        silver_headlines=format_headlines(silver) or "(none)",
        analysis_headlines=format_headlines(analysis_headlines) or "(none)",
    )
    return clean_brief(call_gemini(prompt))


def send_telegram(text: str, *, html: bool = False) -> None:
    token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = to_telegram_html(text) if html else text
    remaining = body
    while remaining:
        chunk, remaining = remaining[:TELEGRAM_LIMIT], remaining[TELEGRAM_LIMIT:]
        payload: dict = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if html:
            payload["parse_mode"] = "HTML"
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()


def main() -> None:
    now = datetime.now(TZ)
    news_header = f"📰 {now:%d.%m.%Y} · {now:%H:%M} JST"
    metals_header = f"🥇 {now:%d.%m.%Y} · {now:%H:%M} JST"
    sent_cache = load_sent_cache()
    prices = fetch_metals_prices()
    errors: list[str] = []

    japan_all = filter_japan_headlines(dedupe(fetch_rss(JAPAN_FEEDS)))
    world_all = dedupe(fetch_rss(WORLD_FEEDS))
    metals_all = fetch_metals_headlines()
    analysis_all = fetch_analysis_headlines()

    japan_items = filter_new(japan_all, sent_cache)
    world_items = filter_new(world_all, sent_cache)
    metals_new = filter_new(metals_all, sent_cache)
    analysis_new = filter_new(analysis_all, sent_cache)

    # --- Message 1: News (only if new headlines) ---
    if japan_items or world_items:
        try:
            news_brief = summarize_news(japan_items, world_items)
            send_telegram(f"{news_header}\n\n{news_brief}", html=True)
            mark_sent(japan_items + world_items, sent_cache, now)
            print(f"News sent. japan={len(japan_items)} world={len(world_items)}")
        except RuntimeError as e:
            errors.append("news")
            send_telegram(f"{news_header}\n\n⚠️ Не удалось сгенерировать новости (лимит Gemini API).")
            print(f"News failed: {e}", file=sys.stderr)
    else:
        print("No new news — skipping news message.")

    # --- Message 2: Metals (new headlines or new institutional analysis) ---
    if metals_new or analysis_new:
        try:
            metals_brief = summarize_metals(metals_new, analysis_all, prices)
            send_telegram(f"{metals_header}\n\n{metals_brief}", html=False)
            mark_sent(metals_new + analysis_new, sent_cache, now)
            print(
                f"Metals sent. news={len(metals_new)} analysis={len(analysis_new)}",
            )
        except RuntimeError as e:
            errors.append("metals")
            send_telegram(f"{metals_header}\n\n⚠️ Не удалось сгенерировать дайджест металлов (лимит Gemini API).")
            print(f"Metals failed: {e}", file=sys.stderr)
    else:
        print("No new metals/analysis headlines — skipping metals message.")

    save_sent_cache(sent_cache)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()

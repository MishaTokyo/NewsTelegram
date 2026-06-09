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

TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tokyo"))
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
]

METALS_KEYWORDS = re.compile(
    r"gold|silver|xau|xag|precious.?metal|bullion|comex|platinum|palladium",
    re.I,
)

GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash-lite", "gemini-2.0-flash"]

FOREIGN_MARKERS = re.compile(
    r"アメリカ|米国|トランプ|バイデン|中国|ロシア|ウクライナ|イスラエル|"
    r"ガザ|イラン|欧州|NATO|中東|韓国|北朝鮮|台湾|フィリピン|"
    r"インド|パキスタン|シリア|イラク|アフガン|フランク|ドイツ|"
    r"英首相|米大統領|国連総会|海外|外国|グローバル|国際情勢"
)

NEWS_PROMPT_RULES = """Rules:
- Cover only the headlines listed below (all are NEW since the last digest).
- Japan block: ONLY Japan-domestic news. ZERO mentions of other countries.
- Japanese readings: after prefectures, cities, and personal names in kanji, add reading in Japanese parentheses: 石川県（いしかわけん）、高市早苗（たかいちさなえ）.
- Dashes (──────────────────) ONLY under section titles and between main sections — never under "Перевод".
- Before each translation: one line of middle dots (············) then "Перевод" — no emojis, no flags.
- Russian translations: professional newsroom style.
- Skip minor crime unless nationally significant.
- Do NOT invent facts.
- No extra text outside the structure shown."""

JAPAN_BLOCK = """🇯🇵 日本
──────────────────

<one short paragraph, 4–6 sentences, JAPANESE only. Telegraphic style.>

············
Перевод
<professional Russian translation. Same facts, concise.>"""

WORLD_BLOCK = """🌍 World
──────────────────

<one short paragraph, 4–6 sentences, ENGLISH only. Telegraphic style.>

············
Перевод
<professional Russian translation. Same facts, concise.>"""

METALS_PROMPT = """Write a professional precious-metals market brief (separate Telegram message).

Output format (follow EXACTLY):

🥇 Gold & Silver
──────────────────

<PRICES block — copy SPOT PRICES below verbatim, one line per metal>

<ENGLISH analysis, 5–8 sentences, institutional desk note style:
- Why gold and silver are at these levels today (macro, rates, USD, flows, geopolitics, industrial demand).
- Balanced: cite factors supporting AND pressuring prices. No bullish/bearish bias.
- Outlook: one conditional clause each for ~1 week, ~1 month, ~1 year ("if real yields...", "range likely while...").
- End with: "Not investment advice.">

············
Перевод
<professional Russian translation. Same structure and facts.>

Rules:
- Use SPOT PRICES exactly as given.
- Neutral, factual, no hype, not investment advice.
- Do NOT invent prices or events.
- No extra sections or text.

SPOT PRICES:
{prices}

METALS headlines:
{headlines}
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
        filtered = items[:METALS_MAX_ITEMS]
    return dedupe(filtered, limit=METALS_MAX_ITEMS)


def fetch_metals_prices() -> str:
    lines: list[str] = []
    for symbol, label in [("GC=F", "Gold"), ("SI=F", "Silver")]:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
            r = requests.get(url, headers=HTTP_HEADERS, timeout=15)
            r.raise_for_status()
            meta = r.json()["chart"]["result"][0]["meta"]
            price = float(meta["regularMarketPrice"])
            prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
            chg_pct = (price - prev) / prev * 100 if prev else 0.0
            lines.append(f"{label}: ${price:,.2f}/oz ({chg_pct:+.2f}% today)")
        except Exception as e:
            print(f"Price {symbol}: {e}", file=sys.stderr)
    return "\n".join(lines) if lines else ""


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
    text = re.sub(r"(Перевод\n)\s*─+\s*\n", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


TRANSLATION_RE = re.compile(
    r"([·]{6,}\s*\nПеревод\n)(.+?)(?=\n\n──────────────────|\Z)",
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
        blocks.append("──────────────────")
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


def summarize_metals(metals_headlines: list[dict], prices: str) -> str:
    prompt = METALS_PROMPT.format(
        prices=prices,
        headlines=format_headlines(metals_headlines) or "(no headlines — use macro context only)",
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
    metals_header = f"🥇 Metals · {now:%d.%m.%Y} · {now:%H:%M} JST"
    sent_cache = load_sent_cache()
    prices = fetch_metals_prices()
    errors: list[str] = []

    japan_all = filter_japan_headlines(dedupe(fetch_rss(JAPAN_FEEDS)))
    world_all = dedupe(fetch_rss(WORLD_FEEDS))
    metals_all = fetch_metals_headlines()

    japan_items = filter_new(japan_all, sent_cache)
    world_items = filter_new(world_all, sent_cache)
    metals_new = filter_new(metals_all, sent_cache)

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

    # --- Message 2: Metals (only if new headlines) ---
    if metals_new and prices:
        try:
            metals_brief = summarize_metals(metals_new, prices)
            send_telegram(f"{metals_header}\n\n{metals_brief}", html=True)
            mark_sent(metals_new, sent_cache, now)
            print(f"Metals sent. new={len(metals_new)}")
        except RuntimeError as e:
            errors.append("metals")
            send_telegram(f"{metals_header}\n\n⚠️ Не удалось сгенерировать анализ металлов (лимит Gemini API).")
            print(f"Metals failed: {e}", file=sys.stderr)
    elif metals_new and not prices:
        send_telegram(f"{metals_header}\n\n⚠️ Цены на металлы недоступны.")
        errors.append("prices")
    else:
        print("No new metals headlines — skipping metals message.")

    save_sent_cache(sent_cache)

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()

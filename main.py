#!/usr/bin/env python3
"""Japan + World + Metals news brief → Telegram."""

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
CACHE_TTL_HOURS = 72
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
    r"gold|silver|xau|xag|precious.?metal|bullion|comex|"
    r"золот|серебр|白金|金|銀|パラジウム|platinum",
    re.I,
)

GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash-lite", "gemini-2.0-flash"]

FOREIGN_MARKERS = re.compile(
    r"アメリカ|米国|トランプ|バイデン|中国|ロシア|ウクライナ|イスラエル|"
    r"ガザ|イラン|欧州|NATO|中東|韓国|北朝鮮|台湾|フィリピン|"
    r"インド|パキスタン|シリア|イラク|アフガン|フランク|ドイツ|"
    r"英首相|米大統領|国連総会|海外|外国|グローバル|国際情勢"
)

PROMPT_RULES = """Rules:
- Cover only the headlines listed below (all are NEW since the last digest).
- Japan block: ONLY Japan-domestic news. ZERO mentions of other countries.
- Japanese readings: after prefectures, cities, and personal names in kanji, add reading in Japanese parentheses: 石川県（いしかわけん）、高市早苗（たかいちさなえ）.
- Metals block: strictly neutral analysis — no bullish/bearish bias, no hype, not investment advice. Present drivers on both sides (what supports AND what pressures prices). Outlook for ~1 week / ~1 month / ~1 year must be conditional ("if rates stay...", "consensus sees range..."), cite factors not certainties.
- Dashes (──────────────────) ONLY under section titles and between main sections — never under "Перевод".
- Before each translation: one line of middle dots (············) then "Перевод" — no emojis, no flags.
- Russian translations: professional newsroom style, not word-for-word literal.
- Skip minor crime unless nationally significant.
- Do NOT invent facts or prices.
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

METALS_BLOCK = """🥇 Gold & Silver
──────────────────

<ENGLISH only. One dense paragraph, 6–10 sentences:
1) State current gold & silver levels from SPOT PRICES (use exact numbers).
2) Why prices are moving today — 2–4 factual drivers from headlines AND macro (rates, USD, geopolitics, ETF flows, industrial demand). Mention factors that could push UP and DOWN — balanced.
3) Outlook: one short clause each for ~1 week, ~1 month, ~1 year — neutral, conditional, no predictions-as-facts.
End with: "Not investment advice.">

············
Перевод
<professional Russian translation. Same facts, concise.>"""

METALS_ONLY_RULES = """Rules:
- Metals-only update (no Japan/World sections in output).
- Strictly neutral — no bias, no hype, not investment advice.
- Use SPOT PRICES exactly. Balance up-side and down-side drivers.
- Outlook: conditional clauses for ~1 week / ~1 month / ~1 year.
- Format exactly as shown. Do NOT invent facts."""


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
    """Spot-like prices via Yahoo Finance futures (free, no API key)."""
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
    return "\n".join(lines) if lines else "Prices unavailable"


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
    """Drop obvious foreign-news headlines before AI filter."""
    domestic = [it for it in items if not FOREIGN_MARKERS.search(it["title"])]
    if not domestic:
        domestic = items
    return domestic


def filter_japan_headlines(items: list[dict]) -> list[dict]:
    """Keep Japan-domestic headlines without an extra Gemini call (saves API quota)."""
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
    """Wrap each translation block in Telegram spoiler (tap-to-reveal blur)."""
    out: list[str] = []
    pos = 0
    for m in TRANSLATION_RE.finditer(text):
        out.append(escape_html(text[pos : m.start()]))
        out.append(escape_html(m.group(1)))
        out.append(f"<tg-spoiler>{escape_html(m.group(2).strip())}</tg-spoiler>")
        pos = m.end()
    out.append(escape_html(text[pos:]))
    return "".join(out)


def build_prompt(
    japan_items: list[dict],
    world_items: list[dict],
    metals_headlines: list[dict],
    prices: str,
    *,
    metals_only: bool = False,
) -> str:
    if metals_only:
        return "\n\n".join([
            "Write a metals-only market brief.",
            f"Output format (follow EXACTLY):\n\n{METALS_BLOCK}",
            METALS_ONLY_RULES,
            f"SPOT PRICES (use these exact numbers):\n{prices}",
            f"METALS headlines:\n{format_headlines(metals_headlines) or '(use macro context from prices only)'}",
        ])

    blocks: list[str] = []
    if japan_items:
        blocks.append(JAPAN_BLOCK)
    if japan_items and (world_items or metals_headlines or prices):
        blocks.append("──────────────────")
    if world_items:
        blocks.append(WORLD_BLOCK)
    if world_items and (metals_headlines or prices):
        blocks.append("──────────────────")
    if metals_headlines or prices:
        blocks.append(METALS_BLOCK)

    parts = [
        "Write a news brief from the NEW headlines below.",
        "Output format (follow EXACTLY):\n",
        "\n\n".join(blocks),
        PROMPT_RULES,
        f"SPOT PRICES (use for metals section):\n{prices}",
    ]
    if japan_items:
        parts.append(f"JAPAN headlines:\n{format_headlines(japan_items)}")
    if world_items:
        parts.append(f"WORLD headlines:\n{format_headlines(world_items)}")
    if metals_headlines:
        parts.append(f"METALS headlines:\n{format_headlines(metals_headlines)}")
    return "\n\n".join(parts)


def summarize(
    japan_items: list[dict],
    world_items: list[dict],
    metals_items: list[dict],
    prices: str,
    *,
    metals_only: bool = False,
) -> str:
    prompt = build_prompt(
        japan_items, world_items, metals_items, prices, metals_only=metals_only,
    )
    return clean_brief(call_gemini(prompt))


def send_telegram(text: str, *, html: bool = False) -> None:
    token = require_env("TELEGRAM_BOT_TOKEN")
    chat_id = require_env("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict = {
        "chat_id": chat_id,
        "text": text[:TELEGRAM_LIMIT],
        "disable_web_page_preview": True,
    }
    if html:
        payload["parse_mode"] = "HTML"
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()


def main() -> None:
    now = datetime.now(TZ)
    header = f"📰 {now:%d.%m.%Y} · {now:%H:%M} JST"
    sent_cache = load_sent_cache()
    prices = fetch_metals_prices()

    japan_all = filter_japan_headlines(dedupe(fetch_rss(JAPAN_FEEDS)))
    world_all = dedupe(fetch_rss(WORLD_FEEDS))
    metals_all = fetch_metals_headlines()

    if not japan_all and not world_all and not metals_all and prices == "Prices unavailable":
        send_telegram(f"{header}\n\nИсточники недоступны.")
        return

    japan_items = filter_new(japan_all, sent_cache)
    world_items = filter_new(world_all, sent_cache)
    metals_new = filter_new(metals_all, sent_cache)
    has_news = bool(japan_items or world_items)
    metals_only = not has_news

    if metals_only and prices == "Prices unavailable" and not metals_new:
        send_telegram(f"{header}\n\nНовых новостей с прошлой рассылки нет.")
        print("Nothing new to send.")
        return

    metals_headlines = metals_all if metals_only else (metals_new or metals_all[:6])

    try:
        brief = summarize(
            japan_items,
            world_items,
            metals_headlines,
            prices,
            metals_only=metals_only,
        )
    except RuntimeError as e:
        send_telegram(f"{header}\n\n⚠️ Не удалось сгенерировать дайджест (лимит Gemini API). Попробуйте позже.")
        print(f"Gemini failed: {e}", file=sys.stderr)
        sys.exit(1)

    send_telegram(to_telegram_html(f"{header}\n\n{brief}"), html=True)

    to_mark = japan_items + world_items + metals_new
    mark_sent(to_mark, sent_cache, now)
    save_sent_cache(sent_cache)
    print(
        "Sent.", len(brief), "chars,",
        f"japan={len(japan_items)} world={len(world_items)} metals_only={metals_only}",
    )


if __name__ == "__main__":
    main()

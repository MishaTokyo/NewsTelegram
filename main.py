#!/usr/bin/env python3
"""Japan + World news brief → Telegram. Dense facts only."""

import json
import os
import re
import sys
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
TELEGRAM_LIMIT = 4096

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

GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.0-flash-lite", "gemini-2.0-flash"]

# Headlines that are primarily about another country (not Japan)
FOREIGN_MARKERS = re.compile(
    r"アメリカ|米国|トランプ|バイデン|中国|ロシア|ウクライナ|イスラエル|"
    r"ガザ|イラン|欧州|NATO|中東|韓国|北朝鮮|台湾|フィリピン|"
    r"インド|パキスタン|シリア|イラク|アフガン|フランク|ドイツ|"
    r"英首相|米大統領|国連総会|海外|外国|グローバル|国際情勢"
)

FILTER_PROMPT = """You filter NHK headlines for a JAPAN-DOMESTIC news digest.

INCLUDE only headlines about:
- Events happening IN Japan (disasters, crime, local government, prefectures, cities)
- Japanese national politics, economy, society, culture, sports IN Japan
- Japanese companies/domestic policy when the story is about Japan itself

EXCLUDE headlines primarily about:
- Other countries, foreign leaders, foreign wars, foreign elections
- International relations UNLESS the story is exclusively about a domestic Japanese decision/impact
- World economy/foreign markets UNLESS about Japan's domestic economy/markets

Return ONLY the exact headline lines to keep (copy verbatim, one per line).
If none qualify, return exactly: NONE

Headlines:
{headlines}
"""

PROMPT_RULES = """Rules:
- Cover only the headlines listed below (all are NEW since the last digest).
- Japan block: ONLY Japan-domestic news. ZERO mentions of other countries.
- Japanese readings: after prefectures, cities, and personal names in kanji, add reading in Japanese parentheses: 石川県（いしかわけん）、高市早苗（たかいちさなえ）.
- Dashes (──────────────────) ONLY under section titles and between the two main sections — never under "Перевод".
- Before each translation: one line of middle dots (············) then "Перевод" — no emojis, no flags.
- Russian translations: professional newsroom style, not word-for-word literal.
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
            feed = feedparser.parse(url)
            for entry in feed.entries[:8]:
                title = (entry.get("title") or "").strip()
                if title:
                    items.append({"source": source, "title": title})
        except Exception as e:
            print(f"RSS {source}: {e}", file=sys.stderr)
    return items


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
        try:
            resp = genai.GenerativeModel(name).generate_content(prompt)
            text = (resp.text or "").strip()
            if text:
                print(f"Gemini model: {name}", file=sys.stderr)
                return text
        except Exception as e:
            last_err = e
            print(f"Gemini {name}: {e}", file=sys.stderr)
    raise RuntimeError(f"All Gemini models failed: {last_err}")


def prefilter_japan(items: list[dict]) -> list[dict]:
    """Drop obvious foreign-news headlines before AI filter."""
    domestic = [it for it in items if not FOREIGN_MARKERS.search(it["title"])]
    if not domestic:
        domestic = items
    return domestic


def filter_japan_headlines(items: list[dict]) -> list[dict]:
    items = prefilter_japan(items)
    if not items:
        return []

    raw = call_gemini(FILTER_PROMPT.format(headlines=format_headlines(items)))
    if raw.strip().upper() == "NONE":
        return []

    kept_titles = {line.strip() for line in raw.splitlines() if line.strip()}
    filtered = [
        it for it in items
        if it["title"] in kept_titles
        or f"[{it['source']}] {it['title']}" in kept_titles
    ]
    if filtered:
        print(f"Japan filter: {len(items)} → {len(filtered)}", file=sys.stderr)
        return filtered

    # Fallback: match by title substring if model paraphrased slightly
    for title in kept_titles:
        for it in items:
            if it["title"] in title or title in it["title"]:
                filtered.append(it)
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


def build_prompt(japan_items: list[dict], world_items: list[dict]) -> str:
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
        PROMPT_RULES,
    ]
    if japan_items:
        parts.append(f"JAPAN headlines:\n{format_headlines(japan_items)}")
    if world_items:
        parts.append(f"WORLD headlines:\n{format_headlines(world_items)}")
    return "\n\n".join(parts)


def summarize(japan_items: list[dict], world_items: list[dict]) -> str:
    return clean_brief(call_gemini(build_prompt(japan_items, world_items)))


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

    japan_all = filter_japan_headlines(dedupe(fetch_rss(JAPAN_FEEDS)))
    world_all = dedupe(fetch_rss(WORLD_FEEDS))

    if not japan_all and not world_all:
        send_telegram(f"{header}\n\nИсточники недоступны.")
        return

    japan_items = filter_new(japan_all, sent_cache)
    world_items = filter_new(world_all, sent_cache)

    if not japan_items and not world_items:
        send_telegram(f"{header}\n\nНовых новостей с прошлой рассылки нет.")
        print("Nothing new to send.")
        return

    brief = summarize(japan_items, world_items)
    send_telegram(to_telegram_html(f"{header}\n\n{brief}"), html=True)

    mark_sent(japan_items + world_items, sent_cache, now)
    save_sent_cache(sent_cache)
    print("Sent.", len(brief), "chars,", len(japan_items) + len(world_items), "new headlines")


if __name__ == "__main__":
    main()

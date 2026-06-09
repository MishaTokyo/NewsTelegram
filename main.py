#!/usr/bin/env python3
"""Japan + World news brief → Telegram. Dense facts only."""

import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser
import google.generativeai as genai
import requests
from dotenv import load_dotenv

load_dotenv()

TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Tokyo"))
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

PROMPT = """Write a two-section news brief from the headlines below.

Style example (English section):
"Day 38 of the war. Trump's Tuesday deadline for Iran to reopen the Strait of Hormuz. Two US warplanes downed. Oil at $126/barrel."

Output format (follow EXACTLY):

🇯🇵 日本
──────────────────

<one short paragraph, 4–6 sentences, JAPANESE only. Telegraphic style.>

············
Перевод
<professional Russian translation. Newsroom style, same facts, concise. No line of dashes here.>

──────────────────

🌍 World
──────────────────

<one short paragraph, 4–6 sentences, ENGLISH only. Same telegraphic style.>

············
Перевод
<professional Russian translation. Newsroom style, same facts, concise.>

Rules:
- Cover the last ~12 hours ({window}).
- Japan block: ONLY Japan-domestic news. ZERO mentions of other countries.
- Japanese readings: Telegram has no furigana. After prefectures, cities, and personal names in kanji, add reading in Japanese parentheses: 石川県（いしかわけん）、高市早苗（たかいちさなえ）. Use correct hiragana/katakana readings.
- Dashes (──────────────────) ONLY under section titles and between the two main sections — never under "Перевод".
- Before each translation: one line of middle dots (············) then the word "Перевод" on the next line — no emojis, no flags.
- Russian translations: professional (как Reuters/BBC Russian), not word-for-word literal.
- Skip minor crime unless nationally significant.
- Do NOT invent facts.
- No extra text outside this structure.

JAPAN headlines:
{japan_headlines}

WORLD headlines:
{world_headlines}
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
            feed = feedparser.parse(url)
            for entry in feed.entries[:8]:
                title = (entry.get("title") or "").strip()
                if title:
                    items.append({"source": source, "title": title})
        except Exception as e:
            print(f"RSS {source}: {e}", file=sys.stderr)
    return items


def dedupe(items: list[dict], limit: int = MAX_ITEMS) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        key = re.sub(r"\W+", "", it["title"].lower())[:80]
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out[:limit]


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


def summarize(japan_items: list[dict], world_items: list[dict], window: str) -> str:
    prompt = PROMPT.format(
        japan_headlines=format_headlines(japan_items) or "(none)",
        world_headlines=format_headlines(world_items) or "(none)",
        window=window,
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
    kind = os.getenv("RUN_KIND", "morning").lower()
    window = "overnight" if kind == "morning" else "today"

    japan_items = filter_japan_headlines(dedupe(fetch_rss(JAPAN_FEEDS)))
    world_items = dedupe(fetch_rss(WORLD_FEEDS))

    if not japan_items and not world_items:
        send_telegram(f"📰 {now:%d.%m.%Y} · {now:%H:%M} JST — sources unavailable.")
        return

    brief = summarize(japan_items, world_items, window)
    header = f"📰 {now:%d.%m.%Y} · {now:%H:%M} JST"
    send_telegram(to_telegram_html(f"{header}\n\n{brief}"), html=True)
    print("Sent.", len(brief), "chars")


if __name__ == "__main__":
    main()

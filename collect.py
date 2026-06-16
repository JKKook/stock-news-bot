"""구글 뉴스 RSS에서 주식 관련 뉴스를 모으는 모듈 (무료, API 키 불필요)."""

import time
import urllib.parse
from datetime import datetime, timezone, timedelta

import feedparser

from config import LOOKBACK_HOURS

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _rss_url(query: str, lang: str) -> str:
    q = urllib.parse.quote(query)
    if lang == "ko":
        return f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _published(entry):
    parsed = entry.get("published_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)


def _clean_title(entry) -> str:
    # 줄바꿈·연속 공백을 한 칸으로 정리
    title = " ".join(entry.get("title", "").split())
    source = (entry.get("source", {}) or {}).get("title") if entry.get("source") else None
    if source and title.endswith(f" - {source}"):
        title = title[: -(len(source) + 3)]
    return title


def collect(queries, max_items: int) -> list[dict]:
    """
    queries: [(라벨, 검색어, 언어, 지역), ...]
    반환: [{"label", "region", "items": [{title, link, source, published, region}]}, ...]
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    results = []

    for label, query, lang, region in queries:
        feed = feedparser.parse(_rss_url(query, lang))
        items = []
        for entry in feed.entries:
            pub = _published(entry)
            if pub and pub < cutoff:
                continue
            items.append({
                "title": _clean_title(entry),
                "link": entry.get("link", "").strip(),
                "source": (entry.get("source", {}) or {}).get("title", ""),
                "published": pub,
                "region": region,
            })
            if len(items) >= max_items:
                break
        results.append({"label": label, "region": region, "items": items})

    return results


def _shorten(title: str, maxlen: int) -> str:
    title = title.strip()
    if len(title) <= maxlen:
        return title
    cut = title[:maxlen]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def build_headlines(pool: list[dict], count: int, maxlen: int) -> list[str]:
    """모든 기사를 모아 최신순 정렬 → '지역_짧은제목' 형태로 상위 N개 반환."""
    ordered = sorted(pool, key=lambda it: it["published"] or _EPOCH, reverse=True)
    out, seen = [], set()
    for it in ordered:
        short = _shorten(it["title"], maxlen)
        key = short.replace(" ", "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(f"{it['region']}_{short}")
        if len(out) >= count:
            break
    return out

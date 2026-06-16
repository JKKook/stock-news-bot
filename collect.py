"""구글 뉴스 RSS에서 주식 관련 뉴스를 모으는 모듈 (무료, API 키 불필요)."""

import time
import urllib.parse
from datetime import datetime, timezone, timedelta

import feedparser

from config import LOOKBACK_HOURS, MAX_PER_QUERY


def _rss_url(query: str, lang: str) -> str:
    """구글 뉴스 검색 RSS 주소를 만든다."""
    q = urllib.parse.quote(query)
    if lang == "ko":
        return f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _is_recent(entry, cutoff: datetime) -> bool:
    """기사 발행 시각이 cutoff(최근 N시간) 이내인지 확인."""
    parsed = entry.get("published_parsed")
    if not parsed:
        return True  # 시간 정보가 없으면 일단 포함
    published = datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
    return published >= cutoff


def _clean_title(entry) -> str:
    """구글 뉴스 제목 끝의 ' - 언론사' 꼬리표를 제거."""
    title = entry.get("title", "").strip()
    source = entry.get("source", {}).get("title") if entry.get("source") else None
    if source and title.endswith(f" - {source}"):
        title = title[: -(len(source) + 3)]
    return title


def collect(queries) -> list[dict]:
    """
    [(라벨, 검색어, 언어), ...] 를 받아
    [{"label", "items": [{"title", "link", "source"}]}, ...] 형태로 반환.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    results = []

    for label, query, lang in queries:
        feed = feedparser.parse(_rss_url(query, lang))
        items = []
        for entry in feed.entries:
            if not _is_recent(entry, cutoff):
                continue
            items.append({
                "title": _clean_title(entry),
                "link": entry.get("link", ""),
                "source": (entry.get("source", {}) or {}).get("title", ""),
            })
            if len(items) >= MAX_PER_QUERY:
                break
        if items:
            results.append({"label": label, "items": items})

    return results

"""뉴스 수집 모듈.

· 본문 발췌(요약문)는 Bing 뉴스 RSS에서 가져온다 (무료, API 키 불필요).
· 대표 링크 1개는 Yahoo Finance 헤드라인 RSS에서 가져온다.
"""

import re
import html
import time
import urllib.parse
from datetime import datetime, timezone, timedelta

import feedparser

from config import LOOKBACK_HOURS

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)
_YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US"


def _bing_url(query: str, lang: str) -> str:
    q = urllib.parse.quote(query)
    loc = "ko" if lang == "ko" else "en-US"
    return f"https://www.bing.com/news/search?q={q}&format=rss&setlang={loc}"


def _published(entry):
    parsed = entry.get("published_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)


def _clean_text(text: str, maxlen: int = 0) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")   # HTML 태그 제거
    text = html.unescape(text)
    text = " ".join(text.split())               # 줄바꿈·연속공백 정리
    if maxlen and len(text) > maxlen:
        text = text[:maxlen].rsplit(" ", 1)[0] + "…"
    return text


def _source(entry) -> str:
    src = entry.get("source")
    if isinstance(src, dict):
        return src.get("title", "") or ""
    return ""


def collect(queries, max_items: int) -> list[dict]:
    """
    queries: [(라벨, 검색어, 언어, 지역), ...]
    반환: [{"label", "region", "items": [{title, excerpt, source, published, region}]}, ...]
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    results = []

    for label, query, lang, region in queries:
        feed = feedparser.parse(_bing_url(query, lang))
        entries = sorted(feed.entries, key=lambda e: _published(e) or _EPOCH, reverse=True)

        def to_item(e):
            return {
                "title": _clean_text(e.get("title", "")),
                "excerpt": _clean_text(e.get("summary", "")),  # 번역 후 표시 단계에서 자름
                "source": _source(e),
                "published": _published(e),
                "region": region,
                "lang": lang,
            }

        recent = [to_item(e) for e in entries if (_published(e) or _EPOCH) >= cutoff]
        # 최근 기사가 없으면 가장 최신 기사라도 채운다(빈 섹션 방지)
        chosen = (recent or [to_item(e) for e in entries])[:max_items]
        results.append({"label": label, "region": region, "items": chosen})

    return results


def yahoo_headline() -> dict | None:
    """Yahoo Finance 최신 대표 헤드라인 1건 (제목 + 실제 링크)."""
    feed = feedparser.parse(_YAHOO_RSS)
    if not feed.entries:
        return None
    e = sorted(feed.entries, key=lambda x: _published(x) or _EPOCH, reverse=True)[0]
    return {"title": _clean_text(e.get("title", "")), "link": e.get("link", "").strip()}


def _shorten(title: str, maxlen: int) -> str:
    title = title.strip()
    if len(title) <= maxlen:
        return title
    cut = title[:maxlen]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def build_headlines(pool: list[dict], per_region: int, maxlen: int) -> dict:
    """모든 기사를 최신순 정렬 → 지역별(국내/해외)로 상위 N개씩 짧은 제목 반환."""
    ordered = sorted(pool, key=lambda it: it["published"] or _EPOCH, reverse=True)
    groups = {"국내": [], "해외": []}
    seen = set()
    for it in ordered:
        region = it["region"]
        if region not in groups or len(groups[region]) >= per_region:
            continue
        short = _shorten(it["title"], maxlen)
        key = short.replace(" ", "")
        if not key or key in seen:
            continue
        seen.add(key)
        groups[region].append(short)
        if all(len(v) >= per_region for v in groups.values()):
            break
    return groups

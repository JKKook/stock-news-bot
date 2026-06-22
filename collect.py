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


def _gnews_url(query: str, lang: str) -> str:
    """구글 뉴스 검색 RSS (Bing보다 신선함). 발췌문은 제공 안 함."""
    q = urllib.parse.quote(query)
    if lang == "ko":
        return f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


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


def _clean_title(entry) -> str:
    """제목 정리 + 구글뉴스 끝의 ' - 언론사' 꼬리표 제거."""
    title = _clean_text(entry.get("title", ""))
    source = _source(entry)
    if source and title.endswith(f" - {source}"):
        title = title[: -(len(source) + 3)]
    return title


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
        feed = feedparser.parse(_gnews_url(query, lang))
        entries = sorted(feed.entries, key=lambda e: _published(e) or _EPOCH, reverse=True)

        def to_item(e):
            return {
                "title": _clean_title(e),
                "excerpt": "",  # 구글 뉴스는 본문 발췌문 미제공
                "source": _source(e),
                "link": e.get("link", "").strip(),
                "published": _published(e),
                "region": region,
                "lang": lang,
            }

        # 최근 LOOKBACK_HOURS 이내 기사만 (오래된 기사는 섞지 않음)
        recent = [to_item(e) for e in entries if (_published(e) or _EPOCH) >= cutoff]
        results.append({"label": label, "region": region, "items": recent[:max_items]})

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


def bloomberg_items(feeds, max_items: int) -> list[dict]:
    """블룸버그 공식 RSS에서 최신 기사 — 제목 + 퍼블리셔 제공 요약 + 링크.
    (스크래핑 아님: 블룸버그가 신디케이션용으로 공개한 피드)"""
    entries = []
    for url in feeds:
        for e in feedparser.parse(url).entries:
            entries.append(e)
    entries.sort(key=lambda e: _published(e) or _EPOCH, reverse=True)

    out, seen = [], set()
    for e in entries:
        title = _clean_text(e.get("title", ""))
        key = title.replace(" ", "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({
            "title": title,
            "excerpt": _clean_text(e.get("summary", "")),  # 피드 제공 요약(그대로)
            "source": "Bloomberg",
            "link": e.get("link", "").strip(),
            "published": _published(e),
            "region": "해외",
            "lang": "en",
        })
        if len(out) >= max_items:
            break
    return out


def build_source_links(pool: list[dict], per_region: int) -> dict:
    """맨 끝 Source 모음 — 지역별 최신 기사 (제목, 링크) 상위 N개."""
    ordered = sorted(pool, key=lambda it: it["published"] or _EPOCH, reverse=True)
    groups = {"국내": [], "해외": []}
    seen = set()
    for it in ordered:
        region = it["region"]
        link = it.get("link")
        if region not in groups or len(groups[region]) >= per_region or not link:
            continue
        key = it["title"].replace(" ", "")
        if not key or key in seen:
            continue
        seen.add(key)
        groups[region].append((it["title"], link))
        if all(len(v) >= per_region for v in groups.values()):
            break
    return groups


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

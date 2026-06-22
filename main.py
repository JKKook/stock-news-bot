"""국내·해외 주식 이슈를 모아 디스코드로 보내는 메인 스크립트.

12시간마다 GitHub Actions가 이 파일을 실행합니다.
로컬 테스트:  .venv/bin/python main.py
"""

from datetime import datetime, timezone, timedelta

import config
from collect import collect, build_headlines, yahoo_headline, bloomberg_items, build_source_links
from market import get_indices, get_fear_greed
from issues import filter_issues
from translate import translate_items, translate_text
from notify import build_messages, send


def _flatten_sectors():
    """{섹터: [(검색어, 언어, 지역)]} → [(섹터, 검색어, 언어, 지역), ...]"""
    queries = []
    for sector, qs in config.SECTORS.items():
        for query, lang, region in qs:
            queries.append((sector, query, lang, region))
    return queries


def main() -> None:
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    today = f"{kst:%Y-%m-%d}"
    header = f"📰 **주식 이슈 브리핑** — {kst:%Y-%m-%d %H:%M} (KST)"

    print("시세·뉴스 수집 중...")
    indices = get_indices(config.INDICES)
    fear_greed = get_fear_greed()
    yahoo = yahoo_headline()
    market = collect(config.MARKET_QUERIES, config.MAX_MARKET)
    sectors = collect(_flatten_sectors(), config.MAX_SECTOR)
    tickers = collect(config.TICKERS, config.TICKER_CANDIDATES)

    # 5) 관심 종목은 '특정 이슈' 기사만 선별 (이슈 없는 종목은 표시 안 함)
    tickers = filter_issues(tickers, config.MAX_TICKER)

    # 블룸버그 공식 RSS (해외 섹션용)
    bloomberg = bloomberg_items(config.BLOOMBERG_FEEDS, config.MAX_BLOOMBERG)

    # 표시할 모든 기사를 모아 영어 → 한국어 번역
    pool = [it for g in (market + sectors + tickers) for it in g["items"]] + bloomberg
    print(f"번역 중... (총 {len(pool)}건)")
    translate_items(pool)
    if yahoo:
        yahoo["title"] = translate_text(yahoo["title"])

    if not pool:
        print("표시할 뉴스가 없어 발송을 건너뜁니다.")
        return

    # 헤드라인 + Source 링크: (번역된) 기사를 지역별 최신순으로
    headlines = build_headlines(pool, config.HEADLINE_PER_REGION, config.HEADLINE_MAX_LEN)
    source_links = build_source_links(pool, config.SOURCE_PER_REGION)

    messages = build_messages(header, today, indices, fear_greed, yahoo, headlines,
                              market, sectors, tickers, bloomberg, source_links)
    send(messages)


if __name__ == "__main__":
    main()

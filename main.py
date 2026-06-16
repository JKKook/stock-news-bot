"""국내·해외 주식 이슈를 모아 디스코드로 보내는 메인 스크립트.

12시간마다 GitHub Actions가 이 파일을 실행합니다.
로컬 테스트:  .venv/bin/python main.py
"""

from datetime import datetime, timezone, timedelta

import config
from collect import collect, build_headlines, yahoo_headline
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
    header = f"📰 **주식 이슈 브리핑** — {kst:%Y-%m-%d %H:%M} (KST)"

    print("뉴스 수집 중...")
    yahoo = yahoo_headline()
    market = collect(config.MARKET_QUERIES, config.MAX_MARKET)
    sectors = collect(_flatten_sectors(), config.MAX_SECTOR)
    tickers = collect(config.TICKERS, config.MAX_TICKER)

    # 헤드라인: 모든 기사를 모아 최신순 상위 N개
    pool = [it for g in (market + sectors + tickers) for it in g["items"]]
    headlines = build_headlines(pool, config.HEADLINE_COUNT, config.HEADLINE_MAX_LEN)

    if not pool:
        print("최근 새 뉴스가 없어 발송을 건너뜁니다.")
        return

    messages = build_messages(header, yahoo, headlines, market, sectors, tickers)
    send(messages)


if __name__ == "__main__":
    main()

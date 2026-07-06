"""국내·해외 주식 이슈를 모아 디스코드로 보내는 메인 스크립트.

12시간마다 GitHub Actions가 이 파일을 실행합니다.
로컬 테스트:  .venv/bin/python main.py
"""

import os
from datetime import datetime, timezone, timedelta

import config
from collect import (collect, build_headlines, yahoo_headline, bloomberg_items,
                     build_source_links, dedupe_all)
from market import get_indices, get_fear_greed, get_quotes
from catalysts import get_catalysts
from issues import filter_issues
from translate import translate_items, translate_text
from summarize import summarize
from measure import log_reversals, score_and_report
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

    # 주말(토/일)·공휴일(KST)엔 정규 브리핑 생략 — 기사는 속보(alerts.py)만 제공.
    #   FORCE_BRIEFING=1 이면 강제 발송(수동 실행·테스트용).
    if os.environ.get("FORCE_BRIEFING") != "1" and (
            kst.weekday() >= 5 or f"{kst:%m-%d}" in config.KR_HOLIDAYS):
        print("주말/공휴일(KST) — 정규 브리핑 생략. 속보는 alerts.py가 담당합니다.")
        return

    print("시세·뉴스 수집 중...")
    indices = get_indices(config.INDICES)
    fear_greed = get_fear_greed()
    yahoo = yahoo_headline()
    market = collect(config.MARKET_QUERIES, config.MAX_MARKET)
    sectors = collect(_flatten_sectors(), config.MAX_SECTOR)
    tickers = collect(config.TICKERS, config.TICKER_CANDIDATES)

    # 5) 관심 종목은 '특정 이슈' 기사만 선별 (이슈 없는 종목은 표시 안 함)
    tickers = filter_issues(tickers, config.MAX_TICKER)

    # 5-1) 전 관심종목 시세 조회 — 개별 줄(뉴스 있는 종목) + 요약표(전 종목)에 사용
    #      with_flow=True: 국내주는 네이버에서 외국인·기관 순매매도 함께(브리핑 전용)
    quotes = get_quotes(config.TICKER_SYMBOLS, with_flow=True)

    # 5-2) 다가오는 촉매(경제지표 + 실적) 조회 (P0-4: FMP, 키 없으면 빈 결과)
    catalysts = get_catalysts()

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

    # 번역된 제목 기준으로 브리핑 전체 중복 기사 제거(같은 사건 한 번만)
    dedupe_all(market, sectors, tickers, bloomberg)

    # 헤드라인 + Source 링크: (번역된) 기사를 지역별 최신순으로
    headlines = build_headlines(pool, config.HEADLINE_PER_REGION, config.HEADLINE_MAX_LEN)
    # (P4-2) 의미 기반 근접 중복 제거 — 토큰 dedup이 못 잡은 '다른 표현·같은 사건'을 임베딩으로
    from semantic import keep_indices
    for region, lst in headlines.items():
        keep = keep_indices(lst)
        headlines[region] = [t for i, t in enumerate(lst) if i in keep]
    source_links = build_source_links(pool, config.SOURCE_PER_REGION)

    # 🧭 so-what 요약 — 품질 필터된 헤드라인만 Claude에 넘겨 3줄 종합 (키 없으면 None)
    print("AI 요약 중...")
    summary = summarize(headlines)

    # (R1) 정확도 측정 — 오늘 되돌림 신호 기록 후, 만기된 과거 신호를 오늘 가격으로 채점·리포트
    #   (로깅 먼저 → 오늘 신호도 '검증 대기'로 즉시 집계되어 루프 작동이 바로 보임)
    log_reversals(quotes)
    accuracy = score_and_report(quotes)

    messages = build_messages(header, today, indices, fear_greed, yahoo, headlines,
                              market, sectors, tickers, bloomberg, source_links, quotes,
                              catalysts, summary, accuracy)
    send(messages)


if __name__ == "__main__":
    main()

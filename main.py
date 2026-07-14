"""국내·해외 주식 이슈를 모아 디스코드로 보내는 메인 스크립트.

12시간마다 GitHub Actions가 이 파일을 실행합니다.
로컬 테스트:  .venv/bin/python main.py
"""

import os
from datetime import datetime, timezone, timedelta

import config
from collect import (collect, build_headlines, yahoo_headline, bloomberg_items,
                     build_source_links, dedupe_all)
from market import (get_indices, get_fear_greed, get_quotes, kr_market_flow,
                    get_kr_futures, label_futures)
from catalysts import get_catalysts
from issues import filter_issues
from translate import translate_items, translate_text
from summarize import summarize, market_context
from measure import log_reversals, score_and_report
from movers import get_movers
from notify import build_messages, build_market_note, send


def _flatten_sectors():
    """{섹터: [(검색어, 언어, 지역)]} → [(섹터, 검색어, 언어, 지역), ...]"""
    queries = []
    for sector, qs in config.SECTORS.items():
        for query, lang, region in qs:
            queries.append((sector, query, lang, region))
    return queries


# 리서치 노트 헤더 — (focus, kind) → 제목 (미래에셋 '마켓 뷰/마켓 클로징' 형식 참고)
_TITLES = {
    ("KR", "view"):    "📑 **[마켓 뷰] 코스피·코스닥 개장 전**",
    ("KR", "closing"): "📑 **[마켓 클로징] 코스피·코스닥 마감**",
    ("US", "view"):    "📑 **[마켓 뷰] 나스닥 개장**",
    ("US", "closing"): "📑 **[마켓 클로징] 나스닥 마감**",
}


def _focus_filter(region, sectors, tickers, headlines, quotes, catalysts, market_flow):
    """(시장별 브리핑) 해당 시장 섹션만 남긴다 — 뉴스·테마·관심종목·촉매·수급.
    지수 대시보드는 필터하지 않는다(밤사이 미국 흐름이 국내 개장 방향을 좌우하므로 맥락 필수)."""
    reg_of = {lbl: r for lbl, _, _, r in config.TICKERS}
    sectors = [g for g in sectors if g.get("region") == region]
    tickers = [g for g in tickers if g.get("region") == region]
    headlines = {region: headlines.get(region, [])}
    quotes = {k: v for k, v in (quotes or {}).items() if reg_of.get(k) == region}
    if catalysts:
        cc = "KR" if region == "국내" else "US"
        catalysts = {
            "economic": [e for e in catalysts.get("economic", []) if e.get("country") == cc],
            "earnings": [e for e in catalysts.get("earnings", []) if reg_of.get(e["name"]) == region],
        }
    if region == "해외":
        market_flow = {}        # 국내 수급은 국내 브리핑에만
    return sectors, tickers, headlines, quotes, catalysts, market_flow


_DOMAIN_SRC = __import__("re").compile(r"^[\w.-]+\.(net|com|kr|org|io|co\.kr)$", __import__("re").I)


def _headline_sources(headlines, pool, limit: int = 3) -> list[str]:
    """AI 기사 요약의 '참고' 출처 — 헤드라인으로 쓰인 기사들의 언론사(중복 제거 상위 N).
    실제 기사에서 뽑으므로 출처를 지어내지 않는다.
    구글뉴스가 언론사명 대신 도메인(v.daum.net 등)을 주는 경우는 제외한다."""
    out = []
    for lst in headlines.values():
        for h in lst:
            base = h.rstrip("…").strip()[:20]
            if not base:
                continue
            for it in pool:
                src = (it.get("source") or "").strip()
                if not src or _DOMAIN_SRC.match(src):   # 도메인 표기는 출처로 부적합
                    continue
                if it.get("title", "").startswith(base) and src not in out:
                    out.append(src)
                    break
    return out[:limit]


def main() -> None:
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    today = f"{kst:%Y-%m-%d}"
    # 시장별 브리핑 모드 — BRIEF_FOCUS=KR|US(없으면 전체), BRIEF_KIND=view|closing
    focus = (os.environ.get("BRIEF_FOCUS") or "").upper()
    kind = (os.environ.get("BRIEF_KIND") or "").lower()
    region = {"KR": "국내", "US": "해외"}.get(focus)
    title = _TITLES.get((focus, kind), "📰 **주식 이슈 브리핑**")
    header = f"{title} — {kst:%Y-%m-%d %H:%M} (KST)"

    # 휴장일엔 생략 — 국내 브리핑은 KST 주말·공휴일, 미국 브리핑은 ET 주말 기준(서머타임 자동).
    #   FORCE_BRIEFING=1 이면 강제 발송(수동 실행·테스트용).
    if os.environ.get("FORCE_BRIEFING") != "1":
        if focus == "US":
            from zoneinfo import ZoneInfo
            et = datetime.now(ZoneInfo("America/New_York"))
            if et.weekday() >= 5:
                print("미국 휴장(ET 주말) — 나스닥 브리핑 생략.")
                return
        elif kst.weekday() >= 5 or config.is_kr_holiday(kst.date()):
            print("주말/공휴일(KST) — 정규 브리핑 생략. 속보는 alerts.py가 담당합니다.")
            return

    print("시세·뉴스 수집 중...")
    indices = get_indices(config.INDICES)
    # (R6) 코스피200 선물(야간 세션 반영)을 코스닥 뒤에 끼워 대시보드에 표시
    kf = get_kr_futures()
    if kf:
        pos = next((i for i, x in enumerate(indices) if x["name"] == "코스닥"), len(indices) - 1)
        indices.insert(pos + 1, kf)
    # 선물은 세션에 따라 '○○ 야간선물' / '○○ 선물(정규장)'로 표기 — 현물 지수와 확실히 구분
    label_futures(indices, kst)
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
    # (R4) 의미 dedup을 렌더 섹션(관심종목·테마 뉴스)까지 확대 — 토큰이 못 잡은 근접중복 제거
    #      우선순위: 관심종목 > 테마(앞선 것을 대표로 남김)
    from semantic import keep_indices, dedupe_groups
    dedupe_groups([tickers, sectors])

    # 헤드라인 + Source 링크: (번역된) 기사를 지역별 최신순으로
    headlines = build_headlines(pool, config.HEADLINE_PER_REGION, config.HEADLINE_MAX_LEN)
    # (P4-2) 의미 기반 근접 중복 제거 — 토큰 dedup이 못 잡은 '다른 표현·같은 사건'을 임베딩으로
    for _rg, lst in headlines.items():          # ⚠️ region(포커스 변수)과 이름 겹치지 않게
        keep = keep_indices(lst)
        headlines[_rg] = [t for i, t in enumerate(lst) if i in keep]
    source_links = build_source_links(pool, config.SOURCE_PER_REGION)

    # (R5) 국내 시장 수급 — 코스피·코스닥 투자자별 순매매(개인/외국인/기관)
    #   한 줄 총평(verdict)의 근거로도 쓰이므로 요약보다 먼저 조회한다.
    market_flow = kr_market_flow()

    # (시장별 브리핑) 해당 시장 섹션만 남긴다 — 요약·총평도 필터된 내용 기준으로 생성
    if region:
        sectors, tickers, headlines, quotes, catalysts, market_flow = _focus_filter(
            region, sectors, tickers, headlines, quotes, catalysts, market_flow)
        print(f"시장별 브리핑: {region} ({kind or 'regular'})")

    # 🧭 so-what 요약 + 🧠 한 줄 총평 — 헤드라인 + 오늘의 시장 데이터를 근거로 (키 없으면 None)
    print("AI 요약 중...")
    summary = summarize(headlines, market_context(indices, fear_greed, market_flow, quotes, region), kind)

    # (R1) 정확도 측정 — 오늘 되돌림 신호 기록 후, 만기된 과거 신호를 오늘 가격으로 채점·리포트
    #   (로깅 먼저 → 오늘 신호도 '검증 대기'로 즉시 집계되어 루프 작동이 바로 보임)
    log_reversals(quotes)
    accuracy = score_and_report(quotes)

    if region:
        # 📑 마켓 뷰 / 마켓 클로징 — 시장 리서치 노트(정규 브리핑과 중복 없음)
        print("시장 급등·급락 종목 수집 중...")
        movers = get_movers(region)
        sources = _headline_sources(headlines, pool)   # AI 기사 요약의 '참고' 출처
        messages = build_market_note(header, region, indices, summary, movers,
                                     catalysts, kind, sources)
    else:
        # 📰 정규 브리핑 — 관심종목·테마 중심 종합본
        messages = build_messages(header, today, indices, fear_greed, yahoo, headlines,
                                  market, sectors, tickers, bloomberg, source_links, quotes,
                                  catalysts, summary, accuracy, market_flow)
    send(messages)


if __name__ == "__main__":
    main()

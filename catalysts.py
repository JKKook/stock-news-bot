"""경제·실적 촉매 캘린더 (P0-4).

다가오는 '예정 촉매'를 모아 브리핑 상단에 보여준다 — 투자 판단의 앵커는 예정 이벤트이므로.

· 실적:   yfinance (관심종목 전 종목, 무료·키 불필요). 종목별 다가오는 실적 발표일.
· 경제지표: FRED(미 세인트루이스 연준) 공식 릴리스 발표일 — CPI·고용·GDP 등.
           FRED_API_KEY 없거나 실패 시 경제지표는 빈 값(봇 안 멈춤).

FMP는 2025-08 무료 티어 축소로 경제 캘린더 차단·소형주 실적 누락 → 사용하지 않음.
"""

import os
from datetime import datetime, timezone, timedelta

import requests

import config

_FRED_KEY = os.environ.get("FRED_API_KEY")
_FRED_URL = "https://api.stlouisfed.org/fred/release/dates"

_IMPACT_RANK = {"Low": 1, "Medium": 2, "High": 3}


def _watchlist_symbols() -> dict:
    """{야후심볼: 표시이름} — 미국·국내 관심종목 모두 (실적 조회용). 비상장(None) 제외.
    (D-2: 국내 종목 실적일도 yfinance로 조회 — .KS도 실적 발표일 반환)"""
    return {sym: label for label, sym in config.TICKER_SYMBOLS.items() if sym}


def _kst_today():
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date()


def _kr_business_day(year: int, month: int, n: int):
    """해당 월의 n번째 영업일(주말·KR_HOLIDAYS 제외). 못 찾으면 None."""
    from datetime import date
    d = date(year, month, 1)
    count = 0
    while d.month == month:
        if d.weekday() < 5 and f"{d:%m-%d}" not in config.KR_HOLIDAYS:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)
    return None


def _kr_cpi_dates(today, horizon) -> list:
    """(R3) 통계청 소비자물가동향 발표 추정일 — 매월 2번째 영업일(전월 CPI 발표).
    today~horizon 에 걸치는 달만. 무료 발표일 API 부재를 알고리즘 패턴으로 보강(잠정)."""
    out = []
    y, m = today.year, today.month
    for _ in range(4):   # 최대 4개월 앞까지
        bd = _kr_business_day(y, m, 2)
        if bd and today <= bd <= horizon:
            out.append(bd)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def releases_between(release_id: int, frm: str, to: str) -> list[str]:
    """(매크로 속보) FRED release_id 의 발표일 중 [frm, to] 문자열 범위. 키없음/실패 시 []."""
    if not _FRED_KEY:
        return []
    try:
        r = requests.get(_FRED_URL, timeout=12, params={
            "release_id": release_id, "api_key": _FRED_KEY, "file_type": "json",
            "include_release_dates_with_no_data": "true", "sort_order": "asc",
        })
        r.raise_for_status()
        return [d["date"] for d in r.json().get("release_dates", [])
                if frm <= d.get("date", "") <= to]
    except Exception:
        return []


def economic_events() -> list[dict]:
    """앞으로 CATALYST_DAYS_AHEAD 일 내 경제지표 발표일.
    · 미국: FRED 공식 릴리스(FRED_API_KEY 있을 때).
    · 한국: 무료 미래 발표일 API가 없어 config.KR_ECONOMIC_EVENTS 큐레이션 사용."""
    today = _kst_today()
    horizon = today + timedelta(days=config.CATALYST_DAYS_AHEAD)
    frm, to = today.isoformat(), horizon.isoformat()
    out = []

    # 미국 — FRED
    if _FRED_KEY:
        for rid, label, impact in config.FRED_RELEASES:
            try:
                r = requests.get(_FRED_URL, timeout=12, params={
                    "release_id": rid, "api_key": _FRED_KEY, "file_type": "json",
                    "include_release_dates_with_no_data": "true", "sort_order": "asc",
                })
                r.raise_for_status()
                for d in r.json().get("release_dates", []):
                    if frm <= d.get("date", "") <= to:
                        out.append({"date": d["date"], "event": label,
                                    "impact": impact, "country": "US"})
            except Exception:
                continue

    # 한국 — kr_calendar.json(금통위 등, confirmed 플래그) + CPI 알고리즘 계산
    for e in config.KR_ECONOMIC_EVENTS:
        d = e.get("date", "")
        if frm <= d <= to:
            name = e["name"] if e.get("confirmed") else e["name"] + " (잠정)"
            out.append({"date": d, "event": name,
                        "impact": e.get("impact", "Medium"), "country": "KR"})
    # CPI 추정일 보강 — 같은 달에 이미 CPI가 등록돼 있으면(확정일) 건너뜀
    cpi_months = {o["date"][:7] for o in out if o["country"] == "KR" and "CPI" in o["event"]}
    for bd in _kr_cpi_dates(today, horizon):
        ds = bd.isoformat()
        if frm <= ds <= to and ds[:7] not in cpi_months:
            out.append({"date": ds, "event": "한국 소비자물가 CPI (잠정)",
                        "impact": "High", "country": "KR"})

    # 날짜 오름차순, 같은 날은 임팩트 높은 순
    out.sort(key=lambda x: (x["date"], -_IMPACT_RANK.get(x["impact"], 0)))
    return out[:config.CATALYST_MAX_ECON]


def earnings_events() -> list[dict]:
    """앞으로 CATALYST_EARN_DAYS 일 내 관심종목(미국+국내) 실적 발표일 (yfinance)."""
    symbols = _watchlist_symbols()
    if not symbols:
        return []
    import yfinance as yf  # 무거운 import는 필요할 때만
    today = _kst_today()
    horizon = today + timedelta(days=config.CATALYST_EARN_DAYS)
    out = []
    for sym, name in symbols.items():
        try:
            cal = yf.Ticker(sym).calendar or {}
            ed = cal.get("Earnings Date")
            if not ed:
                continue
            # datetime→date 정규화(타입 불일치 비교 에러로 전 종목 누락되는 것 방지) + stale(과거)·원거리 제외
            dates = []
            for d in (ed if isinstance(ed, list) else [ed]):
                d = d.date() if isinstance(d, datetime) else d
                if today <= d <= horizon:
                    dates.append(d)
            if dates:
                out.append({"date": min(dates).isoformat(), "name": name})
        except Exception:
            continue
    out.sort(key=lambda x: x["date"])
    return out[:config.CATALYST_MAX_EARN]


def get_catalysts() -> dict:
    """{'economic': [...], 'earnings': [...]}.
    경제지표는 FRED_API_KEY 있을 때만, 실적은 항상(yfinance)."""
    return {"economic": economic_events(), "earnings": earnings_events()}

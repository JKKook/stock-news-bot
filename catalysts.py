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

    # 한국 — 큐레이션(무료 API 부재). (날짜, 라벨, 임팩트) 튜플
    for d, label, impact in config.KR_ECONOMIC_EVENTS:
        if frm <= d <= to:
            out.append({"date": d, "event": label, "impact": impact, "country": "KR"})

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

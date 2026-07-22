"""시장 급등·급락 주요 종목 (Up & Down) — 관심종목이 아니라 '시장이 주목한 종목'.

· 국내: 네이버 모바일 랭킹 API (코스피 급등·급락 중 시총 상위 — 코스닥 제외, 대형주 우선)
        ETF/ETN·우선주 제외 + 거래대금 하한으로 잡주/레버리지 노이즈를 걸러 '의미 있는 움직임'만.
· 해외: 야후 screener (day_gainers / day_losers) — 나스닥 상장 + 시총 하한, 시총 상위 우선(NYSE·마이크로캡 제외).
        표기는 티커(예: NBIS)를 타이틀로 — 미국 종목은 티커로 검색·식별하는 게 일반적.
· 이유: 종목명으로 구글뉴스를 검색해 가장 최신 헤드라인 1건을 '왜 움직였나'로 붙인다(해외는 한국어 번역).
· 우선순위: 같은 급등·급락 풀에서 '시총이 큰 종목'을 먼저 — 시장 영향력이 큰 움직임이 정보성↑.

모두 무료·무인증. 실패 시 빈 결과(그레이스풀) — 섹션만 생략되고 봇은 멈추지 않는다.
"""

import re
import urllib.parse

import feedparser
import requests

import config
from collect import _clean_title, _published, _EPOCH

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
_MOBILE = {**_UA, "Referer": "https://m.stock.naver.com/"}
_PREF = re.compile(r"(우|우B|우C|\d+우B?)$")     # 우선주 접미사


def _num(s):
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return None


def _kr_side(side: str, market: str) -> list[dict]:
    """네이버 랭킹에서 한쪽(up/down) 종목 풀 — ETF/ETN·우선주·저유동성 제외, 시총 부착."""
    out = []
    try:
        r = requests.get(f"https://m.stock.naver.com/api/stocks/{side}/{market}",
                         headers=_MOBILE, timeout=8,
                         params={"page": 1, "pageSize": 40})
        for s in r.json().get("stocks", []):
            if s.get("stockEndType") != "stock":          # ETF·ETN·레버리지 제외
                continue
            name = (s.get("stockName") or "").strip()
            if not name or _PREF.search(name):            # 우선주 제외
                continue
            value = _num(s.get("accumulatedTradingValue"))   # 거래대금(백만원)
            if value is None or value < config.MOVERS_KR_MIN_VALUE:
                continue                                   # 거래대금 미달 = 잡주
            chg = _num(s.get("fluctuationsRatio"))
            if chg is None:
                continue
            mcap = _num(s.get("marketValue")) or 0         # 시총(억원) — 우선순위 정렬용
            out.append({"name": name, "chg": chg, "market": market, "mcap": mcap})
    except Exception:
        return []
    return out


def kr_movers(n: int) -> dict:
    """코스피 급등/급락 중 '시총 상위' n종목 — 대형주 우선(시장 영향력 큰 움직임)."""
    up = _kr_side("up", "KOSPI")
    down = _kr_side("down", "KOSPI")
    up.sort(key=lambda x: -x["mcap"])       # 시총 큰 순 (예: 하이닉스 > 두산)
    down.sort(key=lambda x: -x["mcap"])
    return {"up": up[:n], "down": down[:n]}


_NASDAQ = {"NMS", "NGM", "NCM"}   # Nasdaq Global Select / Global Market / Capital Market


def _us_side(scr: str) -> list[dict]:
    """야후 screener 한쪽 풀 — 나스닥 상장 + 시총 하한, 티커·시총 부착(NYSE·마이크로캡 제외)."""
    out = []
    try:
        r = requests.get("https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved",
                         headers=_UA, timeout=10, params={"scrIds": scr, "count": 50})
        for q in r.json()["finance"]["result"][0].get("quotes", []):
            if q.get("exchange") not in _NASDAQ:              # 나스닥 상장만
                continue
            mcap = q.get("marketCap") or 0
            if mcap < config.MOVERS_US_MIN_MCAP:
                continue                                       # 시총 하한(마이크로캡 제외)
            chg = q.get("regularMarketChangePercent")
            symbol = q.get("symbol")
            if chg is None or not symbol:
                continue
            company = (q.get("shortName") or symbol).split(" - ")[0][:28]
            # 표기 타이틀은 '티커'(미국 종목은 티커로 검색·식별). 회사명은 뉴스 검색용으로 보관.
            out.append({"name": symbol, "symbol": symbol, "company": company,
                        "chg": float(chg), "mcap": float(mcap)})
    except Exception:
        return []
    return out


def us_movers(n: int) -> dict:
    up = _us_side("day_gainers")
    down = _us_side("day_losers")
    up.sort(key=lambda x: -x["mcap"])       # 시총 큰 순 (예: 대형주 > 마이크로캡)
    down.sort(key=lambda x: -x["mcap"])
    return {"up": up[:n], "down": down[:n]}


# '왜 움직였나'로 부적합한 기사 — 자동 시세봇·정기 분석글(사건을 설명하지 못함)
_REASON_EXCLUDE = [
    # 한국어 — 자동 시세/수급/분석 봇
    "투자분석", "주가 흐름", "주가 동향", "기술적 분석", "종목분석", "차트 분석",
    "주간 시황", "증시 캘린더", "공매도 현황", "외국인 순매수 상위", "기관 순매수 상위",
    "옵션 체인", "시세 및 뉴스", "변동폭이 가장 큰 종목", "투자 추천 등급",
    # 영어 — 애널리스트 레이팅·13F 보유지분·시세 페이지 봇(사건을 설명 못 함)
    "options chain", "stock quote", "price target", "technical analysis",
    "average recommendation", "analyst rating", "consensus rating", "brokerages",
    "shares of", "stake in", "position in", "holdings in", "buys shares", "sells shares",
    "13f", "institutional investor", "short interest", "biggest movers", "movers:",
    "here's what", "what you need to know", "moving in", "moving on",
]
_BOT_TITLE = re.compile(
    r"\d+\s*월\s*\d+\s*일"           # "7월 7일 장중 8,070원…" 자동 시세봇
    r"|장중\s*[\d,]+원"
    r"|^\d+일,\s*(외국인|기관|개인)"   # "06일, 외국인 코스닥에서 …" 자동 수급봇
    r"|[\d,]{4,}\s*(shares|주)를?\s*(매입|매도)?"  # "196,488 Shares of…" 13F 보유지분 봇
    , re.IGNORECASE)


def _reason(query: str, lang: str) -> str:
    """종목명으로 구글뉴스 검색 → '왜 움직였나'를 설명하는 최신 헤드라인 1건.
    최근 기사만 + 자동 시세봇/정기분석글 제외 → 사건성 기사만 남긴다. 없으면 빈 문자열."""
    from datetime import datetime, timezone, timedelta
    try:
        q = urllib.parse.quote(query)
        url = (f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko" if lang == "ko"
               else f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en")
        entries = feedparser.parse(url).entries
        now = datetime.now(timezone.utc)
        cands = []
        for e in entries[:15]:
            title = _clean_title(e)
            if not title:
                continue
            pub = _published(e)
            if pub is None or (now - pub) > timedelta(hours=config.MOVERS_REASON_HOURS):
                continue                                   # 오래된 기사는 오늘 움직임의 이유가 아님
            low = title.lower()
            if any(x.lower() in low for x in _REASON_EXCLUDE) or _BOT_TITLE.search(title):
                continue                                   # 자동 시세봇·정기 분석글 제외
            cands.append((pub, title))
        if not cands:
            return ""
        cands.sort(reverse=True)
        best = cands[0][1]
        return best if len(best) <= 70 else best[:69].rstrip() + "…"
    except Exception:
        return ""


def add_reasons(movers: dict, lang: str) -> None:
    """각 종목에 'reason'(사건성 뉴스 헤드라인) 부착 — 해외는 한국어로 번역. 실패 시 빈 문자열."""
    from translate import translate_text
    for side in ("up", "down"):
        for m in movers.get(side, []):
            # 검색은 회사명(뉴스 매칭↑), 표기 타이틀은 티커 — 국내는 종목명 그대로.
            key = m["name"] if lang == "ko" else (m.get("company") or m.get("symbol") or m["name"])
            r = _reason(f"{key} 주가" if lang == "ko" else f"{key} stock", lang)
            if r and lang != "ko":
                try:
                    r = translate_text(r)
                except Exception:
                    pass
            m["reason"] = r


def get_movers(region: str) -> dict:
    """지역별 Up&Down + 이유. region: '국내' | '해외'. 실패 시 {'up':[], 'down':[]}."""
    n = config.MOVERS_COUNT
    if region == "국내":
        mv = kr_movers(n)
        add_reasons(mv, "ko")
    else:
        mv = us_movers(n)
        add_reasons(mv, "en")
    return mv

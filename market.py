"""실시간 시세 데이터 — 주요 지수(야후) + CNN 공포탐욕지수 (무료, API 키 불필요)."""

import re
import html
import statistics

import requests

import config

_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.cnn.com/",
}
_CNN = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"


def get_indices(symbols) -> list[dict]:
    """[(이름, 야후심볼, 국기), ...] → [{name, flag, price, chg(%)}].
    실패한 종목은 건너뜀(빈 섹션 방지는 호출부에서 처리)."""
    out = []
    for name, sym, flag in symbols:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
            r = requests.get(url, headers=_UA, timeout=10)
            meta = r.json()["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            if price is None or not prev:
                continue
            out.append({
                "name": name,
                "flag": flag,
                "price": price,
                "chg": (price - prev) / prev * 100,
            })
        except Exception:
            continue
    return out


_NAVER_UA = {"User-Agent": _UA["User-Agent"], "Referer": "https://finance.naver.com/"}


def _to_int(s: str):
    try:
        return int(s.replace(",", "").replace("+", ""))
    except Exception:
        return None


def _kr_flow(sym: str) -> dict:
    """(P2-7 재시도) 네이버 금융 개별종목 최근 거래일 외국인·기관 순매매(주식수).
    표 열: 날짜·종가·전일비·등락률·거래량·기관순매매·외국인순매매·외국인보유주·보유율.
    무료·무인증. .KS/.KQ 국내주만. 실패·형식변경 시 빈 dict(그레이스풀).
    ⚠️ 스크래핑이라 레이아웃 변경에 취약 — 값 없으면 조용히 생략."""
    m = re.match(r"(\d{6})\.(KS|KQ)$", sym)
    if not m:
        return {}
    try:
        r = requests.get(f"https://finance.naver.com/item/frgn.naver?code={m.group(1)}",
                         headers=_NAVER_UA, timeout=8)
        r.encoding = "euc-kr"
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
            tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
            cells = [html.unescape(re.sub("<[^>]+>", "", x)).strip().replace("\xa0", "")
                     for x in tds]
            if len(cells) >= 7 and re.match(r"\d{4}\.\d{2}\.\d{2}$", cells[0]):
                inst, foreign = _to_int(cells[5]), _to_int(cells[6])
                if inst is None or foreign is None:
                    return {}
                return {"inst_net": inst, "foreign_net": foreign, "flow_date": cells[0]}
    except Exception:
        return {}
    return {}


def get_quotes(symbols: dict, with_flow: bool = False) -> dict:
    """{종목명: 야후심볼} → {종목명: {price, chg(%), w52pos(%), currency}}.
    P0-1: 관심종목 뉴스에 price action(현재가·등락·52주 위치)을 붙이기 위함.
    심볼이 None(비상장)이거나 조회 실패한 종목은 결과에서 생략."""
    out = {}
    for label, sym in symbols.items():
        if not sym:
            continue
        try:
            # range=1mo·interval=1d → 현재가·52주(meta) + 일별 종가(볼린저용)를 1회로
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
            r = requests.get(url, headers=_UA, timeout=10,
                             params={"range": "1mo", "interval": "1d"})
            res = r.json()["chart"]["result"][0]
            meta = res["meta"]
            price = meta.get("regularMarketPrice")
            if price is None:
                continue
            try:
                closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
            except Exception:
                closes = []
            try:
                volumes = res["indicators"]["quote"][0]["volume"]
            except Exception:
                volumes = []
            # 라이브 시세가 일별 종가와 30%+ 어긋나면(야후 글리치) 최근 종가 사용
            if closes and closes[-1] and abs(price - closes[-1]) / closes[-1] > 0.30:
                price = closes[-1]
            # 전일 종가 = 일별 시리즈의 직전 세션 종가.
            #   (range=1mo 라 meta.chartPreviousClose 는 '한 달 전'이라 쓸 수 없음)
            prev = closes[-2] if len(closes) >= 2 else (meta.get("previousClose")
                                                        or meta.get("chartPreviousClose"))
            if not prev:
                continue
            hi, lo = meta.get("fiftyTwoWeekHigh"), meta.get("fiftyTwoWeekLow")
            w52pos = (price - lo) / (hi - lo) * 100 if (hi and lo and hi > lo) else None
            out[label] = {
                "price": price,
                "chg": (price - prev) / prev * 100,
                "w52pos": w52pos,
                "currency": meta.get("currency", ""),
                "bb_pct": _bollinger_pct(closes, price),
                "vol_mult": _volume_mult(volumes),   # (P3-1) 평소 대비 거래량 배수
                **_yf_extra(sym),   # PER(trailing/forward) + 공매도(비율·증감)
                **(_kr_flow(sym) if with_flow else {}),   # (P2-7) 국내 외국인·기관 순매매
            }
        except Exception:
            continue
    return out


def _yf_extra(sym: str) -> dict:
    """yfinance info에서 판단용 지표 1회 조회 — PER + 미국 공매도 수급.
    · per_trailing/per_forward: 국내는 trailing 결측 잦아 forward로 폴백해 판단.
    · short_pct: 공매도 비율(float 대비 %), short_up: 전월 대비 공매도 증가 여부.
    실패/없음 필드는 None."""
    out = {"per_trailing": None, "per_forward": None, "short_pct": None, "short_up": None}
    try:
        import yfinance as yf   # 무거운 import는 필요할 때만
        info = yf.Ticker(sym).info
        out["per_trailing"] = info.get("trailingPE")
        out["per_forward"] = info.get("forwardPE")
        sp = info.get("shortPercentOfFloat")
        if sp is not None:
            out["short_pct"] = sp * 100          # yfinance는 비율(0.23) → %
        cur, prv = info.get("sharesShort"), info.get("sharesShortPriorMonth")
        if cur is not None and prv is not None:
            out["short_up"] = cur > prv           # 전월 대비 숏 증가(빌드업)/감소(커버)
    except Exception:
        pass
    return out


def _volume_mult(volumes: list):
    """(P3-1) 당일 거래량 ÷ 직전 세션 평균 = '평소 대비' 배수.
    같은 시세 조회의 일별 거래량을 재사용(추가 HTTP 없음). 표본 부족·0이면 None.
    가격 급변이 '거래량 동반(=참여 폭발)'인지 판단해 이례성/선반영 신호를 보강."""
    vols = [v for v in (volumes or []) if v]
    if len(vols) < 5:
        return None
    today, hist = vols[-1], vols[:-1]
    avg = statistics.fmean(hist)
    if avg <= 0:
        return None
    return today / avg


def _bollinger_pct(closes: list, price: float):
    """볼린저 %B — 최근 BB_PERIOD 일 종가로 SMA±BB_K×표준편차 밴드를 만들고
    현재가의 밴드 내 위치(%)를 반환. 하단=0·중심=50·상단=100, 밴드 밖은 <0 또는 >100.
    데이터 부족·계산 불가 시 None."""
    n = config.BB_PERIOD
    if len(closes) < n:
        return None
    window = closes[-n:]
    sma = statistics.fmean(window)
    std = statistics.pstdev(window)      # 모표준편차(볼린저 관례)
    if std <= 0:
        return None
    upper, lower = sma + config.BB_K * std, sma - config.BB_K * std
    return (price - lower) / (upper - lower) * 100


def get_fear_greed() -> dict | None:
    """CNN 공포탐욕지수 (0~100) + 어제/1주/1개월/1년 전 값."""
    try:
        r = requests.get(_CNN, headers=_UA, timeout=12)
        d = r.json()["fear_and_greed"]
        return {
            "score": d.get("score"),
            "rating": d.get("rating", ""),
            "prev": d.get("previous_close"),
            "week": d.get("previous_1_week"),
            "month": d.get("previous_1_month"),
            "year": d.get("previous_1_year"),
        }
    except Exception:
        return None

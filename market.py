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


# 국내 지수는 네이버(한국거래소 공식 시세)를 쓴다 — 야후는 국내지수 데이터가 부정확하다.
#   실측(2026-07-14): 야후 ^KQ11 previousClose 가 '2거래일 전' 값(837.43)이라 등락률을 -10.4%로
#   과대계산(실제 -5.3%). 실시간 가격도 야후 750.21 vs 네이버 756.83 으로 어긋났다.
_KR_INDEX_CODE = {"^KS11": "KOSPI", "^KQ11": "KOSDAQ"}


def _naver_index(code: str):
    """네이버 국내지수 — (현재가, 등락%, 등락포인트). 모두 부호 포함. 실패 시 None."""
    d = requests.get(f"https://m.stock.naver.com/api/index/{code}/basic",
                     headers=_MOBILE_UA, timeout=8).json()
    price = float(str(d["closePrice"]).replace(",", ""))
    chg = float(str(d["fluctuationsRatio"]).replace(",", ""))
    pt = float(str(d["compareToPreviousClosePrice"]).replace(",", ""))
    return price, chg, pt


def _yahoo_prev_close(res: dict, price: float):
    """전일 종가 — 야후 chartPreviousClose 를 믿지 말고 일별 종가 시리즈로 직접 구한다.
    (chartPreviousClose 가 stale 하게 들어오는 사례가 실측됨 → 등락률이 통째로 틀어짐)
    마지막 일봉이 '오늘 바'(현재가와 일치)면 그 직전 봉이 전일 종가, 아니면 마지막 봉이 전일 종가."""
    try:
        closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    except Exception:
        closes = []
    if len(closes) >= 2 and closes[-1] and abs(price - closes[-1]) / closes[-1] < 0.005:
        return closes[-2]        # 마지막 봉 = 오늘(진행중/마감) → 직전 봉이 전일 종가
    if closes:
        return closes[-1]        # 오늘 봉이 아직 없음 → 마지막 봉이 전일 종가
    meta = res.get("meta", {})
    return meta.get("chartPreviousClose") or meta.get("previousClose")


def get_indices(symbols) -> list[dict]:
    """[(이름, 야후심볼, 국기), ...] → [{name, flag, price, chg(%), chg_pt}].
    · 국내 지수(코스피·코스닥)는 네이버 공식 시세 사용(야후 부정확).
    · 해외 지수는 야후를 쓰되 전일 종가를 일별 시리즈로 재계산(야후 previousClose stale 방어).
    실패한 종목은 건너뜀(빈 섹션 방지는 호출부에서 처리)."""
    out = []
    for name, sym, flag in symbols:
        code = _KR_INDEX_CODE.get(sym)
        if code:                                   # 국내 지수 → 네이버
            try:
                price, chg, pt = _naver_index(code)
                out.append({"name": name, "flag": flag,
                            "price": price, "chg": chg, "chg_pt": pt})
                continue
            except Exception:
                pass                               # 실패 시 아래 야후 경로로 폴백
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
            r = requests.get(url, headers=_UA, timeout=10,
                             params={"range": "5d", "interval": "1d"})
            res = r.json()["chart"]["result"][0]
            price = res["meta"].get("regularMarketPrice")
            if price is None:
                continue
            prev = _yahoo_prev_close(res, price)   # chartPreviousClose 대신 시리즈 기준
            if not prev:
                continue
            out.append({
                "name": name,
                "flag": flag,
                "price": price,
                "chg": (price - prev) / prev * 100,
                "chg_pt": price - prev,     # 포인트 변화(리서치 노트 Summary 표기용)
            })
        except Exception:
            continue
    return out


_NAVER_UA = {"User-Agent": _UA["User-Agent"], "Referer": "https://finance.naver.com/"}
_MOBILE_UA = {"User-Agent": _UA["User-Agent"], "Referer": "https://m.stock.naver.com/"}


def index_session(name: str, kst) -> str:
    """지수·선물이 지금 어느 세션인지(KST 기준) — '정규장' / '야간선물' / '장마감(종가)'.
    · 국내 선물(코스피200): 주간 09:00~15:45 = 정규장, 18:00~익일 05:00 = 야간선물
    · 해외 선물(나스닥): 미 정규장(KST 약 22:30~06:00) = 정규장, 그 외 = 야간선물
    · 국내 현물(코스피·코스닥): 평일 09:00~15:30 = 정규장, 그 외 = 장마감(종가)
    · 해외 현물(나스닥·S&P·다우): 미 정규장 = 정규장, 그 외 = 장마감(종가)
    현물은 정규장에만 움직이므로 '야간선물'과 라벨이 겹치지 않는다."""
    wd, hm = kst.weekday(), kst.hour * 60 + kst.minute
    us_regular = (hm >= 22 * 60 + 30) or (hm <= 6 * 60)   # KST로 환산한 미 정규장(근사)
    if "코스피200" in name:                                  # 국내 선물
        if 9 * 60 <= hm <= 15 * 60 + 45:
            return "정규장"
        if hm >= 18 * 60 or hm <= 5 * 60:
            return "야간선물"
        return "장마감(종가)"
    if "선물" in name:                                       # 해외 선물(나스닥 등)
        return "정규장" if us_regular else "야간선물"
    if name in ("코스피", "코스닥"):                          # 국내 현물
        return "정규장" if (wd < 5 and 9 * 60 <= hm <= 15 * 60 + 30) else "장마감(종가)"
    return "정규장" if us_regular else "장마감(종가)"          # 해외 현물


def label_futures(indices: list, kst) -> None:
    """(표기) 선물 항목 이름을 세션에 맞춰 '○○ 야간선물' / '○○ 선물(정규장)'로 바꾼다(제자리).
    현물 지수(나스닥·코스피)와 헷갈리지 않도록 '야간선물'을 워딩으로 못박는다."""
    for ix in indices:
        name = ix.get("name", "")
        if "선물" not in name:
            continue
        base = name.replace("선물", "").strip()          # '나스닥', '코스피200'
        sess = index_session(name, kst)
        if sess == "야간선물":
            ix["name"] = f"{base} 야간선물"
        else:
            ix["name"] = f"{base} 선물({'정규장' if sess == '정규장' else '장마감'})"


def get_kr_futures():
    """(R6) 코스피200 선물 — 네이버 모바일. 야간 세션(18:00~익일 05:00)엔 야간선물 시세를 반영하므로
    나스닥선물과 함께 '밤사이 국내 방향성'을 본다(특히 KST 23시 브리핑). 실패 시 None.
    반환 형식은 get_indices 항목과 동일({name,flag,price,chg})."""
    try:
        d = requests.get("https://m.stock.naver.com/api/index/FUT/basic",
                         headers=_MOBILE_UA, timeout=8).json()
        price = float(str(d.get("closePrice", "")).replace(",", ""))
        chg = float(str(d.get("fluctuationsRatio", "")).replace(",", ""))
        return {"name": "코스피200선물", "flag": "🇰🇷", "price": price, "chg": chg}
    except Exception:
        return None


def kr_market_flow() -> dict:
    """(R5) 코스피·코스닥 투자자별 순매매(개인/외국인/기관, 억원) — 네이버 모바일 integration API.
    CNN 공포탐욕(미국 편향)을 보완하는 국내 시장 수급·심리 지표. 무인증·무료. 실패 시 빈 dict.
    ⚠️ 비공식 API라 형식 변경에 취약 — 값 없으면 조용히 생략(그레이스풀)."""
    out = {}
    for name, code in (("코스피", "KOSPI"), ("코스닥", "KOSDAQ")):
        try:
            r = requests.get(f"https://m.stock.naver.com/api/index/{code}/integration",
                             headers=_MOBILE_UA, timeout=8)
            dt = r.json().get("dealTrendInfo") or {}
            p, f, i = (_to_int(dt.get("personalValue")), _to_int(dt.get("foreignValue")),
                       _to_int(dt.get("institutionalValue")))
            if any(v is not None for v in (p, f, i)):
                out[name] = {"personal": p, "foreign": f, "institution": i,
                             "date": dt.get("bizdate")}
        except Exception:
            continue
    return out


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

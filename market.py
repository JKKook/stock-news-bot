"""실시간 시세 데이터 — 주요 지수(야후) + CNN 공포탐욕지수 (무료, API 키 불필요)."""

import requests

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

"""(R1) 정확도 측정 루프.

봇의 '되돌림 경고'(급등/급락 + 밴드 과확장 → 추격 주의)는 사실상
'이 극단 움직임은 되돌아올 것'이라는 방향성 판정이다.
이를 기록하고 MEASURE_HORIZON_DAYS 일 뒤 실제 가격과 대조해 '정말 되돌아왔는지'
적중률을 측정한다 — 정확도를 주장이 아니라 측정으로 바꿔 임계값 튜닝 근거를 만든다.

· 판정 기준은 notify._reversal_hits 를 그대로 재사용(렌더와 동일한 신호).
· 현재가는 브리핑이 이미 조회한 quotes 를 재사용(추가 API 호출 없음).
· 상태: prediction_log.json (GitHub Actions cache 로 실행 간 유지 — alert_state.json 과 동일 패턴).
· 모든 동작을 try/except 로 감싸 실패해도 브리핑을 멈추지 않는다(정보성 부가 기능).
"""

import json
from datetime import datetime, timezone, timedelta, date

import config

LOG_FILE = "prediction_log.json"


def _kst_date() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date()


def _load() -> dict:
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("signals", [])
        return d
    except Exception:
        return {"signals": []}


def _save(d: dict) -> None:
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass


def log_reversals(quotes: dict) -> None:
    """오늘 되돌림 경고가 뜬 종목을 기준가와 함께 기록. 같은 (날짜·종목)은 1회만."""
    try:
        from notify import _reversal_hits
        hits = _reversal_hits(quotes)
        if not hits:
            return
        d = _load()
        today = _kst_date().isoformat()
        existing = {s["id"] for s in d["signals"]}
        for label, q in hits:
            sid = f"{today}:{label}"
            if sid in existing or not q.get("price"):
                continue
            d["signals"].append({
                "id": sid, "date": today, "ticker": label,
                "chg": round(q.get("chg", 0), 2), "ref_price": q["price"],
                "bb": round(q.get("bb_pct", 0), 1),
                "scored": False, "outcome": None, "future_return": None,
            })
        d["signals"] = d["signals"][-config.MEASURE_LOG_MAX:]   # 오래된 것부터 폐기
        _save(d)
    except Exception:
        pass


def score_and_report(quotes: dict) -> list:
    """만기(HORIZON일 경과) 신호를 현재가로 채점하고, 누적 적중률 요약 블록을 반환.
    반환: list[list[str]] (블록) 또는 [] (표본 없음)."""
    try:
        d = _load()
        today = _kst_date()
        horizon = timedelta(days=config.MEASURE_HORIZON_DAYS)
        changed = False

        for s in d["signals"]:
            if s.get("scored"):
                continue
            try:
                logged = date.fromisoformat(s["date"])
            except Exception:
                continue
            if today - logged < horizon:
                continue                       # 아직 만기 전 — 대기
            q = (quotes or {}).get(s["ticker"])
            ref = s.get("ref_price")
            if not q or not q.get("price") or not ref:
                if today - logged >= horizon * 2:   # 오래 채점 불가면 폐기 처리
                    s["scored"], s["outcome"] = True, "unscoreable"
                    changed = True
                continue
            fut = (q["price"] - ref) / ref * 100    # 기록 후 사후 수익률(%)
            chg = s.get("chg", 0)
            reverted = (chg > 0 and fut < 0) or (chg < 0 and fut > 0)  # 되돌림 적중?
            s["scored"], s["future_return"] = True, round(fut, 2)
            s["outcome"] = "hit" if reverted else "miss"
            changed = True

        if changed:
            _save(d)

        cutoff = (today - timedelta(days=90)).isoformat()
        scored = [s for s in d["signals"]
                  if s.get("outcome") in ("hit", "miss") and s["date"] >= cutoff]
        pending = [s for s in d["signals"] if not s.get("scored")]

        if not scored:
            if pending:
                return [["### 📏 판정 정확도 (되돌림 경고)",
                         f"_표본 누적 중 — 검증 대기 {len(pending)}건 "
                         f"({config.MEASURE_HORIZON_DAYS}일 후 채점)_"]]
            return []

        n = len(scored)
        hits = sum(1 for s in scored if s["outcome"] == "hit")
        # 되돌림 방향으로 부호 통일(상승경고는 -fut, 하락경고는 +fut) → 양수=되돌림 우세
        rev = [(-s["future_return"] if s["chg"] > 0 else s["future_return"])
               for s in scored if s.get("future_return") is not None]
        avg_rev = sum(rev) / len(rev) if rev else 0.0
        return [[
            "### 📏 판정 정확도 (되돌림 경고 사후검증)",
            f"- 최근 검증 **{n}건 중 {hits}건 적중 · 적중률 {hits / n * 100:.0f}%** "
            f"(경고 후 {config.MEASURE_HORIZON_DAYS}일)",
            f"- 평균 되돌림 **{avg_rev:+.1f}%** (양수 = 급변이 실제로 되돌아옴)",
            "_봇의 '추격 주의' 판정이 맞았는지 누적 측정 · 정보성(매매신호 아님)_",
        ]]
    except Exception:
        return []

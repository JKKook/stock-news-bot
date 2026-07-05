"""속보·급변 감시 (별도 워크플로 alerts.yml, 10분 주기).

- 주가지수 전일 대비 ±N% 단계 돌파 시 즉시 알림
- 증시/지정학 속보 키워드가 포함된 새 기사 알림
- CNN 공포탐욕지수 급변 알림
상태(이미 보낸 알림/기사)는 alert_state.json 으로 관리해 중복 발송을 막는다.
"""

import json
import time
import urllib.parse
from datetime import datetime, timezone, timedelta

import feedparser

import config
from collect import _clean_title, _published, _source, _EPOCH
from events import fingerprint, headline
from market import get_indices, get_fear_greed
from notify import _fg_zone, send
from translate import translate_text

STATE_FILE = "alert_state.json"


def _parse_dt(iso: str) -> datetime:
    """ISO 문자열 → aware datetime. 실패 시 아주 과거(_EPOCH)로 취급."""
    try:
        return datetime.fromisoformat(iso)
    except Exception:
        return _EPOCH


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False)


# ── 1) 지수 급변 ────────────────────────────────────────────────
def check_indices(state: dict, today: str, indices: list[dict]) -> list[str]:
    bands = state.get("index_band", {}) if state.get("day") == today else {}
    trigger = {n for n, _, _ in config.ALERT_INDICES}  # 급변 트리거는 주식 지수만
    alerts = []
    for ix in indices:
        if ix["name"] not in trigger:
            continue
        chg = ix["chg"]
        crossed = max([b for b in config.ALERT_INDEX_BANDS if abs(chg) >= b], default=None)
        if crossed is None:
            continue
        if crossed > bands.get(ix["name"], 0):
            bands[ix["name"]] = crossed
            direction = "급락 🔻" if chg < 0 else "급등 🔺"
            level = "⚠️ 서킷브레이커/사이드카 수준" if crossed >= 8 else "큰 변동"
            alerts.append(
                f"{ix['flag']} **{ix['name']} {direction} {chg:+.2f}%** ({crossed}%↑ 돌파, {level})\n"
                f"현재 {ix['price']:,.2f} (전일 종가 대비)"
            )
    state["index_band"] = bands
    state["day"] = today
    return alerts


# ── 2) 속보 뉴스 ────────────────────────────────────────────────
def _gnews_url(query: str, lang: str) -> str:
    q = urllib.parse.quote(query)
    if lang == "ko":
        return f"https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _severity(title_low: str) -> int:
    """속보 심각도 (3=극단, 2=강, 1=일반) — D-3 정렬용. title_low는 소문자."""
    if any(w in title_low for w in config.ALERT_SEVERITY_HIGH):
        return 3
    if any(w in title_low for w in config.ALERT_SEVERITY_MID):
        return 2
    return 1


def check_news(state: dict) -> list[str]:
    sent = set(state.get("sent", []))
    events = dict(state.get("events", {}))   # 사건 지문 → 마지막 알림 시각(ISO)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=config.ALERT_LOOKBACK_MIN)
    window = timedelta(hours=config.ALERT_EVENT_WINDOW_HOURS)
    kw = [k.lower() for k in config.ALERT_KEYWORDS]

    # ── 1단계: 후보 수집 (조기 종료 없이 — L1 날짜·키워드·L2 컬럼 게이트 통과분) ──
    candidates = []
    for query, lang, region in config.ALERT_NEWS_QUERIES:
        try:
            feed = feedparser.parse(_gnews_url(query, lang))
        except Exception:
            continue
        for e in feed.entries[:15]:
            title = _clean_title(e)
            key = title.replace(" ", "")[:80]
            if not key or key in sent:
                continue
            low = title.lower()
            pub = _published(e)
            if pub is None or pub < cutoff:                       # L1: 무날짜·오래된 기사
                continue
            if not any(k in low for k in kw):                     # 속보 키워드 게이트
                continue
            if any(x in title for x in config.ALERT_NEWS_EXCLUDE): # L2: 회고·컬럼
                continue
            candidates.append({
                "sev": _severity(low), "pub": pub, "title": title, "lang": lang,
                "region": region, "src": _source(e), "link": e.get("link", "").strip(),
                "key": key, "fp": fingerprint(title),
            })
        if len(candidates) >= 40:   # 작업량 상한
            break

    # ── 2단계: 심각도 → 최신순 정렬 (D-3: 가장 중요한 속보를 먼저) ──
    candidates.sort(key=lambda c: (c["sev"], c["pub"]), reverse=True)

    # ── 3단계: 선정 (dedup + 사건 지문 억제, 번역은 선정분만) ──
    alerts, fresh_keys, picked_fp = [], [], set()
    for c in candidates:
        if len(alerts) >= config.ALERT_MAX_PER_RUN:
            break
        key, fp = c["key"], c["fp"]
        if key in sent:                       # 이번 실행 내 중복 키
            continue
        # Layer 4) 같은 사건 지문이 window 내 이미 알림됐거나 이번 선정분과 중복이면 억제
        #   (심각도순 선정이라 같은 사건은 '가장 센 제목'이 대표로 남는다)
        if fp and ((events.get(fp) and (now - _parse_dt(events[fp])) < window) or fp in picked_fp):
            sent.add(key)
            fresh_keys.append(key)
            continue
        sent.add(key)
        fresh_keys.append(key)
        if fp:
            events[fp] = now.isoformat()
            picked_fp.add(fp)
        shown = c["title"] if c["lang"] == "ko" else translate_text(c["title"])
        flag = "🇰🇷" if c["region"] == "국내" else "🇺🇸"
        block = f"{headline(c['title'])} {flag}\n{shown}" + (f" ({c['src']})" if c["src"] else "")
        if c["link"]:
            block += f"\n<{c['link']}>"
        alerts.append(block)

    # 사건 지문: window 지난 항목은 정리(상태 파일 비대화 방지)
    state["events"] = {fp: t for fp, t in events.items() if (now - _parse_dt(t)) < window}
    # 최근 보낸 기사 키 캡(300개)
    state["sent"] = (state.get("sent", []) + fresh_keys)[-300:]
    return alerts


# ── 3) 공포탐욕 급변 ────────────────────────────────────────────
def check_fng(state: dict) -> list[str]:
    fg = get_fear_greed()
    if not fg or fg.get("score") is None:
        return []
    score = fg["score"]
    last = state.get("fng_alerted")
    if last is None:
        state["fng_alerted"] = score      # 첫 실행은 기준만 저장
        return []
    if abs(score - last) >= config.ALERT_FNG_DELTA:
        state["fng_alerted"] = score
        zone, emoji = _fg_zone(score)
        return [f"{emoji} **공포탐욕지수 급변: {last:.0f} → {score:.0f}** ({zone})"]
    return []


def _pack(header: str, alerts: list[str]) -> list[str]:
    msgs, cur = [], header
    for a in alerts:
        if len(cur) + len(a) + 2 > 1900:
            msgs.append(cur)
            cur = ""
        cur += ("\n\n" if cur else "") + a
    if cur.strip():
        msgs.append(cur)
    return msgs


def main() -> None:
    state = load_state()
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    today = f"{kst:%Y-%m-%d}"

    try:
        indices = get_indices(config.INDICES)   # 전체 지수(금·은·비트코인 포함) 1회 조회
    except Exception:
        indices = []

    alerts = []
    alerts += check_indices(state, today, indices)
    alerts += check_fng(state)
    alerts += check_news(state)

    save_state(state)

    if not alerts:
        print("새 속보·급변 없음.")
        return

    # 상단은 중립 표기 — '급변'은 각 기사에서 뽑은 타이틀이 대신 설명한다
    header = f"📣 **실시간 시장 알림** — {kst:%H:%M} (KST)"
    messages = _pack(header, alerts)   # 속보 기사 내용만 (지수 대시보드 미첨부)
    if messages:
        messages[-1] += f"\n\n{config.DISCLAIMER}"   # (P0-3) 매수/매도 신호 아님
    send(messages)


if __name__ == "__main__":
    main()

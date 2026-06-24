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
from market import get_indices, get_fear_greed
from notify import _fg_zone, _fmt_chg, send
from translate import translate_text

STATE_FILE = "alert_state.json"


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


def check_news(state: dict) -> list[str]:
    sent = set(state.get("sent", []))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=config.ALERT_LOOKBACK_MIN)
    kw = [k.lower() for k in config.ALERT_KEYWORDS]
    alerts, fresh_keys = [], []

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
            pub = _published(e)
            if pub and pub < cutoff:          # 오래된 기사 제외
                continue
            if not any(k in title.lower() for k in kw):  # 속보 키워드 게이트
                continue
            sent.add(key)
            fresh_keys.append(key)
            shown = title if lang == "ko" else translate_text(title)
            src = _source(e)
            flag = "🇰🇷" if region == "국내" else "🇺🇸"
            link = e.get("link", "").strip()
            block = f"{flag} 🚨 {shown}" + (f" ({src})" if src else "")
            if link:
                block += f"\n<{link}>"
            alerts.append(block)
            if len(alerts) >= config.ALERT_MAX_PER_RUN:
                break
        if len(alerts) >= config.ALERT_MAX_PER_RUN:
            break

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


def _index_snapshot(indices: list[dict]) -> str:
    """알림에 첨부할 '현재 주요 지수' 전체 현황."""
    if not indices:
        return ""
    lines = ["📊 **현재 주요 지수**"]
    for ix in indices:
        lines.append(f"{ix['flag']} {ix['name']} {ix['price']:,.2f}  {_fmt_chg(ix['chg'])}")
    return "\n".join(lines)


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

    header = f"🚨 **속보 · 급변 알림** — {kst:%H:%M} (KST)"
    messages = _pack(header, alerts)
    snapshot = _index_snapshot(indices)   # 알림마다 전체 지수 현황 첨부
    if snapshot:
        messages.append(snapshot)
    send(messages)


if __name__ == "__main__":
    main()

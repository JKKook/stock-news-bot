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
from market import get_indices, get_fear_greed, get_quotes, index_session
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


# 세션 판정(정규장/야간선물/장마감)은 market.index_session 공유 — 브리핑 대시보드와 동일 기준.


# ── 1) 지수 급변 ────────────────────────────────────────────────
def check_indices(state: dict, today: str, indices: list[dict], kst: datetime) -> list[str]:
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
            session = index_session(ix["name"], kst)   # 정규장 / 야간선물 / 장마감
            alerts.append(
                f"{ix['flag']} **{ix['name']} {direction} {chg:+.2f}%** · ⏰{session} ({crossed}%↑ 돌파, {level})\n"
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


def _confirm_move(title: str) -> str:
    """(P3 확장) 속보 제목이 관심종목을 지목하면 실제 가격·거래량 반응으로 확증/주의.
    · |등락|≥ALERT_CONFIRM_MOVE → '📈 실제 {종목} {등락}·거래량 N×' (진짜 이례성, 선반영 강도)
    · 반응 미미 → '📉 {종목} 가격 반응 미미'(이미 반영/영향 제한 가능)
    관심종목 미언급이면 ''(지수·지정학 속보는 종목 확증 대상 아님). 매매신호 아님.
    실제 가격 반응으로 '제목만 자극적인 가짜 속보'를 사용자가 가려낼 근거를 준다."""
    matched = {}
    for label, sym in config.TICKER_SYMBOLS.items():
        if not sym or not config.TICKER_ALERT.get(label, True):   # (R7) 알림 off 종목 제외
            continue
        if label in title or (len(sym) >= 4 and sym in title):   # 한글명 또는 4자+ 심볼
            matched[label] = sym
    if not matched:
        return ""
    try:
        quotes = get_quotes(matched)
    except Exception:
        return ""
    parts = []
    for label, q in quotes.items():
        chg = q.get("chg")
        if chg is None:
            continue
        vm = q.get("vol_mult")
        vtxt = f"·거래량 {vm:.1f}×" if (vm and vm >= config.VOLUME_FLAG) else ""
        if abs(chg) >= config.ALERT_CONFIRM_MOVE:
            parts.append(f"📈 실제 {label} {chg:+.1f}%{vtxt} 동반")
        else:
            parts.append(f"📉 {label} 가격 반응 미미({chg:+.1f}%)")
    return ("\n" + " · ".join(parts)) if parts else ""


def _market_confirm(title: str, indices: list) -> str:
    """(R2) 속보 제목이 지수·시장·지정학을 지목하면 실제 지수/VIX 움직임으로 확증/주의.
    · 지수 |등락|≥ALERT_MARKET_MOVE → '📈 {지수} 실제 반응', 미만 → '📉 시장 반응 미미'
    · 지정학 키워드 + VIX 급등 → '📈 VIX 공포 급등'
    indices(get_indices 결과)를 재사용 — 추가 호출 없음. 관심종목 확증(_confirm_move)과 상호보완.
    '제목만 자극적이고 시장은 무반응'인 속보를 가려낼 근거를 준다. 매매신호 아님."""
    if not indices:
        return ""
    idx = {i["name"]: i for i in indices}
    low = title.lower()
    parts = []
    for keywords, candidates in config.ALERT_MARKET_MAP:
        if not any(k in low for k in keywords):
            continue
        avail = [idx[n] for n in candidates if n in idx]
        if not avail:
            continue
        rep = max(avail, key=lambda i: abs(i["chg"]))   # 가장 크게 움직인 것 = 대표 반응
        chg = rep["chg"]
        if abs(chg) >= config.ALERT_MARKET_MOVE:
            parts.append(f"📈 {rep['name']} {chg:+.1f}% 실제 반응")
        else:
            parts.append(f"📉 {rep['name']} {chg:+.1f}%(시장 반응 미미)")
    if any(k.lower() in low for k in config.ALERT_GEO_KEYWORDS):   # 지정학 → VIX
        vix = idx.get("VIX")
        if vix:
            if vix["chg"] >= config.ALERT_VIX_SPIKE:
                parts.append(f"📈 VIX {vix['chg']:+.1f}% 공포 급등")
            else:
                parts.append(f"📉 VIX {vix['chg']:+.1f}%(공포 반응 미미)")
    return ("\n" + " · ".join(parts)) if parts else ""


def check_macro(state: dict, kst: datetime, indices: list) -> list[str]:
    """(매크로 속보) 미 CPI·연준 FOMC 발표일 & 발표 시각(ET) 이후에 관련 내용을 속보로.
    급락/폭락 키워드가 없어도 발표일이면 흘려보낸다(물가·금리는 투자 핵심 정보). label별 하루 1회.
    · CPI  → 최신 헤드라인(제목에 수치 포함) 그대로
    · FOMC → 헤드라인들을 AI로 2~3문장 정리(연준 성명 핵심). 미국 지수 반응도 붙임."""
    from zoneinfo import ZoneInfo
    from catalysts import releases_between

    et = datetime.now(ZoneInfo("America/New_York"))
    et_today, kst_today = et.date().isoformat(), kst.date().isoformat()
    lo, hi = min(et_today, kst_today), max(et_today, kst_today)

    events = state.get("events", {})
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=config.ALERT_LOOKBACK_MIN)
    idx = {i["name"]: i for i in (indices or [])}
    alerts, fresh = [], []

    for ev in config.MACRO_ALERT_EVENTS:
        # ── 오늘이 발표일인가 (FRED release_id 또는 macro_calendar 날짜) ──
        if "release_id" in ev:
            hit = bool(releases_between(ev["release_id"], lo, hi))
        else:
            days = set(config.MACRO_DATES.get(ev.get("dates_key", ""), []))
            hit = bool(days & {et_today, kst_today})
        if not hit:
            continue
        # ── 발표 시각(ET) 이후인가 — 결과/성명 확보 ──
        ah, am = ev.get("after_et", [0, 0])
        if (et.hour, et.minute) < (ah, am):
            continue
        fp = f"macro:{ev['label']}:{et_today}"        # 발표일 기준 하루 1회
        if fp in events:
            continue

        # ── 관련 헤드라인 수집(발표 시각 이후·lookback 이내·키워드 매칭) ──
        found = []
        for query, lang in ev["queries"]:
            try:
                feed = feedparser.parse(_gnews_url(query, lang))
            except Exception:
                continue
            for e in feed.entries[:15]:
                title = _clean_title(e)
                low = title.lower()
                pub = _published(e)
                if pub is None or pub < cutoff:
                    continue
                if not any(k.lower() in low for k in ev["keywords"]):
                    continue
                if any(x in title for x in config.ALERT_NEWS_EXCLUDE):
                    continue
                shown = title if lang == "ko" else translate_text(title)
                found.append((pub, shown, e.get("link", "").strip(), _source(e)))
        if not found:
            continue
        found.sort(key=lambda x: x[0], reverse=True)   # 최신순

        # ── 본문 구성 ──
        header = f"📢 **{ev['label']} 발표** 🇺🇸"
        if ev.get("summarize"):
            from summarize import macro_brief
            brief = macro_brief(ev["label"], [f[1] for f in found[:6]])
            body = brief or found[0][1]
        else:
            body = found[0][1]
        block = f"{header}\n{body}"
        # 미국 지수 반응(있으면)
        react = [f"{n} {idx[n]['chg']:+.1f}%" for n in ("나스닥", "S&P500", "다우") if n in idx]
        if react:
            block += "\n📊 " + " · ".join(react)
        link = found[0][2]
        src = found[0][3]
        if src and not ev.get("summarize"):
            block += f" ({src})"
        if link:
            block += f"\n<{link}>"

        alerts.append(block)
        events[fp] = now.isoformat()
        fresh.append(fp)

    if fresh:
        state["events"] = events
    return alerts


def _is_market_closed_kst(kst: datetime) -> bool:
    """토·일 또는 한국 공휴일(KST)이면 True — 이때는 지수 급등락 속보를 보내지 않고
    전쟁·지정학 / 심각한 경제 충격 기사만 발송한다(config.ALERT_WEEKEND_ONLY)."""
    return kst.weekday() >= 5 or config.is_kr_holiday(kst.date())


def check_news(state: dict, indices: list, weekend: bool = False) -> list[str]:
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
            # (주말 모드) 토·일엔 지수 급등락 기사는 빼고 전쟁·심각한 경제 충격만 통과
            if weekend and not any(k.lower() in low for k in config.ALERT_WEEKEND_ONLY):
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
        block += _confirm_move(c["title"])            # (P3) 관심종목 지목 시 가격·거래량 확증
        block += _market_confirm(c["title"], indices)  # (R2) 지수·지정학 지목 시 시장 반응 확증
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

    # (휴장 모드) 토·일·공휴일(KST)엔 지수 급변·공포탐욕 알림을 끄고,
    #   속보는 전쟁·지정학 / 심각한 경제 충격 기사만 보낸다.
    closed = _is_market_closed_kst(kst)
    alerts = []
    if not closed:
        alerts += check_indices(state, today, indices, kst)
        alerts += check_fng(state)
    alerts += check_macro(state, kst, indices)   # 미 CPI·연준 FOMC 발표일 속보(휴장 무관)
    alerts += check_news(state, indices, weekend=closed)

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

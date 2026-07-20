"""속보·급변 감시 (별도 워크플로 alerts.yml, 10분 주기).

- 주가지수 전일 대비 ±N% 단계 돌파 시 즉시 알림
- 증시/지정학 속보 키워드가 포함된 새 기사 알림
- CNN 공포탐욕지수 급변 알림
상태(이미 보낸 알림/기사)는 alert_state.json 으로 관리해 중복 발송을 막는다.
"""

import json
import re
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


# ── (Layer 5) 내용 유사도 중복 억제 헬퍼 ────────────────────────────
_NORM_RE = re.compile(r"[^0-9a-z가-힣]+")


def _norm_title(title: str) -> str:
    """비교용 정규화 — 소문자 + 한글·영숫자만 남김(공백·문장부호·이모지 제거)."""
    return _NORM_RE.sub("", title.lower())


def _title_sim(a: str, b: str) -> float:
    """두 정규화 제목의 문자 bigram Jaccard 유사도(0~1).
    단어 순서·조사 차이에 강해, 출처만 다른 재탕 헤드라인을 언어 무관하게 잡는다."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ga = {a[i:i + 2] for i in range(len(a) - 1)} or {a}
    gb = {b[i:i + 2] for i in range(len(b) - 1)} or {b}
    return len(ga & gb) / len(ga | gb)


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
    """휴장(급등락 억제) 모드인지 — 이때는 지수 급등락 속보를 보내지 않고
    전쟁·지정학 / 심각한 경제 충격 기사만 발송한다(config.ALERT_WEEKEND_ONLY).

    · 토·일 또는 한국 공휴일(KST) → True
    · 주말·공휴일 직후 '첫 거래일'의 코스피 정규장 개장(09:00) 전(00:00~08:59) → True
      (예: 월요일 새벽. 이 시간대 '급락/폭락' 속보는 직전 주말 흐름을 되짚는 stale 기사라
       다음 정규장이 열릴 때까지 지수 알림에서 제외한다.)
      단, 어제가 정규 거래일이면 밤사이 미국장이 실제로 열려 있었으므로(KST 심야=미 정규장)
      개장 전이라도 억제하지 않는다 — 진짜 라이브 급락 속보를 놓치지 않기 위함."""
    d = kst.date()
    if kst.weekday() >= 5 or config.is_kr_holiday(d):
        return True
    if kst.hour < 9:                                    # 첫 거래일 개장(09:00) 전
        prev = d - timedelta(days=1)
        if prev.weekday() >= 5 or config.is_kr_holiday(prev):
            return True
    return False


def check_news(state: dict, indices: list, weekend: bool = False) -> list[str]:
    sent = set(state.get("sent", []))
    events = dict(state.get("events", {}))   # 사건 지문 → 마지막 알림 시각(ISO)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=config.ALERT_LOOKBACK_MIN)
    window = timedelta(hours=config.ALERT_EVENT_WINDOW_HOURS)
    kw = [k.lower() for k in config.ALERT_KEYWORDS]
    # (L5) 최근 발송 제목(정규화) — [[iso, norm], ...]. dup 창 지난 건 버리고 비교 대상만 남긴다.
    dup_window = timedelta(hours=config.ALERT_DUP_WINDOW_HOURS)
    sent_titles = [e for e in state.get("sent_titles", [])
                   if len(e) == 2 and (now - _parse_dt(e[0])) < dup_window]

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
        # Layer 5) 내용 유사도 — 출처만 다른 재탕(제목이 조금 다른 같은 사건)이 최근 발송분과
        #   THRESHOLD 이상 겹치면 억제. 같은 실행 내 선정분(sent_titles에 즉시 추가됨)도 함께 비교.
        norm = _norm_title(c["title"])
        if norm and any(_title_sim(norm, e[1]) >= config.ALERT_DUP_SIM_THRESHOLD
                        for e in sent_titles):
            sent.add(key)
            fresh_keys.append(key)
            continue
        sent.add(key)
        fresh_keys.append(key)
        sent_titles.append([now.isoformat(), norm])   # 이후 후보·다음 실행과의 비교 대상에 포함
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
    # (L5) 최근 발송 제목도 캡(300개) — dup 창 안에서 재탕 비교에 사용
    state["sent_titles"] = sent_titles[-300:]
    return alerts


# ── 2-1) 섹터 큰 호재·악재 속보 ──────────────────────────────────
def check_sectors(state: dict) -> list[str]:
    """관심 섹터(config.SECTORS)의 '엄청난' 호재/악재만 실시간 속보로.
    섹터 검색어로 찾은 최근 기사에 강한 호재/악재 키워드가 있을 때만 인정(일반 소식 무시).
    노이즈 억제: L1 날짜·L2 회고컷·L5 유사도 + 섹터 지문(섹터:방향)으로 18h 내 재알림 억제.
    표시: ⭐ **[섹터 · 호재]** / ❗ **[섹터 · 악재]** (급락/규제 등 시장 속보와 별개 라인)."""
    if not config.ALERT_SECTOR_ENABLE:
        return []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=config.ALERT_LOOKBACK_MIN)
    window = timedelta(hours=config.ALERT_EVENT_WINDOW_HOURS)
    dup_window = timedelta(hours=config.ALERT_DUP_WINDOW_HOURS)
    pos = [k.lower() for k in config.ALERT_SECTOR_POSITIVE]
    neg = [k.lower() for k in config.ALERT_SECTOR_NEGATIVE]

    sent = set(state.get("sent", []))
    sec_events = dict(state.get("sector_events", {}))     # 섹터 지문 → 마지막 알림 ISO
    titles_all = list(state.get("sent_titles", []))
    recent = [e for e in titles_all if len(e) == 2 and (now - _parse_dt(e[0])) < dup_window]

    # ── 후보 수집 (섹터 검색 + 강한 호재/악재 게이트) ──
    candidates = []
    for sector, queries in config.SECTORS.items():
        for query, lang, region in queries:
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
                if pub is None or pub < cutoff:                         # L1 날짜
                    continue
                if any(x in title for x in config.ALERT_NEWS_EXCLUDE):  # L2 회고컷
                    continue
                is_neg = any(k in low for k in neg)
                is_pos = any(k in low for k in pos)
                if not (is_neg or is_pos):        # '엄청난' 게이트 — 둘 다 아니면 무시
                    continue
                direction = "악재" if is_neg else "호재"   # 겹치면 악재 우선(리스크)
                candidates.append({
                    "sector": sector, "direction": direction, "pub": pub, "title": title,
                    "lang": lang, "region": region, "src": _source(e),
                    "link": e.get("link", "").strip(), "key": key,
                    "fp": f"sector:{sector}:{direction}",
                })

    candidates.sort(key=lambda c: c["pub"], reverse=True)   # 최신순

    # ── 선정 (dedup + 섹터 지문 + L5 유사도) ──
    alerts, fresh_keys, picked_fp = [], [], set()
    for c in candidates:
        if len(alerts) >= config.ALERT_SECTOR_MAX_PER_RUN:
            break
        key, fp = c["key"], c["fp"]
        if key in sent:
            continue
        if (sec_events.get(fp) and (now - _parse_dt(sec_events[fp])) < window) or fp in picked_fp:
            sent.add(key)
            fresh_keys.append(key)
            continue
        norm = _norm_title(c["title"])
        if norm and any(_title_sim(norm, e[1]) >= config.ALERT_DUP_SIM_THRESHOLD for e in recent):
            sent.add(key)
            fresh_keys.append(key)
            continue
        sent.add(key)
        fresh_keys.append(key)
        entry = [now.isoformat(), norm]
        recent.append(entry)
        titles_all.append(entry)
        sec_events[fp] = now.isoformat()
        picked_fp.add(fp)
        shown = c["title"] if c["lang"] == "ko" else translate_text(c["title"])
        flag = "🇰🇷" if c["region"] == "국내" else "🇺🇸"
        icon = "⭐" if c["direction"] == "호재" else "❗"
        src = f" ({c['src']})" if c["src"] else ""
        block = f"{icon} **[{c['sector']} · {c['direction']}]** {flag}\n{shown}{src}"
        if c["link"]:
            block += f"\n<{c['link']}>"
        alerts.append(block)

    state["sector_events"] = {fp: t for fp, t in sec_events.items() if (now - _parse_dt(t)) < window}
    state["sent"] = (state.get("sent", []) + fresh_keys)[-300:]
    state["sent_titles"] = titles_all[-300:]
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
    alerts += check_sectors(state)               # 관심 섹터의 엄청난 호재/악재(휴장 무관)

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

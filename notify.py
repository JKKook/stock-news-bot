"""모은 뉴스를 디스코드 채널(웹훅)로 보내는 모듈.

가독성 우선 레이아웃:
· 국내 / 해외를 최상위로 분리 (각 지역은 새 메시지로 시작)
· 디스코드 마크다운 헤더(#, ##)로 영역을 시각적으로 구분
· 기사는 '제목 + 발췌문(인용)' 텍스트, 링크는 맨 위 Yahoo 헤드라인 1개뿐
"""

import os
import time
import unicodedata

import requests

from config import (EXCERPT_MAX_LEN, SOURCE_BLOOMBERG, DISCLAIMER,
                    TICKER_MOVE_FLAG, SHORT_INTEREST_FLAG, REVERSAL_MOVE_FLAG,
                    VOLUME_FLAG, BLOOMBERG_EXCERPT_LEN, TICKERS, TICKER_SYMBOLS)

DISCORD_LIMIT = 1900  # 디스코드 메시지 길이 제한(2000)보다 약간 작게

SEPARATOR = "┄" * 42  # 구분선(점선) — 길게 꽉 채움

REGIONS = [("국내", "# 🇰🇷 국내 증시"), ("해외", "# 🇺🇸 해외 증시")]


def _fmt_chg(c: float) -> str:
    arrow = "▲" if c > 0 else ("▼" if c < 0 else "─")
    return f"{arrow} {c:+.2f}%"


def _bb_label(pct: float) -> str:
    """볼린저 %B → 표시 라벨. 밴드 안은 위치%, 밖은 상단↑/하단↓."""
    if pct > 100:
        return "BB 상단↑"   # 밴드 상단 위(과열 구간)
    if pct < 0:
        return "BB 하단↓"   # 밴드 하단 아래(과매도 구간)
    return f"BB {pct:.0f}%"


def _bb_zone(pct: float) -> str:
    """볼린저 %B → 밴드 내 위치 서술(과매도/과열 같은 신호어 대신 위치어 사용)."""
    if pct > 100:
        return "밴드 상단 돌파"
    if pct >= 80:
        return "밴드 상단권"
    if pct < 0:
        return "밴드 하단 이탈"
    if pct <= 20:
        return "밴드 하단권"
    return "밴드 중심권"


def _per_desc(t, f) -> str:
    """PER 서술 — Trailing/Forward 명칭 명확 + 두 값의 방향으로 이익 성장/감소 판단.
    절대 고/저평가는 단정하지 않는다(정보성)."""
    if t and t > 0:
        s = f"Trailing PER {t:.1f}배"
        if f and f > 0:
            s += f" → Forward PER {f:.1f}배"
            if f < t * 0.9:       # 미래 EPS↑ 기대 → Forward↓
                s += " · 이익성장 기대"
            elif f > t * 1.1:     # 미래 EPS↓ 우려 → Forward↑
                s += " · 이익감소 우려"
        return s
    if f and f > 0:               # 국내주 등 trailing 결측 → forward만
        return f"Forward PER {f:.1f}배"
    if f is not None and f < 0:   # 적자 → PER 무의미
        return "적자 (PER 해당없음)"
    return ""


def _assess_line(q: dict) -> str:
    """볼린저 위치 + PER + (높을 때만)공매도 수급을 결합한 한 줄 판단.
    정보성 — 매수/매도 신호 아님."""
    parts = []
    if q.get("bb_pct") is not None:
        parts.append(_bb_zone(q["bb_pct"]))
    per = _per_desc(q.get("per_trailing"), q.get("per_forward"))
    if per:
        parts.append(per)
    sp = q.get("short_pct")
    if sp is not None and sp >= SHORT_INTEREST_FLAG:   # (A) 공매도 높을 때만
        arrow = "↑" if q.get("short_up") else ("↓" if q.get("short_up") is False else "")
        parts.append(f"공매도 {sp:.0f}%{arrow}")
    return "📐 " + " · ".join(parts) if parts else ""


def _dwidth(s: str) -> int:
    """표 정렬용 표시 폭 — 한중일 문자는 2, 그 외 1."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, w: int, right: bool = False) -> str:
    gap = " " * max(0, w - _dwidth(s))
    return (gap + s) if right else (s + gap)


def _per_num(d: dict) -> str:
    """표용 PER 한 값 — Trailing 우선(개별 줄과 동일 기준), 없으면 Forward, 적자/결측 처리."""
    t, f = d.get("per_trailing"), d.get("per_forward")
    v = t if (t and t > 0) else (f if (f and f > 0) else None)
    if v:
        return "999+" if v > 999 else f"{v:.0f}"   # 극단 PER(고성장·저이익)은 캡 — 숫자 오인 방지
    if (t is not None and t < 0) or (f is not None and f < 0):
        return "적자"
    return "-"


def _cap_width(s: str, maxw: int) -> str:
    """표시 폭 기준으로 문자열을 maxw 이하로 자른다(줄넘김 방지용 종목명 제한)."""
    if _dwidth(s) <= maxw:
        return s
    out = ""
    for ch in s:
        if _dwidth(out + ch) > maxw:
            break
        out += ch
    return out


def _compact_price(v: float) -> str:
    """해외(USD) 가격을 짧게 — 100↑ 정수, 그 외 소수."""
    return f"{v:.0f}" if abs(v) >= 100 else f"{v:.1f}"


def _price_kwon(v: float) -> str:
    """국내 가격을 '천원' 단위로 — 2,425,000원→2425, 309,500→310, 1,141→1.1."""
    k = v / 1000
    return f"{k:.0f}" if abs(k) >= 10 else f"{k:.1f}"


def _one_table(rows: list) -> list[str]:
    """헤더행 포함 rows(가변 열) → monospace 표 라인들. 첫 열 좌측·나머지 우측 정렬.
    열 간격 1칸으로 좁게 — 모바일에서 줄넘김 없이 한눈에 보이도록."""
    ncol = len(rows[0])
    widths = [max(_dwidth(r[i]) for r in rows) for i in range(ncol)]
    lines = ["```"]
    for i, r in enumerate(rows):
        cells = [_pad(r[0], widths[0])] + [_pad(r[j], widths[j], True) for j in range(1, ncol)]
        line = " ".join(cells)
        lines.append(line)
        if i == 0:
            lines.append("─" * _dwidth(line))
    lines.append("```")
    return lines


def _watchlist_table_blocks(quotes: dict) -> list[list[str]]:
    """전 관심종목 지표를 국내/해외 2개 표(코드블록)로 — 한눈 요약(정보 밀도)."""
    if not quotes:
        return []
    region_of = {label: region for label, _q, _l, region in TICKERS}
    groups = {"해외": [], "국내": []}
    for label, d in quotes.items():
        r = region_of.get(label)
        if r in groups:
            groups[r].append((label, d))

    body = ["### ⭐ 관심종목 지표"]
    for region, flag in [("해외", "🇺🇸"), ("국내", "🇰🇷")]:
        if not groups[region]:
            continue
        rows = [("종목", "가격", "등락", "BB", "PER")]
        for label, d in groups[region]:
            # 해외는 티커 심볼(좁고 인식됨)·USD, 국내는 한글명 10폭 컷·천원
            if region == "해외":
                name, price = (TICKER_SYMBOLS.get(label) or label), _compact_price(d["price"])
            else:
                name, price = _cap_width(label, 10), _price_kwon(d["price"])
            bb = f"{d['bb_pct']:.0f}" if d.get("bb_pct") is not None else "-"
            rows.append((name, price, f"{d['chg']:+.1f}%", bb, _per_num(d)))
        body.append(f"**{flag} {region}**")
        body += _one_table(rows)
    body.append("_국내 가격=천원 · 해외=$ · BB=볼린저%B(중심50) · PER=Trailing_")
    return [body] if len(body) > 2 else []


def _quote_line(q: dict) -> str:
    """관심종목 시세 한 줄 (P0-1): 현재가 · 등락% · 52주 위치 · 볼린저 %B.
    52주 위치 = 최근 1년 저가~고가 구간에서의 백분위. BB% = 15일 밴드 내 위치(중심 50)."""
    price = q["price"]
    p = f"{price:,.0f}" if q.get("currency") == "KRW" else f"{price:,.2f}"
    line = f"📊 {p} {_fmt_chg(q['chg'])}"
    if q.get("w52pos") is not None:
        line += f" · 52주 {q['w52pos']:.0f}%"
    if q.get("bb_pct") is not None:
        line += f" · {_bb_label(q['bb_pct'])}"
    return line


def _fg_zone(score: float):
    if score < 25:
        return "극단적 공포", "😱"
    if score < 45:
        return "공포", "😨"
    if score <= 55:
        return "중립", "😐"
    if score < 75:
        return "탐욕", "🤑"
    return "극단적 탐욕", "🤩"


def _dashboard_blocks(indices, fear_greed) -> list[list[str]]:
    """맨 위 대시보드: 주요 지수 시세 + CNN 공포탐욕지수."""
    blocks = []
    if indices:
        b = ["### 📊 주요 지수 시세"]
        for ix in indices:
            b.append(f"{ix['flag']} **{ix['name']}**  {ix['price']:,.2f}  {_fmt_chg(ix['chg'])}")
        blocks.append(b)

    if fear_greed and fear_greed.get("score") is not None:
        score = fear_greed["score"]
        zone, emoji = _fg_zone(score)
        filled = round(score / 100 * 20)
        bar = "█" * filled + "░" * (20 - filled)
        fb = [
            f"### {emoji} 공포탐욕지수 (CNN)",
            f"**{score:.0f} / 100 — {zone} ({fear_greed['rating'].title()})**",
            f"`[{bar}]`",
        ]
        hist = []
        for label, key in [("어제", "prev"), ("1주 전", "week"), ("한달 전", "month"), ("1년 전", "year")]:
            v = fear_greed.get(key)
            if v is not None:
                hist.append(f"{label} {v:.0f}")
        if hist:
            fb.append(" · ".join(hist))
        blocks.append(fb)
    return blocks


_CTRY_FLAG = {"US": "🇺🇸", "KR": "🇰🇷", "EA": "🇪🇺", "CN": "🇨🇳", "JP": "🇯🇵", "GB": "🇬🇧"}
_IMPACT_STARS = {"High": "★★★", "Medium": "★★☆", "Low": "★☆☆"}


def _summary_blocks(summary: dict) -> list[list[str]]:
    """상단 'so-what' 요약 (P1) — 무엇이 바뀌었나/왜 중요한가/무엇을 지켜볼까."""
    if not summary:
        return []
    b = ["### 🧭 오늘의 핵심 (AI 요약)"]
    if summary.get("what_changed"):
        b.append(f"**무엇이 바뀌었나** — {summary['what_changed']}")
    if summary.get("why_matters"):
        b.append(f"**왜 중요한가** — {summary['why_matters']}")
    if summary.get("watch"):
        b.append(f"**무엇을 지켜볼까** — {summary['watch']}")
    if summary.get("affected"):
        b.append(f"**관심 대상 연결** — {summary['affected']}")  # (P1-6) 사건→섹터/종목
    return [b] if len(b) > 1 else []


def _catalyst_blocks(catalysts: dict) -> list[list[str]]:
    """다가오는 '예정 촉매' — 경제지표(임팩트 별점) + 관심종목 실적 (P0-4)."""
    if not catalysts:
        return []
    econ = catalysts.get("economic") or []
    earn = catalysts.get("earnings") or []
    if not econ and not earn:
        return []

    body = ["### 📅 예정 촉매"]  # 호라이즌은 상위 🟡 중기 divider가 표시
    if econ:
        body.append("**경제지표**")
        for e in econ:
            flag = _CTRY_FLAG.get(e["country"], e["country"])
            stars = _IMPACT_STARS.get(e["impact"], "")
            body.append(f"{stars} `{e['date']}` {flag} {e['event']}")
    if earn:
        if econ:
            body.append("")   # 경제지표 ↔ 실적 사이 여백(margin) 통일
        body.append("**관심종목 실적**")
        # 날짜별로 티커 묶어 한 줄씩
        by_date = {}
        for x in earn:
            by_date.setdefault(x["date"], []).append(x["name"])
        for d in sorted(by_date):
            body.append(f"`{d}` " + " · ".join(by_date[d]))
    return [body]


def _cut(text: str, maxlen: int) -> str:
    text = text.strip()
    if len(text) <= maxlen:
        return text
    cut = text[:maxlen]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def _item_blocks(items: list[dict], show_region: bool = False) -> list[list[str]]:
    """기사 1건 = ['• 제목 (출처)'] (발췌문 있으면 다음 줄) 묶음. 묶음은 분할되지 않는다.
    같은 소제목 안의 기사들은 붙이고, 그룹 끝에 빈 줄을 둬 다음 소제목과 분리한다.
    show_region=True 면 항목 앞에 🇰🇷/🇺🇸(테마 뉴스처럼 지역이 섞일 때)."""
    blocks = []
    for it in items:
        src = f" ({it['source']})" if it["source"] else ""
        rf = ""
        if show_region:
            rf = "🇰🇷 " if it.get("region") == "국내" else "🇺🇸 " if it.get("region") == "해외" else ""
        block = [f"• {rf}{it['title']}{src}"]
        if it["excerpt"]:
            block.append(_cut(it["excerpt"], EXCERPT_MAX_LEN))
        blocks.append(block)
    if blocks:
        blocks[-1] = blocks[-1] + [""]  # 그룹 끝 빈 줄
    return blocks


def _section(header: str, labeled_groups: list, show_region: bool = False) -> list[list[str]]:
    """섹션 헤더 + 소제목이 항상 첫 기사와 붙어 다니도록 블록을 구성.
    labeled_groups: [(소제목, items), ...]
    """
    out, pending = [], header
    for label, items in labeled_groups:
        ibs = _item_blocks(items, show_region)
        lead = [label] + (ibs[0] if ibs else [])
        if pending:
            lead = [pending] + lead
            pending = None
        out.append(lead)
        out += ibs[1:]
    return out  # 빈 섹션이면 빈 리스트


def _grouped_blocks(header: str, labeled_groups: list, show_region: bool = False) -> list[list[str]]:
    """소제목+기사들을 '소제목당 한 블록'으로 묶는다 — 메시지 분할 시 헤더-기사 고아 방지.
    첫 블록에 섹션 헤더를 붙이고, 각 그룹 끝에 빈 줄(margin)을 둔다."""
    out, first = [], True
    for label, items in labeled_groups:
        lines = [label]
        for it in items:
            rf = ""
            if show_region:
                rf = "🇰🇷 " if it.get("region") == "국내" else "🇺🇸 " if it.get("region") == "해외" else ""
            src = f" ({it['source']})" if it.get("source") else ""
            lines.append(f"• {rf}{it['title']}{src}")
            if it.get("excerpt"):
                lines.append(_cut(it["excerpt"], EXCERPT_MAX_LEN))
        lines.append("")  # 그룹 사이 여백
        if first:
            lines = [header] + lines
            first = False
        out.append(lines)
    return out


def _theme_news_blocks(sectors: list[dict]) -> list[list[str]]:
    """테마별 소식 — 테마(섹터) 단위로 국내+해외 뉴스를 함께 묶는다(SECTORS 순서 유지)."""
    by_theme, order = {}, []
    for g in sectors:
        if not g["items"]:
            continue
        if g["label"] not in by_theme:
            by_theme[g["label"]] = []
            order.append(g["label"])
        by_theme[g["label"]] += g["items"]
    if not order:
        return []
    labeled = [(f"**{lbl}**", by_theme[lbl]) for lbl in order]
    return _grouped_blocks("### 🏭 테마별 소식", labeled, show_region=True)


def _watchlist_news_blocks(tickers: list[dict], quotes=None) -> list[list[str]]:
    """관심종목 뉴스 — 뉴스 있는 종목만, 뉴스 불릿만(수치·판단은 상단 표에). 변동폭 큰 순."""
    groups = [g for g in tickers if g["items"]]
    if not groups:
        return []

    def move(g):
        q = (quotes or {}).get(g["label"])
        return abs(q["chg"]) if q else -1.0

    labeled = []
    for g in sorted(groups, key=move, reverse=True):
        q = (quotes or {}).get(g["label"])
        flag = " 🔥" if (q and abs(q["chg"]) >= TICKER_MOVE_FLAG) else ""
        rf = "🇰🇷 " if g.get("region") == "국내" else "🇺🇸 " if g.get("region") == "해외" else ""
        labeled.append((f"**{rf}{g['label']}**{flag}", g["items"]))
    return _grouped_blocks("### ⭐ 관심종목 뉴스", labeled)


def _watchlist_highlights(quotes: dict) -> list[list[str]]:
    """표 아래 '주목' 한 줄 — 밴드 극단(≤20/≥80)이거나 급변(±5%↑)인 종목의 핵심 판단(표=수치+판단)."""
    if not quotes:
        return []
    cand = []
    for label, q in quotes.items():
        bb = q.get("bb_pct")
        big = abs(q.get("chg", 0)) >= TICKER_MOVE_FLAG
        extreme = bb is not None and (bb <= 20 or bb >= 80)
        if not (big or extreme):
            continue
        bits = [_fmt_chg(q["chg"])]
        if bb is not None:
            bits.append(_bb_zone(bb))
        vm = q.get("vol_mult")
        if vm is not None and vm >= VOLUME_FLAG:   # (P3-1) 거래량 동반 시 표시
            bits.append(f"거래량 {vm:.1f}×")
        sp = q.get("short_pct")
        if sp is not None and sp >= SHORT_INTEREST_FLAG:
            bits.append(f"공매도 {sp:.0f}%")
        cand.append((abs(q.get("chg", 0)), f"{label} " + "·".join(bits)))
    if not cand:
        return []
    cand.sort(reverse=True)   # 변동폭 큰 순 상위 4개만
    return [["📐 **주목** — " + "  /  ".join(n for _, n in cand[:4])]]


def _kr_flow_blocks(quotes: dict) -> list[list[str]]:
    """(P2-7) 국내 관심종목 외국인·기관 순매매 — 주식수×종가로 억원 근사, 큰 순.
    CNN 공포탐욕(미국 편향)을 보완하는 국내 수급 관점. 정보성(매매신호 아님)."""
    if not quotes:
        return []
    rows = []
    for label, q in quotes.items():
        f, i, p = q.get("foreign_net"), q.get("inst_net"), q.get("price")
        if f is None or i is None or not p:
            continue
        fv, iv = f * p / 1e8, i * p / 1e8          # 순매매금액 근사(억원)
        if abs(fv) < 30 and abs(iv) < 30:          # 30억 미만은 노이즈로 생략
            continue
        rows.append((abs(fv) + abs(iv), label, fv, iv))
    if not rows:
        return []
    rows.sort(reverse=True)

    def eok(v):   # 억 단위 값 → 1조 이상은 '조'로
        return f"{v / 10000:+.1f}조" if abs(v) >= 10000 else f"{v:+,.0f}억"

    lines = ["### 🏦 국내 수급 · 외국인·기관 순매매"]
    for _, label, fv, iv in rows[:8]:
        lines.append(f"- **{label}** 외국인 {eok(fv)} · 기관 {eok(iv)}")
    lines.append("_전 거래일 · 주식수×종가 환산(근사) · 순매수(+)/순매도(−) · 매매신호 아님._")
    return [lines]


def _reversal_warnings(quotes: dict) -> list[list[str]]:
    """(P3-2) 급변(±REVERSAL_MOVE_FLAG%↑) + 볼린저 밴드 같은 방향 과확장(상단권/하단권)이
    겹친 종목에 '이미 큰 폭 반영·추격 주의' 맥락 라벨. 정보성 행동재무 경고 — 매매신호 아님.
    급등/급락 소식에 추격 진입하려는 충동을 '이미 가격에 반영됐을 수 있다'로 눌러준다."""
    if not quotes:
        return []
    hits = []
    for label, q in quotes.items():
        chg, bb = q.get("chg"), q.get("bb_pct")
        if chg is None or bb is None or abs(chg) < REVERSAL_MOVE_FLAG:
            continue
        stretched = (chg > 0 and bb >= 80) or (chg < 0 and bb <= 20)  # 급변과 같은 방향 과확장
        if not stretched:
            continue
        desc = f"{label} {chg:+.1f}%·{_bb_zone(bb)}"
        vm = q.get("vol_mult")
        if vm is not None and vm >= VOLUME_FLAG:   # (P3-1) 거래량 동반이면 선반영 강도↑
            desc += f"·거래량 {vm:.1f}×"
        hits.append((abs(chg), desc))
    if not hits:
        return []
    hits.sort(reverse=True)
    return [[
        "⚠️ **추격 주의** · 이미 큰 폭 반영 — " + "  /  ".join(h for _, h in hits),
        "_급등·급락이 가격에 선반영됐을 수 있어 되돌림 위험 — 추격 진입은 신중히(매매 신호 아님)._",
    ]]


def _region_blocks(title, market, sectors_grouped, tickers, quotes=None) -> list[list[str]]:
    blocks = [[title]]
    if market:
        blocks += _section("### 📰 시장 뉴스",
                           [(f"**{g['label']}**", g["items"]) for g in market])
    if sectors_grouped:
        blocks += _section("### 🏭 섹터별 소식 · 장기 테마",  # (P2-8) 호라이즌 라벨
                           [(f"**{lbl}**", items) for lbl, items in sectors_grouped])
    if tickers:
        # (P0-1) 종목명 옆에 시세 첨부 + (P0-2) 변동폭 큰 순 정렬·급변 🔥 표시
        def move(g):
            q = (quotes or {}).get(g["label"])
            return abs(q["chg"]) if q else -1.0   # 시세 있는 큰 변동 먼저, 없으면 뒤로
        labeled = []
        for g in sorted(tickers, key=move, reverse=True):
            q = (quotes or {}).get(g["label"])
            flag = " 🔥" if (q and abs(q["chg"]) >= TICKER_MOVE_FLAG) else ""
            label = f"**{g['label']}**{flag}"
            if q:
                label += f"\n{_quote_line(q)}"          # 시세는 다음 줄
                assess = _assess_line(q)                # 볼린저+PER 판단은 또 다음 줄
                if assess:
                    label += f"\n{assess}"
            labeled.append((label, g["items"]))
        blocks += _section("### ⭐ 관심 종목", labeled)
    return blocks


def _emit(blocks: list[list[str]]) -> list[str]:
    """블록을 디스코드 한도에 맞춰 메시지로 합친다.
    · 한 블록(제목+발췌문)은 쪼개지 않음
    · '# '로 시작하는 지역 헤더는 항상 새 메시지로 시작
    """
    messages, current = [], ""
    for block in blocks:
        text = "\n".join(block)
        # '# ' 지역 헤더 또는 '**━━' 호라이즌 divider는 항상 새 메시지로 시작
        force_break = bool(block) and (block[0].startswith("# ") or block[0].startswith("**━━"))
        is_header = bool(block) and block[0].lstrip().startswith("#")

        if force_break and current:
            messages.append(current.rstrip())
            current = ""

        if len(text) > DISCORD_LIMIT:  # 블록 하나가 한도 초과 시 글자수로 분할
            if current:
                messages.append(current.rstrip())
                current = ""
            for i in range(0, len(text), DISCORD_LIMIT):
                messages.append(text[i:i + DISCORD_LIMIT])
            continue

        # 섹션(##/#) 헤더 앞은 '정확히 빈 줄 1개'로 통일 — 그룹 끝 빈 줄과 겹쳐도 중복 방지
        if current and is_header:
            current = current.rstrip("\n") + "\n\n"
        if current and len(current) + len(text) + 1 > DISCORD_LIMIT:
            messages.append(current.rstrip())
            current = ""
        current += text + "\n"

    if current.strip():
        messages.append(current.rstrip())
    return messages


def _bloomberg_blocks(items: list[dict]) -> list[list[str]]:
    """해외 섹션 내 블룸버그 하위 섹션: 제목 + 요약(번역) + <링크>."""
    out = []
    for i, it in enumerate(items):
        b = [f"• {it['title']} (Bloomberg)"]
        if it.get("excerpt"):
            b.append(_cut(it["excerpt"], BLOOMBERG_EXCERPT_LEN))  # 1~2줄만(링크는 하단 Source)
        # 링크는 본문에 달지 않고 Source 영역에서만 제공
        if i == 0:
            b = ["### 🏦 블룸버그 주요 기사"] + b  # 헤더를 첫 기사와 묶어 고아 방지
        out.append(b)
    return out


def _glossary_blocks() -> list[list[str]]:
    """하단 용어 주석 — 판단 줄에 쓰인 어려운 용어 설명."""
    return [[
        "### 📖 용어 (참고)",
        "**Trailing PER**: 최근 12개월 *실제* 이익 기준(주가 ÷ 지난 1년 EPS). 확정 실적.",
        "**Forward PER**: 향후 12개월 *예상* 이익 기준. Forward < Trailing → 이익성장 기대, 반대 → 이익감소 우려.",
        "**EPS**: 주당순이익(순이익 ÷ 주식 수).",
        "**BB %B (볼린저)**: 15일 이동평균 ±2×표준편차 밴드에서 현재가 위치(하단 0 · 중심 50 · 상단 100).",
        "**52주 위치**: 최근 1년 저가~고가 구간에서 현재가의 백분위.",
        "**공매도 %**: 유통주식 대비 공매도 잔량 비율(높을수록 하락 베팅 큼). ↑ 전월 대비 증가 · ↓ 감소.",
    ]]


def _source_blocks(source_links: dict, bloomberg: list[dict] = None) -> list[list[str]]:
    """맨 끝 Source 모음 — 한국/미국/블룸버그, 제목 하이퍼링크."""
    groups = [
        ("🇰🇷 한국", source_links.get("국내") or []),
        ("🇺🇸 미국", source_links.get("해외") or []),
    ]
    # 블룸버그 주요 기사 몇 개를 하이퍼링크로 (본문 섹션엔 링크 없음)
    bb = [(it["title"], it["link"]) for it in (bloomberg or []) if it.get("link")]
    if bb:
        groups.append(("🏦 블룸버그", bb))

    if not any(items for _, items in groups):
        return []

    def entry(t, l):
        return f"- [{t}]({l})"  # 제목에 하이퍼링크 (긴 URL 숨김)

    blocks, header_done = [], False
    for label, items in groups:
        if not items:
            continue
        lead = [f"**[{label}]**", entry(*items[0])]
        if not header_done:
            lead = ["### 🔗 Source (주요 기사 링크)"] + lead
            header_done = True
        blocks.append(lead)
        for t, l in items[1:]:
            blocks.append([entry(t, l)])
    return blocks


def _headline_blocks(headlines, today) -> list[list[str]]:
    """🔥 오늘의 헤드라인 블록(국내/해외). 없으면 []."""
    if not headlines or not (headlines.get("국내") or headlines.get("해외")):
        return []
    hb = [f"### 🔥 오늘의 헤드라인 ({today})"]
    for region, flag in [("국내", "🇰🇷"), ("해외", "🇺🇸")]:
        hs = headlines.get(region) or []
        if hs:
            if len(hb) > 1:
                hb.append("")   # 지역 그룹 사이 여백(margin) 통일
            hb.append(f"**{flag} {region}**")
            hb += [f"{i}. {t}" for i, t in enumerate(hs, 1)]
    return [hb]


def build_messages(header, today, indices, fear_greed, yahoo, headlines, market, sectors, tickers, bloomberg, source_links, quotes=None, catalysts=None, summary=None) -> list[str]:
    # (P2-8 풀버전) 섹션을 투자 호라이즌 3개 층으로 묶는다 — 혼합 사용자가 '내 층'만 스캔.
    #   divider는 폰트를 키우지 않는 작은 볼드(**━━ … ━━**), _emit이 각 층을 새 메시지로 시작.
    short = (_dashboard_blocks(indices, fear_greed)      # 지금 시장 현황
             + _summary_blocks(summary)                  # 오늘의 핵심(so-what)
             + _watchlist_table_blocks(quotes)           # 관심종목 수치
             + _watchlist_highlights(quotes)             # 주목(급변·밴드 극단)
             + _reversal_warnings(quotes)                # 추격 주의(선반영)
             + _kr_flow_blocks(quotes)                   # 국내 수급
             + _headline_blocks(headlines, today))       # 오늘의 헤드라인
    mid = _catalyst_blocks(catalysts)                    # 예정 촉매(수일~수주)
    long = (_theme_news_blocks(sectors)                  # 테마별 소식(장기 테마)
            + _watchlist_news_blocks(tickers, quotes))   # 관심종목 뉴스

    blocks = [[SEPARATOR, header]]  # 맨 앞 구분선
    if short:
        blocks += [["**━━ 🔴 지금 · 단기 ━━**"]] + short
    if mid:
        blocks += [["**━━ 🟡 중기 · 수일~수주 ━━**"]] + mid
    if long:
        blocks += [["**━━ 🟢 장기 · 테마·펀더멘털 ━━**"]] + long

    # 부록(호라이즌 밖) — Source 링크 · 용어 · 면책
    blocks += _source_blocks(source_links)
    blocks += _glossary_blocks()  # 용어 주석
    blocks.append([DISCLAIMER])  # (P0-3) 면책 — 매수/매도 신호 아님
    blocks.append([SEPARATOR])   # 맨 뒤 구분선
    return _emit(blocks)


def send(messages: list[str]) -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("⚠️  DISCORD_WEBHOOK_URL 미설정 — 콘솔에만 출력합니다.\n")
        print("\n\n──────────\n\n".join(messages))
        return

    for msg in messages:
        # 429(요청 과다) 발생 시 디스코드가 알려주는 시간만큼 기다렸다 재전송
        while True:
            # flags=4 (SUPPRESS_EMBEDS): 링크 미리보기 카드 표시 안 함
            resp = requests.post(webhook, json={"content": msg, "flags": 4}, timeout=20)
            if resp.status_code == 429:
                wait = resp.json().get("retry_after", 1) + 0.5
                print(f"⏳ rate limit — {wait:.1f}초 대기 후 재시도")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        time.sleep(0.7)  # 메시지 사이 간격을 둬 rate limit 예방
    print(f"✅ 디스코드로 {len(messages)}개 메시지를 보냈습니다.")

"""모은 뉴스를 디스코드 채널(웹훅)로 보내는 모듈.

가독성 우선 레이아웃:
· 국내 / 해외를 최상위로 분리 (각 지역은 새 메시지로 시작)
· 디스코드 마크다운 헤더(#, ##)로 영역을 시각적으로 구분
· 기사는 '제목 + 발췌문(인용)' 텍스트, 링크는 맨 위 Yahoo 헤드라인 1개뿐
"""

import os
import time
import requests

from config import EXCERPT_MAX_LEN, SOURCE_BLOOMBERG

DISCORD_LIMIT = 1900  # 디스코드 메시지 길이 제한(2000)보다 약간 작게

SEPARATOR = "┄" * 42  # 구분선(점선) — 길게 꽉 채움

REGIONS = [("국내", "# 🇰🇷 국내 증시"), ("해외", "# 🇺🇸 해외 증시")]


def _fmt_chg(c: float) -> str:
    arrow = "▲" if c > 0 else ("▼" if c < 0 else "─")
    return f"{arrow} {c:+.2f}%"


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
        b = ["## 📊 주요 지수 시세"]
        for ix in indices:
            b.append(f"{ix['flag']} **{ix['name']}**  {ix['price']:,.2f}  {_fmt_chg(ix['chg'])}")
        blocks.append(b)

    if fear_greed and fear_greed.get("score") is not None:
        score = fear_greed["score"]
        zone, emoji = _fg_zone(score)
        filled = round(score / 100 * 20)
        bar = "█" * filled + "░" * (20 - filled)
        fb = [
            f"## {emoji} 공포탐욕지수 (CNN)",
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


def _cut(text: str, maxlen: int) -> str:
    text = text.strip()
    if len(text) <= maxlen:
        return text
    cut = text[:maxlen]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def _item_blocks(items: list[dict]) -> list[list[str]]:
    """기사 1건 = ['• 제목 (출처)'] (발췌문 있으면 다음 줄) 묶음. 묶음은 분할되지 않는다.
    같은 소제목 안의 기사들은 붙이고, 그룹 끝에 빈 줄을 둬 다음 소제목과 분리한다.
    """
    blocks = []
    for it in items:
        src = f" ({it['source']})" if it["source"] else ""
        block = [f"• {it['title']}{src}"]
        if it["excerpt"]:
            block.append(_cut(it["excerpt"], EXCERPT_MAX_LEN))
        blocks.append(block)
    if blocks:
        blocks[-1] = blocks[-1] + [""]  # 그룹 끝 빈 줄
    return blocks


def _section(header: str, labeled_groups: list) -> list[list[str]]:
    """섹션 헤더 + 소제목이 항상 첫 기사와 붙어 다니도록 블록을 구성.
    labeled_groups: [(소제목, items), ...]
    """
    out, pending = [], header
    for label, items in labeled_groups:
        ibs = _item_blocks(items)
        lead = [label] + (ibs[0] if ibs else [])
        if pending:
            lead = [pending] + lead
            pending = None
        out.append(lead)
        out += ibs[1:]
    return out  # 빈 섹션이면 빈 리스트


def _region_blocks(title, market, sectors_grouped, tickers) -> list[list[str]]:
    blocks = [[title]]
    if market:
        blocks += _section("## 📰 시장 뉴스",
                           [(f"**{g['label']}**", g["items"]) for g in market])
    if sectors_grouped:
        blocks += _section("## 🏭 섹터별 소식",
                           [(f"**{lbl}**", items) for lbl, items in sectors_grouped])
    if tickers:
        blocks += _section("## ⭐ 관심 종목",
                           [(f"**{g['label']}**", g["items"]) for g in tickers])
    return blocks


def _emit(blocks: list[list[str]]) -> list[str]:
    """블록을 디스코드 한도에 맞춰 메시지로 합친다.
    · 한 블록(제목+발췌문)은 쪼개지 않음
    · '# '로 시작하는 지역 헤더는 항상 새 메시지로 시작
    """
    messages, current = [], ""
    for block in blocks:
        text = "\n".join(block)
        force_break = bool(block) and block[0].startswith("# ")
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

        # 섹션(##/#) 앞에 빈 줄 2개를 둬서 영역 구분을 확실히
        sep = "\n\n" if (current and is_header) else ""
        if current and len(current) + len(sep) + len(text) + 1 > DISCORD_LIMIT:
            messages.append(current.rstrip())
            current = ""
            sep = ""
        current += sep + text + "\n"

    if current.strip():
        messages.append(current.rstrip())
    return messages


def _bloomberg_blocks(items: list[dict]) -> list[list[str]]:
    """해외 섹션 내 블룸버그 하위 섹션: 제목 + 요약(번역) + <링크>."""
    out = []
    for i, it in enumerate(items):
        b = [f"• {it['title']} (Bloomberg)"]
        if it.get("excerpt"):
            b.append(_cut(it["excerpt"], EXCERPT_MAX_LEN))
        # 링크는 본문에 달지 않고 Source 영역에서만 제공
        if i == 0:
            b = ["## 🏦 블룸버그 주요 기사"] + b  # 헤더를 첫 기사와 묶어 고아 방지
        out.append(b)
    return out


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
            lead = ["## 🔗 Source (주요 기사 링크)"] + lead
            header_done = True
        blocks.append(lead)
        for t, l in items[1:]:
            blocks.append([entry(t, l)])
    return blocks


def build_messages(header, today, indices, fear_greed, yahoo, headlines, market, sectors, tickers, bloomberg, source_links) -> list[str]:
    blocks = [[SEPARATOR, header]]  # 맨 앞 구분선

    # 📊 맨 위 대시보드 — 주요 지수 시세 + 공포탐욕지수
    blocks += _dashboard_blocks(indices, fear_greed)

    # 0) 대표 링크 — Yahoo Finance 헤드라인 1개 (브리핑 내 유일한 링크)
    if yahoo:
        b = ["## 📈 Yahoo Finance 대표 헤드라인", yahoo["title"]]
        if yahoo["link"]:
            b.append(yahoo["link"])
        blocks.append(b)

    # 1) 오늘의 헤드라인 — 국내/해외 분리
    if headlines and (headlines.get("국내") or headlines.get("해외")):
        hb = [f"## 🔥 오늘의 헤드라인 ({today})"]
        for region, flag in [("국내", "🇰🇷"), ("해외", "🇺🇸")]:
            hs = headlines.get(region) or []
            if hs:
                hb.append(f"**{flag} {region}**")
                hb += [f"{i}. {t}" for i, t in enumerate(hs, 1)]
        blocks.append(hb)

    # 2~4) 지역별 묶음 (각 지역은 새 메시지로 시작)
    for region, title in REGIONS:
        m = [g for g in market if g["region"] == region and g["items"]]

        # 섹터: 같은 지역의 섹터들을 SECTORS 순서대로 묶음
        sg, order = {}, []
        for g in sectors:
            if g["region"] != region or not g["items"]:
                continue
            if g["label"] not in sg:
                sg[g["label"]] = []
                order.append(g["label"])
            sg[g["label"]] += g["items"]
        sectors_grouped = [(lbl, sg[lbl]) for lbl in order]

        t = [g for g in tickers if g["region"] == region and g["items"]]
        bb = bloomberg if region == "해외" else []  # 블룸버그는 해외 섹션에만

        if m or sectors_grouped or t or bb:
            blocks += _region_blocks(title, m, sectors_grouped, t)
            blocks += _bloomberg_blocks(bb)

    # 맨 끝 Source 링크 모음 (블룸버그 주요 기사도 하이퍼링크로 포함)
    blocks += _source_blocks(source_links, bloomberg[:SOURCE_BLOOMBERG])

    blocks.append([SEPARATOR])  # 맨 뒤 구분선
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

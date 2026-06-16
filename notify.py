"""모은 뉴스를 디스코드 채널(웹훅)로 보내는 모듈.

가독성 우선 레이아웃:
· 국내 / 해외를 최상위로 분리 (각 지역은 새 메시지로 시작)
· 디스코드 마크다운 헤더(#, ##)로 영역을 시각적으로 구분
· 기사는 '제목 + 발췌문(인용)' 텍스트, 링크는 맨 위 Yahoo 헤드라인 1개뿐
"""

import os
import time
import requests

DISCORD_LIMIT = 1900  # 디스코드 메시지 길이 제한(2000)보다 약간 작게

REGIONS = [("국내", "# 🇰🇷 국내 증시"), ("해외", "# 🇺🇸 해외 증시")]


def _item_blocks(items: list[dict]) -> list[list[str]]:
    """기사 1건 = ['• 제목 (출처)', '> 발췌문'] 묶음. 이 묶음은 분할되지 않는다."""
    blocks = []
    for it in items:
        src = f" ({it['source']})" if it["source"] else ""
        block = [f"• {it['title']}{src}"]
        if it["excerpt"]:
            block.append(f"> {it['excerpt']}")
        blocks.append(block)
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
        blocks += _section("## 📊 시장 지수",
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

        if current and len(current) + len(text) + 1 > DISCORD_LIMIT:
            messages.append(current.rstrip())
            current = ""
        current += text + "\n"

    if current.strip():
        messages.append(current.rstrip())
    return messages


def build_messages(header, yahoo, headlines, market, sectors, tickers) -> list[str]:
    blocks = [[header]]

    # 0) 대표 링크 — Yahoo Finance 헤드라인 1개 (브리핑 내 유일한 링크)
    if yahoo:
        b = ["## 📈 Yahoo Finance 대표 헤드라인", yahoo["title"]]
        if yahoo["link"]:
            b.append(yahoo["link"])
        blocks.append(b)

    # 1) 오늘의 헤드라인 — 국내/해외 분리
    if headlines and (headlines.get("국내") or headlines.get("해외")):
        hb = ["## 🔥 오늘의 헤드라인"]
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

        if m or sectors_grouped or t:
            blocks += _region_blocks(title, m, sectors_grouped, t)

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
            resp = requests.post(webhook, json={"content": msg}, timeout=20)
            if resp.status_code == 429:
                wait = resp.json().get("retry_after", 1) + 0.5
                print(f"⏳ rate limit — {wait:.1f}초 대기 후 재시도")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        time.sleep(0.7)  # 메시지 사이 간격을 둬 rate limit 예방
    print(f"✅ 디스코드로 {len(messages)}개 메시지를 보냈습니다.")

"""모은 뉴스를 디스코드 채널(웹훅)로 보내는 모듈.

가독성을 위해 기사는 '제목 + 발췌문(인용 블록)' 텍스트로 보여주고,
링크는 맨 위 Yahoo Finance 대표 헤드라인 1개만 첨부한다.
"""

import os
import time
import requests

DISCORD_LIMIT = 1900  # 디스코드 메시지 길이 제한(2000)보다 약간 작게


def _item_blocks(items: list[dict], show_region: bool = False) -> list[list[str]]:
    """기사 1건 = ['제목 (출처)', '> 발췌문'] 묶음. 이 묶음은 분할되지 않는다."""
    blocks = []
    for it in items:
        tag = f"[{it['region']}] " if show_region else "• "
        src = f" ({it['source']})" if it["source"] else ""
        block = [f"{tag}{it['title']}{src}"]
        if it["excerpt"]:
            block.append(f"> {it['excerpt']}")
        blocks.append(block)
    return blocks


def _emit(blocks: list[list[str]]) -> list[str]:
    """블록(여러 줄 묶음)을 디스코드 한도에 맞춰 메시지로 합치되, 한 블록은 쪼개지 않는다."""
    messages, current = [], ""
    for block in blocks:
        text = "\n".join(block)
        if len(text) > DISCORD_LIMIT:  # 블록 하나가 한도를 넘으면 어쩔 수 없이 글자수로 분할
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
    blocks = [[header, ""]]

    # 0) 대표 링크 — Yahoo Finance 헤드라인 1개 (브리핑 내 유일한 링크)
    if yahoo:
        head = ["📈 __Yahoo Finance 대표 헤드라인__", yahoo["title"]]
        if yahoo["link"]:
            head.append(yahoo["link"])
        head.append("")
        blocks.append(head)

    # 1) 헤드라인 모음
    if headlines:
        block = ["🔥 __오늘의 헤드라인__"]
        block += [f"{i}. {h}" for i, h in enumerate(headlines, 1)]
        block.append("")
        blocks.append(block)

    # 2) 시장 전체
    market_groups = [g for g in market if g["items"]]
    if market_groups:
        blocks.append(["📊 __시장 전체 주요 이슈__"])
        for g in market_groups:
            blocks.append([f"**{g['label']}**"])
            blocks += _item_blocks(g["items"])
        blocks.append([""])

    # 3) 섹터별 (같은 섹터의 국내/해외를 한 묶음으로)
    grouped, order = {}, []
    for g in sectors:
        if g["label"] not in grouped:
            grouped[g["label"]] = []
            order.append(g["label"])
        grouped[g["label"]] += g["items"]
    sector_blocks = []
    for label in order:
        items = grouped[label]
        if not items:
            continue
        sector_blocks.append([f"**{label}**"])
        sector_blocks += _item_blocks(items, show_region=True)
    if sector_blocks:
        blocks.append(["🏭 __섹터별 주요 소식__"])
        blocks += sector_blocks
        blocks.append([""])

    # 4) 관심 종목
    ticker_groups = [g for g in tickers if g["items"]]
    if ticker_groups:
        blocks.append(["⭐ __관심 종목 소식__"])
        for g in ticker_groups:
            blocks.append([f"**{g['label']}** [{g['region']}]"])
            blocks += _item_blocks(g["items"])

    return _emit(blocks)


def send(messages: list[str]) -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("⚠️  DISCORD_WEBHOOK_URL 미설정 — 콘솔에만 출력합니다.\n")
        print("\n\n".join(messages))
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

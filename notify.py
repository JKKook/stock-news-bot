"""모은 뉴스를 디스코드 채널(웹훅)로 보내는 모듈.

링크는 임베디드(미리보기 카드) 없이 순수 링크만 <...> 형태로 첨부한다.
"""

import os
import requests

DISCORD_LIMIT = 1900  # 디스코드 메시지 길이 제한(2000)보다 약간 작게


def _item_lines(items: list[dict], show_region: bool = False) -> list[str]:
    """기사 1건 = '제목 — 출처' 줄 + '<링크>' 줄."""
    lines = []
    for it in items:
        tag = f"[{it['region']}] " if show_region else "• "
        src = f" — {it['source']}" if it["source"] else ""
        lines.append(f"{tag}{it['title']}{src}")
        if it["link"]:
            lines.append(f"<{it['link']}>")
    return lines


def build_messages(header, headlines, market, sectors, tickers) -> list[str]:
    lines = [header, ""]

    # 1) 헤드라인
    if headlines:
        lines.append("🔥 __오늘의 헤드라인__")
        for i, h in enumerate(headlines, 1):
            lines.append(f"{i}. {h}")
        lines.append("")

    # 2) 시장 전체
    market_groups = [g for g in market if g["items"]]
    if market_groups:
        lines.append("📊 __시장 전체 주요 이슈__")
        for g in market_groups:
            lines.append(f"**{g['label']}**")
            lines += _item_lines(g["items"])
        lines.append("")

    # 3) 섹터별 (같은 섹터의 국내/해외를 한 묶음으로)
    grouped, order = {}, []
    for g in sectors:
        if g["label"] not in grouped:
            grouped[g["label"]] = []
            order.append(g["label"])
        grouped[g["label"]] += g["items"]
    sector_lines = []
    for label in order:
        items = grouped[label]
        if not items:
            continue
        sector_lines.append(f"**{label}**")
        sector_lines += _item_lines(items, show_region=True)
    if sector_lines:
        lines.append("🏭 __섹터별 주요 소식__")
        lines += sector_lines
        lines.append("")

    # 4) 관심 종목
    ticker_groups = [g for g in tickers if g["items"]]
    if ticker_groups:
        lines.append("⭐ __관심 종목 소식__")
        for g in ticker_groups:
            lines.append(f"**{g['label']}** [{g['region']}]")
            lines += _item_lines(g["items"])

    # 줄 단위로 디스코드 한도에 맞춰 나누기
    messages, current = [], ""
    for line in lines:
        piece = line[:DISCORD_LIMIT]
        if current and len(current) + len(piece) + 1 > DISCORD_LIMIT:
            messages.append(current.rstrip())
            current = ""
        current += piece + "\n"
    if current.strip():
        messages.append(current.rstrip())
    return messages


def send(messages: list[str]) -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("⚠️  DISCORD_WEBHOOK_URL 미설정 — 콘솔에만 출력합니다.\n")
        print("\n\n".join(messages))
        return

    for msg in messages:
        resp = requests.post(webhook, json={"content": msg}, timeout=20)
        resp.raise_for_status()
    print(f"✅ 디스코드로 {len(messages)}개 메시지를 보냈습니다.")

"""모은 뉴스를 디스코드 채널(웹훅)로 보내는 모듈."""

import os
import requests

DISCORD_LIMIT = 1900  # 디스코드 메시지 길이 제한(2000)보다 약간 작게


def _section_lines(section: dict) -> list[str]:
    lines = [f"**{section['label']}**"]
    for it in section["items"]:
        src = f" ({it['source']})" if it["source"] else ""
        lines.append(f"• [{it['title']}]({it['link']}){src}")
    return lines


def build_messages(market: list[dict], tickers: list[dict], header: str) -> list[str]:
    """디스코드 글자 수 제한에 맞춰 '줄 단위'로 여러 메시지로 나눠 반환."""
    lines = [header, ""]
    if market:
        lines.append("📊 __시장 전체 주요 이슈__")
        for s in market:
            lines += _section_lines(s)
        lines.append("")
    if tickers:
        lines.append("⭐ __관심 종목 소식__")
        for s in tickers:
            lines += _section_lines(s)

    messages, current = [], ""
    for line in lines:
        # 한 줄(기사 1건)이 한도를 통째로 넘으면 그 줄만 잘라 담는다
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
        print("⚠️  DISCORD_WEBHOOK_URL 이 설정되지 않아 콘솔에만 출력합니다.\n")
        print("\n\n".join(messages))
        return

    for msg in messages:
        resp = requests.post(webhook, json={"content": msg}, timeout=20)
        resp.raise_for_status()
    print(f"✅ 디스코드로 {len(messages)}개 메시지를 보냈습니다.")

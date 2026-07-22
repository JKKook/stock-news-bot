"""테스트 채널 전용 발송 — DISCORD_TEST_WEBHOOK_URL 로만 보낸다(실제 발송 채널은 안 건드림).

UI 미리보기를 실제 채널과 분리해 테스트하기 위한 개발 도구.
· .env 의 DISCORD_TEST_WEBHOOK_URL(테스트 채널 웹후크)을 사용.
· notify.send 를 재사용하므로 메시지 분할·429 재시도 로직 그대로.
· 메시지 앞에 '🧪 [테스트]' 표기가 자동으로 붙는다.

사용:
    from test_send import test_send
    test_send("아무 내용")                 # 문자열 1건
    test_send(["여러", "메시지"])           # 리스트
    test_send(lines, label="관심종목 표")   # 라벨 지정
"""

import os

# .env 로드 (환경에 이미 있으면 유지)
try:
    with open(".env", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
except FileNotFoundError:
    pass

import notify  # noqa: E402


def test_send(content, label: str = "UI 테스트") -> None:
    url = os.environ.get("DISCORD_TEST_WEBHOOK_URL")
    if not url:
        print("⚠️  DISCORD_TEST_WEBHOOK_URL 미설정 — .env에 테스트 채널 웹후크를 추가하세요.")
        print("    (디스코드: 테스트 채널 → 설정 → 연동 → 웹후크 → URL 복사)")
        return
    os.environ["DISCORD_WEBHOOK_URL"] = url   # notify.send 가 이 값을 읽어 발송

    banner = f"{notify.SEPARATOR}\n🧪 **[테스트 중] {label}** (실제 발송 아님)\n"
    footer = "\n-# 🧪 UI 테스트용 · 정식 발송 아님"
    msgs = content if isinstance(content, list) else [content]
    if msgs:
        msgs = [banner + msgs[0]] + msgs[1:]
        msgs[-1] = msgs[-1] + footer
    notify.send(msgs)

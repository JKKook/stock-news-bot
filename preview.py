"""브리핑 UI 미리보기 — 디스코드 발송 없이 콘솔로만 출력.

사용:  .venv/bin/python preview.py
· .env(DEEPL_API_KEY·FRED_API_KEY·GEMINI_API_KEY)를 자동 로드
· DISCORD_WEBHOOK_URL을 강제로 비워 send()가 콘솔에만 출력하도록 한다
"""

import os

# .env 로드 (간단 파서 — 이미 환경에 있으면 유지)
try:
    with open(".env", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
except FileNotFoundError:
    pass

os.environ.pop("DISCORD_WEBHOOK_URL", None)   # 웹훅 무시 → 콘솔 출력 모드

import main
main.main()

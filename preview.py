"""브리핑 UI 미리보기 — 디스코드 발송 없이 콘솔로만 출력.

기본:  .venv/bin/python preview.py                                  (전체 브리핑)

시장별 리서치 노트(실제 발송과 동일):
  BRIEF_FOCUS=KR BRIEF_KIND=view    ... preview.py   # 📑 [마켓 뷰] 코스피 개장 전
  BRIEF_FOCUS=KR BRIEF_KIND=closing ... preview.py   # 📑 [마켓 클로징] 코스피 마감
  BRIEF_FOCUS=US BRIEF_KIND=view    ... preview.py   # 📑 [마켓 뷰] 나스닥 개장
  BRIEF_FOCUS=US BRIEF_KIND=closing ... preview.py   # 📑 [마켓 클로징] 나스닥 마감

주말·공휴일엔 가드에 막히므로 FORCE_BRIEFING=1 을 함께 주면 강제 출력된다.

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

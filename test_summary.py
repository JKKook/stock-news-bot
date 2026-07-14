"""AI 요약(기사요약·한줄총평·핵심이슈)만 빠르게 테스트 — 전체 파이프라인 없이 3초.

사용:
  .venv/bin/python test_summary.py                 # 마켓 클로징(분석 톤)
  .venv/bin/python test_summary.py view            # 마켓 뷰(전망 톤)
  .venv/bin/python test_summary.py closing         # 마켓 클로징(분석 톤)

아래 SAMPLE_* 값을 직접 고쳐가며 프롬프트 결과를 바로 확인할 수 있다.
(.env 자동 로드 — GEMINI_API_KEY 필요)
"""

import os
import sys

# .env 로드
try:
    with open(".env", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
except FileNotFoundError:
    pass

from summarize import summarize, market_context   # noqa: E402

# ── 여기를 고쳐가며 테스트 ──────────────────────────────────────
SAMPLE_HEADLINES = {
    "국내": [
        "코스피 장중 6,600선 붕괴 후 자율 반등…외국인 순매수 전환",
        "SK하이닉스 2분기 실적 시장 기대치 하회 전망…장기 계약은 확대",
        "메타, 대규모 AI 데이터센터 투자 확대 발표",
        "7월 1~10일 수출 전년 대비 54% 증가…반도체 193%↑",
    ],
    "해외": [],
}

SAMPLE_INDICES = [
    {"name": "코스피", "chg": +1.86},
    {"name": "코스닥", "chg": -0.72},
    {"name": "나스닥 야간선물", "chg": +0.17},
]
SAMPLE_FG = {"score": 49, "rating": "neutral"}
SAMPLE_FLOW = {
    "코스피": {"personal": -8_200, "foreign": +5_100, "institution": +3_400},
    "코스닥": {"personal": +1_800, "foreign": -900, "institution": -700},
}
SAMPLE_QUOTES = {
    "SK하이닉스": {"chg": -7.34},
    "삼성전자": {"chg": +2.10},
}
# ────────────────────────────────────────────────────────────

kind = (sys.argv[1] if len(sys.argv) > 1 else "closing").lower()
label = {"view": "🔭 마켓 뷰 (전망 톤)", "closing": "🧠 마켓 클로징 (분석 톤)"}.get(kind, kind)

ctx = market_context(SAMPLE_INDICES, SAMPLE_FG, SAMPLE_FLOW, SAMPLE_QUOTES, region="국내")
print(f"═══ {label} ═══\n")
print("[프롬프트에 들어가는 시장 데이터]")
print(ctx, "\n")

s = summarize(SAMPLE_HEADLINES, ctx, kind)
if not s:
    print("❌ 요약 실패 (GEMINI_API_KEY 확인 또는 쿼터 초과)")
    raise SystemExit(1)

print("📰 AI 기사 요약")
print(" ", s.get("news_digest") or "(없음)")
print()
print("🧠 한 줄 총평" if kind != "view" else "🔭 한 줄 전망")
print(" >", s.get("verdict") or "(없음)")
print()
print("✔ 핵심 이슈")
for kp in s.get("key_points") or ["(없음)"]:
    print("  ·", kp)
print()
print("🧭 so-what 3줄 (정규 브리핑용)")
for k, name in [("what_changed", "무엇이 바뀌었나"), ("why_matters", "왜 중요한가"), ("watch", "무엇을 지켜볼까")]:
    print(f"  {name}: {s.get(k) or '(없음)'}")

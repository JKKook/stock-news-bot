"""브리핑 상단 'so-what' 요약 (P1) — Google Gemini(무료 티어)로 헤드라인을 3줄로 종합.

  무엇이 바뀌었나 / 왜 중요한가 / 무엇을 지켜볼까

· 무료: Google AI Studio(aistudio.google.com)에서 GEMINI_API_KEY 발급(결제 불필요).
· 새 SDK 없이 기존 requests로 Gemini REST 호출 — 구조화 출력(responseSchema) 사용.

리서치 근거:
· 원시 헤드라인은 '발생'만 전할 뿐 판단 재료(맥락·중요도)가 없다 → so-what 레이어로 보완.
· '품질 필터 후 요약'이 필수 — 여기 입력은 이미 수집·중복제거된 상위 헤드라인만 넣는다.

가드레일(환각·조언 방지):
· 제공된 헤드라인만 근거. 없는 사실·수치·종목 지어내기 금지. 주가 예측·매수/매도 판단 금지.
· 근거 부족하면 그렇게 밝힌다. GEMINI_API_KEY 없거나 실패 시 None → 섹션 생략(봇 안 멈춤).
"""

import os
import json
import hashlib
from datetime import datetime, timezone, timedelta

import requests

import config

_KEY = os.environ.get("GEMINI_API_KEY")
_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_CACHE_FILE = "summary_cache.json"

_SYSTEM = (
    "너는 한국 개인투자자를 위한 시장 브리핑 요약가다. "
    "아래에 주어진 '오늘의 헤드라인'만 근거로 요약한다. "
    "헤드라인에 없는 사실·수치·종목명을 지어내지 마라. "
    "주가 예측이나 매수/매도/보유 판단은 절대 하지 마라 — 정보 제공과 맥락 정리만 한다. "
    "근거가 부족하면 '아직 판단하기 이른 상황'이라고 솔직히 밝혀라. "
    "각 항목은 반드시 1문장으로 핵심만 압축한다(최대 2문장, 사실 나열 금지). "
    # (P1-6) 사건 → 관심 섹터/종목 연결
    "affected에는 오늘 헤드라인이 '관심 섹터/종목' 목록 중 무엇과 직접 연결되는지만 적는다. "
    "헤드라인에 근거가 명확할 때만 고르고, 근거 없으면 빈 문자열로 둔다. "
    "형식 예: '반도체(SK하이닉스·한미반도체), AI 인프라'. 없으면 ''."
)

# Gemini responseSchema (Type enum은 대문자)
_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "what_changed": {"type": "STRING"},
        "why_matters": {"type": "STRING"},
        "watch": {"type": "STRING"},
        "affected": {"type": "STRING"},
    },
    "required": ["what_changed", "why_matters", "watch", "affected"],
    "propertyOrdering": ["what_changed", "why_matters", "watch", "affected"],
}


# (D-5) 조언성 표현 denylist — 프롬프트 가드가 뚫렸을 때의 안전망.
#   '순매수'·'매수세'(서술) 와 '매수 추천'(조언)을 구분하려고 구체 패턴만 넣는다.
_ADVICE_PATTERNS = [
    "매수 추천", "매도 추천", "매수하세요", "매도하세요", "사야", "팔아야",
    "목표주가", "투자의견", "비중 확대", "비중 축소", "비중확대", "비중축소",
    "강력 매수", "저점 매수", "지금 사", "손절", "익절", "추천합니다", "추천드립니다",
]


def _guard(summary: dict | None) -> dict | None:
    """조언성 표현이 섞인 항목은 드롭(D-5). 전부 드롭되면 None."""
    if not summary:
        return summary
    cleaned = {}
    for k, v in summary.items():
        hit = next((p for p in _ADVICE_PATTERNS if p in v), None)
        if hit:
            print(f"⚠️  요약 가드: '{k}'에서 조언성 표현('{hit}') 감지 → 해당 항목 제외")
            continue
        cleaned[k] = v
    # 핵심 3필드가 모두 사라지면 요약 자체를 생략
    if not any(cleaned.get(k) for k in ("what_changed", "why_matters", "watch")):
        return None
    return cleaned


def _cache_get(key: str):
    """헤드라인 해시로 최근(SUMMARY_CACHE_TTL_H시간 내) 요약 재사용 — 중복 호출/429 방지."""
    if not config.SUMMARY_CACHE_TTL_H:
        return None
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            e = json.load(f).get(key)
        if e and (datetime.now(timezone.utc) - datetime.fromisoformat(e["ts"])
                  < timedelta(hours=config.SUMMARY_CACHE_TTL_H)):
            return e["data"]
    except Exception:
        pass
    return None


def _cache_put(key: str, data: dict) -> None:
    """요약 캐시 저장 + 만료 항목 정리(파일 비대화 방지). 실패해도 무시(봇 안 멈춤)."""
    if not config.SUMMARY_CACHE_TTL_H:
        return
    try:
        now = datetime.now(timezone.utc)
        cache = {}
        try:
            with open(_CACHE_FILE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
        cache = {k: v for k, v in cache.items()
                 if (now - datetime.fromisoformat(v["ts"])
                     < timedelta(hours=config.SUMMARY_CACHE_TTL_H))}
        cache[key] = {"ts": now.isoformat(), "data": data}
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass


def _call_model(model: str, body: dict) -> dict | None:
    """단일 Gemini 모델 호출 → 요약 dict(또는 빈 값이면 None). 실패 시 예외 발생(폴백이 잡음)."""
    r = requests.post(_URL.format(model=model),
                      params={"key": _KEY}, json=body, timeout=20)
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    text = "".join(p.get("text", "") for p in parts)
    data = json.loads(text)
    out = {k: (data.get(k) or "").strip()
           for k in ("what_changed", "why_matters", "watch", "affected")}
    return out if any(out.values()) else None


def _watchlist_context() -> str:
    """관심 섹터·종목 목록을 프롬프트용 텍스트로 (P1-6 사건→관심대상 매핑용)."""
    sectors = ", ".join(config.SECTORS.keys())
    tickers = ", ".join(label for label, *_ in config.TICKERS)
    return f"관심 섹터: {sectors}\n관심 종목: {tickers}"


def summarize(headlines: dict) -> dict | None:
    """{'국내':[제목...], '해외':[제목...]} → {'what_changed','why_matters','watch'} 또는 None."""
    if not _KEY:
        return None
    ko = headlines.get("국내") or []
    en = headlines.get("해외") or []
    if not ko and not en:
        return None

    lines = []
    if ko:
        lines += ["[국내]"] + [f"- {t}" for t in ko]
    if en:
        lines += ["[해외]"] + [f"- {t}" for t in en]
    prompt = ("오늘의 헤드라인:\n" + "\n".join(lines)
              + "\n\n" + _watchlist_context())

    body = {
        "system_instruction": {"parts": [{"text": _SYSTEM}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _SCHEMA,
            "maxOutputTokens": 1024,
            "temperature": 0.3,
            # 2.5-flash는 thinking을 기본 소비 → 단순 요약엔 불필요, 꺼서 출력 잘림 방지
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    # (P4-1) 캐시 히트 시 API 호출 없이 반환(동일 헤드라인 중복실행·테스트 보호)
    cache_key = hashlib.md5(prompt.encode("utf-8")).hexdigest()
    cached = _cache_get(cache_key)
    if cached is not None:
        return _guard(cached)

    # (P4-1) 모델 폴백 체인 — 1차 실패/429면 다음 모델로. 전부 실패해야 생략.
    last_err = None
    for model in [config.SUMMARY_MODEL, *config.SUMMARY_FALLBACK_MODELS]:
        try:
            out = _call_model(model, body)
            if out:
                _cache_put(cache_key, out)
                if model != config.SUMMARY_MODEL:
                    print(f"ℹ️  요약 폴백 모델 사용: {model}")
                return _guard(out)   # D-5 조언 가드
        except Exception as e:
            last_err = e
            print(f"⚠️  요약 모델 {model} 실패({str(e)[:90]}) — 다음 폴백 시도")
    print(f"⚠️  모든 요약 모델 실패 — 요약 섹션 생략 (마지막 오류: {last_err})")
    return None

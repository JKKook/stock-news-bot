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
    "형식 예: '반도체(SK하이닉스·한미반도체), AI 인프라'. 없으면 ''. "
    # (한 줄 총평) 오늘이 '어떤 시장'인지 규정
    "verdict는 '한 줄 총평'이다 — 아래 '오늘의 시장 데이터'(지수·수급·공포탐욕·급변 종목)와 헤드라인을 "
    "종합해 **오늘이 어떤 시장인지를 딱 한 문장**으로 규정한다. "
    "수치를 나열하지 말고 시장의 '성격'을 짚어라(예: 위험회피, 관망, 순환매, 테마 쏠림, 저가매수 유입, "
    "외국인 이탈 속 개인 방어 등). 주가 예측·매매 판단은 절대 금지. 데이터가 빈약하면 '방향성 판단 이른 장세'라고 적어라. "
    # (리서치 노트 Summary) 핵심 이슈 불릿
    "key_points는 오늘 시장의 '핵심 이슈' 2~3개다 — 수급(외국인·기관·개인), 환율·금리, 매크로 지표, "
    "지정학/정책 등 **지수 등락 외의 굵직한 사실**을 각 한 줄로. 반드시 주어진 헤드라인·시장 데이터에 "
    "근거해야 하며 지어내지 마라. 종목 개별 등락은 넣지 마라(별도 섹션에서 다룬다). "
    "형식 예: '외국인 8거래일째 순매도..달러/원 1,550원 재돌파'. 근거가 없으면 빈 배열. "
    # (AI 기사 요약) 최상단 서술형 문단
    "news_digest는 '오늘의 기사 요약'이다 — 주어진 헤드라인들을 종합해 **2~3문장의 자연스러운 서술형 문단**으로 "
    "요약한다(불릿 금지, 이어지는 문장으로). 오늘 시장을 움직인 핵심 사건과 그 함의를 기자처럼 담담하게 서술하라. "
    "헤드라인에 없는 사실·수치는 절대 지어내지 마라. 주가 예측·매매 판단 금지."
)

# Gemini responseSchema (Type enum은 대문자)
_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "what_changed": {"type": "STRING"},
        "why_matters": {"type": "STRING"},
        "watch": {"type": "STRING"},
        "affected": {"type": "STRING"},
        "verdict": {"type": "STRING"},     # 한 줄 총평 — 오늘이 어떤 시장인지
        "key_points": {"type": "ARRAY", "items": {"type": "STRING"}},  # 핵심 이슈 2~3개
        "news_digest": {"type": "STRING"},   # AI 기사 요약 — 서술형 2~3문장
    },
    "required": ["what_changed", "why_matters", "watch", "affected", "verdict",
                 "key_points", "news_digest"],
    "propertyOrdering": ["news_digest", "verdict", "key_points",
                         "what_changed", "why_matters", "watch", "affected"],
}

_FIELDS = ("what_changed", "why_matters", "watch", "affected", "verdict", "news_digest")


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
        if isinstance(v, list):                      # key_points — 항목별로 검사
            cleaned[k] = [x for x in v
                          if not any(p in x for p in _ADVICE_PATTERNS)]
            continue
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
    out = {k: (data.get(k) or "").strip() for k in _FIELDS}
    out["key_points"] = [str(x).strip() for x in (data.get("key_points") or []) if str(x).strip()]
    return out if any(out.values()) else None


def _today_kst() -> str:
    return f"{datetime.now(timezone.utc) + timedelta(hours=9):%Y-%m-%d}"


def _judge_call(sysmsg: str, payload: str, item_props: dict, required: list) -> list | None:
    """공통 배열 판정 호출 — 후보 배열 → 판정 배열(JSON). 키 없거나 실패 시 None(→ 호출측이 폴백)."""
    if not _KEY:
        return None
    body = {
        "system_instruction": {"parts": [{"text": sysmsg}]},
        "contents": [{"parts": [{"text": payload}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {"type": "ARRAY", "items": {
                "type": "OBJECT", "properties": item_props, "required": required}},
            "maxOutputTokens": 1024, "temperature": 0.1,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    for model in [config.SUMMARY_MODEL, *config.SUMMARY_FALLBACK_MODELS]:
        try:
            r = requests.post(_URL.format(model=model), params={"key": _KEY}, json=body, timeout=25)
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            data = json.loads("".join(p.get("text", "") for p in parts))
            return data if isinstance(data, list) else None
        except Exception:
            continue
    return None


def judge_sector(candidates: list[dict]) -> list[dict] | None:
    """(섹터 속보 2차 판정) 각 후보가 정말 '엄청난' 호재/악재/경쟁위협인지 + 오늘 새 사건인지.
    candidates: [{"title":.., "sector":..}]. 반환 [{"i":idx, "verdict":호재|악재|경쟁위협|무시, "reason":..}] 또는 None(폴백)."""
    if not _KEY or not candidates:
        return None
    sysmsg = (
        "너는 한국 개인투자자를 위한 '관심 섹터 속보' 편집자다. 아래 후보 헤드라인을 냉정하게 판정하라.\n"
        "[분류]\n"
        "- 호재: 섹터/대표기업 주가를 크게 움직일 '엄청난' 긍정 사건(조 단위·사상 최대 수주/실적, 획기적 상용화·양산, 대규모 투자·M&A, 정부 대형 정책).\n"
        "- 악재: 구조적·대형 부정 충격(수출규제/제재, 생산중단, 대규모 리콜/소송, 파산, 공급망 붕괴, 대형 사고).\n"
        "- 경쟁위협: 미국·한국 외 경쟁국(특히 중국)이 우리 섹터·종목을 위협하는 기술 약진(성능 능가, 가성비 압도, 세계 최초).\n"
        "- 무시: 일반 시황·소폭 등락, 평범한 신제품/전망/목표주가/루머, 그리고 이미 며칠 지난 사건의 후속·회고·정리·전망.\n"
        "[신선도] 오늘 새로 발생/발표된 사건만 인정. 제목이 과거 사건을 되짚는 후속·정리·전망이면 반드시 '무시'.\n"
        "[원칙] 확신이 없으면 '무시'. 자극적 단어만으로 통과시키지 마라. 헤드라인에 없는 사실을 지어내지 마라. reason은 20자 내 한 줄 근거."
    )
    lines = "\n".join(f"{i}. [{c.get('sector','')}] {c['title']}" for i, c in enumerate(candidates))
    payload = f"오늘(KST): {_today_kst()}\n후보 헤드라인:\n{lines}"
    props = {"i": {"type": "INTEGER"}, "verdict": {"type": "STRING"}, "reason": {"type": "STRING"}}
    return _judge_call(sysmsg, payload, props, ["i", "verdict"])


def judge_breaking(candidates: list[dict], recent_sent: list[str]) -> list[dict] | None:
    """(속보 뉴스 2차 판정) '지난 속보(회고·후속)'·'반복 속보(이미 보낸 사건)'·무가치 기사를 걸러낸다.
    candidates: [{"title":..}]. recent_sent: 최근 이미 보낸 속보 제목. 반환 [{"i":idx, "keep":bool, "reason":..}] 또는 None(폴백)."""
    if not _KEY or not candidates:
        return None
    sysmsg = (
        "너는 한국 개인투자자를 위한 '증시 속보' 편집자다. 각 후보가 '지금 보낼 새 속보'로 적합한지 판정하라.\n"
        "[탈락(keep=false)]\n"
        "- 지난 속보: 이미 며칠 지난 사건을 되짚는 정리·회고·후속·전망(오늘 발행됐어도 사건 자체가 과거면 탈락).\n"
        "- 반복 속보: 아래 '최근 이미 보낸 속보'와 사실상 같은 사건.\n"
        "- 무가치: 단순 시황·소폭 등락 등 속보 가치가 낮은 것.\n"
        "[유지(keep=true)] 오늘 새로 터진, 아직 안 보낸 중대한 증시 사건.\n"
        "확신 없으면 keep=false. reason은 짧게."
    )
    recent = "\n".join(f"- {t}" for t in recent_sent[:20]) or "(없음)"
    lines = "\n".join(f"{i}. {c['title']}" for i, c in enumerate(candidates))
    payload = f"오늘(KST): {_today_kst()}\n최근 이미 보낸 속보:\n{recent}\n\n후보 헤드라인:\n{lines}"
    props = {"i": {"type": "INTEGER"}, "keep": {"type": "BOOLEAN"}, "reason": {"type": "STRING"}}
    return _judge_call(sysmsg, payload, props, ["i", "keep"])


def _watchlist_context() -> str:
    """관심 섹터·종목 목록을 프롬프트용 텍스트로 (P1-6 사건→관심대상 매핑용)."""
    sectors = ", ".join(config.SECTORS.keys())
    tickers = ", ".join(label for label, *_ in config.TICKERS)
    return f"관심 섹터: {sectors}\n관심 종목: {tickers}"


def macro_brief(label: str, headlines: list[str]) -> str | None:
    """(매크로 속보) 연준 FOMC·CPI 등 발표 헤드라인들을 2~3문장으로 정리. 실패/키없음 시 None.
    금리 결정·정책 기조·향후 전망 위주. 헤드라인 근거만(환각 방지), 매매 판단 금지."""
    if not _KEY or not headlines:
        return None
    sysmsg = (
        f"너는 한국 개인투자자를 위한 '{label} 발표 속보' 정리가다. "
        "아래 헤드라인들만 근거로, 발표 핵심을 2~3문장의 자연스러운 서술형으로 정리하라. "
        "금리 결정·물가 수치·정책 기조·향후 전망 위주로. 헤드라인에 없는 사실·수치는 절대 지어내지 마라. "
        "주가 예측·매수/매도 판단은 금지. 근거가 빈약하면 '세부 내용 확인 필요'라고 밝혀라."
    )
    body = {
        "system_instruction": {"parts": [{"text": sysmsg}]},
        "contents": [{"parts": [{"text": "헤드라인:\n" + "\n".join(f"- {h}" for h in headlines)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {"type": "OBJECT", "properties": {"brief": {"type": "STRING"}},
                               "required": ["brief"]},
            "maxOutputTokens": 512, "temperature": 0.3,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    for model in [config.SUMMARY_MODEL, *config.SUMMARY_FALLBACK_MODELS]:
        try:
            r = requests.post(_URL.format(model=model), params={"key": _KEY}, json=body, timeout=20)
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            brief = json.loads("".join(p.get("text", "") for p in parts)).get("brief", "").strip()
            if brief and not any(p in brief for p in _ADVICE_PATTERNS):
                return brief
        except Exception:
            continue
    return None


def earnings_brief(name: str, headlines: list[str]) -> str | None:
    """(실적 속보) 빅테크·관심종목의 분기 실적 발표 헤드라인들을 2~3문장으로 정리. 실패/키없음 시 None.
    매출·이익·전년/전분기 대비·시장 예상 대비(서프라이즈/쇼크)·가이던스 위주. 헤드라인 근거만(환각 방지)."""
    if not _KEY or not headlines:
        return None
    sysmsg = (
        f"너는 한국 개인투자자를 위한 '{name} 분기 실적 발표 속보' 정리가다. "
        "아래 헤드라인들만 근거로, 실적 발표의 핵심을 2~3문장의 자연스러운 서술형으로 정리하라. "
        "매출·영업이익/순이익(또는 EPS)·전년/전분기 대비 증감·시장 예상 대비(서프라이즈/쇼크)·가이던스(다음 분기 전망) 위주로. "
        "헤드라인에 없는 사실·수치는 절대 지어내지 마라. 주가 예측·매수/매도 판단은 금지. "
        "근거가 빈약하면 '세부 수치 확인 필요'라고 밝혀라."
    )
    body = {
        "system_instruction": {"parts": [{"text": sysmsg}]},
        "contents": [{"parts": [{"text": "헤드라인:\n" + "\n".join(f"- {h}" for h in headlines)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {"type": "OBJECT", "properties": {"brief": {"type": "STRING"}},
                               "required": ["brief"]},
            "maxOutputTokens": 512, "temperature": 0.3,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    for model in [config.SUMMARY_MODEL, *config.SUMMARY_FALLBACK_MODELS]:
        try:
            r = requests.post(_URL.format(model=model), params={"key": _KEY}, json=body, timeout=20)
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            brief = json.loads("".join(p.get("text", "") for p in parts)).get("brief", "").strip()
            if brief and not any(p in brief for p in _ADVICE_PATTERNS):
                return brief
        except Exception:
            continue
    return None


def market_context(indices, fear_greed, market_flow, quotes, region=None) -> str:
    """(한 줄 총평용) 오늘의 시장 데이터를 프롬프트 텍스트로 — 지수·공포탐욕·국내수급·급변 종목.
    region이 주어지면 그 시장 지수만 넘긴다(나스닥 브리핑인데 코스피 얘기를 하지 않도록).
    선물(야간)은 밤사이 방향성 맥락이라 항상 포함."""
    parts = []
    if indices:
        want = {"국내": ("코스피", "코스닥"), "해외": ("나스닥", "S&P500", "다우")}.get(region)
        picked = [i for i in indices
                  if (want is None or i["name"] in want or "선물" in i["name"] or i["name"] == "VIX")]
        parts.append("지수: " + ", ".join(f"{i['name']} {i['chg']:+.1f}%" for i in picked[:8]))
    if fear_greed and fear_greed.get("score") is not None:
        parts.append(f"공포탐욕지수: {fear_greed['score']:.0f}/100 ({fear_greed.get('rating','')})")
    if market_flow:
        for name, d in market_flow.items():
            def eok(v):
                return "—" if v is None else (f"{v/10000:+.1f}조" if abs(v) >= 10000 else f"{v:+,}억")
            parts.append(f"{name} 수급: 개인 {eok(d['personal'])} / 외국인 {eok(d['foreign'])}"
                         f" / 기관 {eok(d['institution'])}")
    if quotes:
        movers = sorted(quotes.items(), key=lambda kv: -abs(kv[1].get("chg", 0)))[:5]
        movers = [f"{k} {v['chg']:+.1f}%" for k, v in movers if abs(v.get("chg", 0)) >= 3]
        if movers:
            parts.append("급변 관심종목: " + ", ".join(movers))
    return "오늘의 시장 데이터:\n" + "\n".join(f"- {p}" for p in parts) if parts else ""


# 브리핑 종류별 논조 — 마켓 뷰는 '전망', 마켓 클로징은 '분석'
_KIND_TONE = {
    "view": (
        "\n\n[브리핑 종류: 마켓 뷰 — 개장 전]\n"
        "verdict와 key_points는 **전망·관전 포인트** 중심으로 쓴다. "
        "밤사이 해외 지수·야간선물 흐름, 수급, 예정 이벤트를 근거로 "
        "'오늘 장을 어떻게 볼 것인가 / 무엇이 변수인가'를 짚어라. "
        "verdict는 '~로 출발할 가능성', '~가 관건인 장' 같은 전망형 한 문장. "
        "단, 지수·주가의 구체적 수치 예측과 매매 판단은 절대 금지 — 관전 포인트로만."
    ),
    "closing": (
        "\n\n[브리핑 종류: 마켓 클로징 — 마감 후]\n"
        "verdict와 key_points는 **원인 분석** 중심으로 쓴다. "
        "지수 등락·투자자별 수급·급등락 종목을 근거로 "
        "'오늘 시장이 왜 이렇게 움직였나 / 그 의미는 무엇인가'를 짚어라. "
        "verdict는 '~때문에 ~한 장세였다' 같은 분석형 한 문장. 매매 판단은 금지."
    ),
}


def summarize(headlines: dict, market_ctx: str = "", kind: str = "") -> dict | None:
    """{'국내':[제목...], '해외':[제목...]} → 요약 dict(한 줄 총평 verdict 포함) 또는 None.
    market_ctx(시장 데이터 텍스트)를 주면 verdict가 실제 수치에 근거해 생성된다."""
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
              + (f"\n\n{market_ctx}" if market_ctx else "")
              + "\n\n" + _watchlist_context()
              + _KIND_TONE.get(kind, ""))   # 마켓 뷰=전망 / 마켓 클로징=분석

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

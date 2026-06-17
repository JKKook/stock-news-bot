"""영어 기사 제목·발췌문을 한국어로 번역 (무료 구글 번역, API 키 불필요).

번역 실패 시 원문(영어)을 그대로 두어 봇이 멈추지 않도록 한다.
"""

from deep_translator import GoogleTranslator

_translator = GoogleTranslator(source="auto", target="ko")


def _safe(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return text
    try:
        return _translator.translate(text) or text
    except Exception:
        return text  # 실패하면 원문 유지


def translate_items(items: list[dict]) -> None:
    """lang=='en' 인 기사들의 제목·발췌문을 한국어로 바꿔 끼운다(제자리 수정)."""
    for it in items:
        if it.get("lang") != "en":
            continue
        it["title"] = _safe(it["title"])
        if it.get("excerpt"):
            it["excerpt"] = _safe(it["excerpt"])


def translate_text(text: str) -> str:
    """단일 문자열(예: Yahoo 헤드라인) 번역."""
    return _safe(text)

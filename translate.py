"""영어 기사 제목·발췌문을 한국어로 번역 (DeepL, 무료 키 사용).

DEEPL_API_KEY 환경변수가 없거나 번역 실패 시 원문(영어)을 그대로 둬서
봇이 멈추지 않도록 한다.
"""

import os
import deepl

_KEY = os.environ.get("DEEPL_API_KEY")
# 무료 키('...:fx')는 자동으로 무료 엔드포인트로 연결됨
_translator = deepl.Translator(_KEY) if _KEY else None


def _translate_batch(texts: list[str]) -> list[str]:
    if not _translator or not texts:
        return texts
    try:
        results = _translator.translate_text(texts, target_lang="KO")
        return [r.text for r in results]
    except Exception as e:
        print(f"⚠️  DeepL 번역 실패({e}) — 원문 유지")
        return texts


def translate_items(items: list[dict]) -> None:
    """lang=='en' 기사의 제목·발췌문을 한국어로 일괄 번역(제자리 수정)."""
    jobs, texts = [], []
    for it in items:
        if it.get("lang") != "en":
            continue
        if it.get("title"):
            jobs.append((it, "title"))
            texts.append(it["title"])
        if it.get("excerpt"):
            jobs.append((it, "excerpt"))
            texts.append(it["excerpt"])

    if not texts:
        return
    for (it, field), translated in zip(jobs, _translate_batch(texts)):
        it[field] = translated


def translate_text(text: str) -> str:
    """단일 문자열(예: Yahoo 헤드라인) 번역."""
    if not text:
        return text
    return _translate_batch([text])[0]

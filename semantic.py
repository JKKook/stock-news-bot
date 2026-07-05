"""(P4-2) 의미 기반 근접 중복 제거 — 토큰 겹침(collect._is_same_event)이 못 잡는
'다른 표현, 같은 사건'을 Gemini 임베딩 코사인 유사도로 병합한다.

· 무료: gemini-embedding-001 (요약용 생성 모델과 별도 쿼터라 요약 429에 영향 없음).
· 한 번의 batchEmbedContents로 전체 제목을 임베딩(호출 1회) → 비용·지연 최소.
· GEMINI_API_KEY 없거나 실패 시 입력을 그대로 통과(그레이스풀 — 기존 토큰 dedup은 유지).
"""

import os
import math

import requests

import config

_KEY = os.environ.get("GEMINI_API_KEY")
_URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:batchEmbedContents"


def _embed(texts: list[str]) -> list[list[float]]:
    m = config.EMBED_MODEL
    body = {"requests": [{"model": f"models/{m}", "content": {"parts": [{"text": t}]}}
                         for t in texts]}
    r = requests.post(_URL.format(m=m), params={"key": _KEY}, json=body, timeout=20)
    r.raise_for_status()
    return [e["values"] for e in r.json()["embeddings"]]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def keep_indices(titles: list[str], threshold: float | None = None) -> set:
    """의미 유사도가 threshold↑ 인 '뒤' 항목을 중복으로 보고, 남길 인덱스 집합을 반환.
    앞선(먼저 채택된) 항목을 대표로 남긴다. 키 없음·2개 미만·실패 시 전체 keep(무효과)."""
    if threshold is None:
        threshold = config.SEMANTIC_DUP_THRESHOLD
    if not _KEY or len(titles) < 2:
        return set(range(len(titles)))
    try:
        embs = _embed(titles)
    except Exception as e:
        print(f"⚠️  의미 dedup 임베딩 실패({str(e)[:70]}) — 토큰 dedup만 사용")
        return set(range(len(titles)))
    keep, kept = [], []
    for i, emb in enumerate(embs):
        if any(_cos(emb, k) >= threshold for k in kept):
            continue
        keep.append(i)
        kept.append(emb)
    return set(keep)

"""속보 기사에서 '사건 지문'(대상 + 방향)을 추출한다 (Layer 4).

지문 예: "코스피:DOWN", "나스닥:UP", "증시:HALT", "지정학:SHOCK"
같은 지문이 최근 ALERT_EVENT_WINDOW_HOURS 안에 이미 알림됐으면
alerts.py 가 재알림을 억제한다 — 같은 사건을 여러 기사가 시차로 전해도 1회만.

문자열(제목) 단위 dedup 은 언론사마다 제목이 달라 같은 사건을 못 묶지만,
지문은 '대상+방향'이 같으면 제목이 달라도 하나로 본다.
"""

from config import ALERT_EVENT_ENTITIES, ALERT_EVENT_DIRECTIONS


def _match(low: str, vocab) -> str | None:
    """소문자화된 제목에서 vocab(위에서부터 우선)의 첫 매칭 카테고리를 반환."""
    for canonical, aliases in vocab:
        if any(a in low for a in aliases):
            return canonical
    return None


def fingerprint(title: str) -> str | None:
    """제목 → '대상:방향' 지문. 방향을 못 찾으면 None(=사건 특정 불가 → Layer4 건너뜀)."""
    low = title.lower()
    direction = _match(low, ALERT_EVENT_DIRECTIONS)
    if direction is None:
        return None
    entity = _match(low, ALERT_EVENT_ENTITIES) or "증시"  # 대상 불명확 시 일반 증시로
    return f"{entity}:{direction}"


# 방향 → 속보 타이틀 (이모지, 라벨). 기사 내용에 맞는 제목을 붙이기 위함.
_DIR_TITLE = {
    "DOWN":  ("🚨", "급락"),
    "UP":    ("📈", "급등"),
    "SHOCK": ("🌍", "리스크"),
}


def headline(title: str) -> str:
    """기사 제목 → 내용에 맞는 속보 타이틀.
    예: '🚨 코스피 급락', '📈 나스닥 급등', '🌍 지정학 리스크'.
    방향을 못 찾으면(급락·급등·지정학 어디에도 안 걸림) 일반 '🚨 속보'."""
    low = title.lower()
    direction = _match(low, ALERT_EVENT_DIRECTIONS)
    if direction is None:
        return "🚨 속보"
    emoji, label = _DIR_TITLE[direction]
    entity = _match(low, ALERT_EVENT_ENTITIES) or "증시"
    return f"{emoji} {entity} {label}"

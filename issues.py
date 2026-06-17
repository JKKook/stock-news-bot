"""관심 종목 기사 중 '특정 이슈'만 골라내는 선별기 (단순 주가변동·과거 시황 제외)."""

from config import ISSUE_INCLUDE, ISSUE_EXCLUDE

_INCLUDE = [k.lower() for k in ISSUE_INCLUDE]


def is_issue(item: dict) -> bool:
    """제목에 이슈 키워드가 있고, 단순 시황/마감 기사가 아니면 True.
    (정확도를 위해 본문이 아닌 '제목' 기준으로 판단)
    """
    title = item.get("title", "")
    low = title.lower()

    if not any(k in low for k in _INCLUDE):
        return False                      # 제목에 구체적 사건 키워드 없음 → 제외
    if any(k in title for k in ISSUE_EXCLUDE):
        return False                      # 단순 주가/마감 시황 → 제외
    return True


def filter_issues(groups: list[dict], keep: int) -> list[dict]:
    """종목 그룹별로 이슈 기사만 남기고 최대 keep개로 자른다.
    (이슈가 없는 종목은 items가 비어 표시되지 않음)
    """
    for g in groups:
        g["items"] = [it for it in g["items"] if is_issue(it)][:keep]
    return groups

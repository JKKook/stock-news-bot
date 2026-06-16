"""국내·해외 주식 이슈를 모아 디스코드로 보내는 메인 스크립트.

12시간마다 GitHub Actions가 이 파일을 실행합니다.
로컬에서 직접 테스트하려면:  python main.py
"""

from datetime import datetime, timezone, timedelta

from config import MARKET_QUERIES, TICKERS
from collect import collect
from notify import build_messages, send


def main() -> None:
    # 한국 시간(KST = UTC+9) 기준 헤더
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    header = f"📰 **주식 이슈 브리핑** — {kst:%Y-%m-%d %H:%M} (KST)"

    print("뉴스 수집 중...")
    market = collect(MARKET_QUERIES)
    tickers = collect(TICKERS)

    if not market and not tickers:
        print("최근 새 뉴스가 없어 발송을 건너뜁니다.")
        return

    messages = build_messages(market, tickers, header)
    send(messages)


if __name__ == "__main__":
    main()

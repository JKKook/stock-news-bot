# ════════════════════════════════════════════════════════════════
#  설정 파일 — 여기만 고치면 됩니다
#  공통 형식:  ("보여줄 이름", "구글뉴스 검색어", "언어", "지역")
#    · 언어 → 한국 뉴스 "ko" / 미국 뉴스 "en"
#    · 지역 → 헤드라인 앞에 붙는 표기  "국내" / "해외"
#  ※ 관심종목은 watchlist.json 에서 편집(코드 수정 불필요). 아래 정의는 폴백 기본값.
# ════════════════════════════════════════════════════════════════

import os
import json

# 최근 몇 시간 이내 뉴스만 표시할지 (이보다 오래된 기사는 제외)
# 구글 뉴스는 신선해서 12시간이면 직전 발송 이후 새 소식 위주로 보임
LOOKBACK_HOURS = 12

# 경제·주식과 무관한 기사 제거용 노이즈 키워드(제목에 있으면 제외).
#   과필터 방지 위해 '명백한 비시장 주제'만. 애매어(경기·홍수 단독 등)는 제외.
#   한국어/영어 원제목 기준(번역 전 필터). 영어는 소문자로 비교.
NEWS_NOISE_EXCLUDE = [
    # 날씨·재해
    "폭염", "뇌우", "한파", "폭설", "장마", "태풍", "폭우", "미세먼지", "황사", "지진해일",
    "heatwave", "thunderstorm", "wildfire", "hurricane", "snowfall", "flood risk",
    # 스포츠
    "월드컵", "올림픽", "프로야구", "프로축구", "k리그", "메이저리그", "챔피언스리그",
    "world cup", "olympic", "world series", "super bowl", "playoff",
    # 연예·문화
    "아이돌", "케이팝", "박스오피스", "개봉", "드라마", "예능", "콘서트", "셀럽", "연예인",
    "box office", "celebrity", "horoscope",
    # 라이프스타일
    "레시피", "맛집", "여행지", "다이어트", "반려동물", "육아", "운세", "recipe",
]

# (P0-3) 행동재무 가드레일 — 모든 발송 하단에 상시 표기하는 면책 문구.
#   뉴스만 보고 산 매수는 초과수익이 없고(연구: Barber & Odean), 앱식 알림은
#   군집행동을 부추긴다 → '정보 제공 / 신호 아님'을 1급 요소로 명시.
DISCLAIMER = "ⓘ 정보 제공용 · 매수/매도 신호 아님. 투자 판단과 책임은 본인에게 있습니다."

# 정규 브리핑을 생략할 한국 공휴일(KST) — 토/일은 코드에서 자동 처리, 여기는 공휴일만.
#   고정 양력 공휴일은 "MM-DD"(매년 동일). 음력(설·추석·부처님오신날)·대체공휴일은 해마다
#   달라 직접 "MM-DD"로 추가/갱신. 주말·공휴일엔 정규 브리핑 대신 속보(alerts.py)만 발송.
KR_HOLIDAYS = [
    "01-01",  # 신정
    "03-01",  # 삼일절
    "05-05",  # 어린이날
    "06-06",  # 현충일
    "08-15",  # 광복절
    "10-03",  # 개천절
    "10-09",  # 한글날
    "12-25",  # 성탄절
]

# (R9) 음력 공휴일(설날·추석 연휴 + 부처님오신날)은 해마다 양력 날짜가 달라 자동 계산한다.
#   korean_lunar_calendar 로 해당 연도의 양력일을 구함(없으면 조용히 생략 — 고정 공휴일만 적용).
_lunar_cache = {}


def lunar_holidays(year: int) -> set:
    """해당 연도 음력 공휴일의 양력 'MM-DD' 집합 — 설날·추석(각 전후 연휴 포함)·부처님오신날."""
    if year in _lunar_cache:
        return _lunar_cache[year]
    out = set()
    try:
        from datetime import date, timedelta
        from korean_lunar_calendar import KoreanLunarCalendar
        c = KoreanLunarCalendar()

        def solar(lm, ld):
            c.setLunarDate(year, lm, ld, False)
            return date.fromisoformat(c.SolarIsoFormat())

        for base in (solar(1, 1), solar(8, 15)):     # 설날·추석: 전날~다음날 3일 연휴
            for delta in (-1, 0, 1):
                out.add(f"{base + timedelta(days=delta):%m-%d}")
        out.add(f"{solar(4, 8):%m-%d}")              # 부처님오신날
    except Exception:
        out = set()
    _lunar_cache[year] = out
    return out


def is_kr_holiday(d) -> bool:
    """d(date)가 한국 공휴일인지 — 고정 양력(KR_HOLIDAYS) + 음력 공휴일(해당 연도 계산)."""
    mmdd = f"{d:%m-%d}"
    return mmdd in KR_HOLIDAYS or mmdd in lunar_holidays(d.year)

# (P1) AI 'so-what' 요약에 쓸 Gemini 모델 (Google AI Studio 무료 티어).
#   gemini-2.0-flash 는 무료 쿼터 0(429)이라 2.5-flash 사용(검증됨). 대안: "gemini-flash-latest".
SUMMARY_MODEL = "gemini-2.5-flash"
# (P4-1) 폴백 체인 — 1차 모델이 429(쿼터)·실패면 순차로 다른 모델 시도(요약이 조용히 사라지지 않게).
SUMMARY_FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.5-flash-lite"]
SUMMARY_CACHE_TTL_H = 6   # 동일 헤드라인 재요약 방지(테스트·중복실행 보호). 0이면 캐시 끔.

# (P4-2) 의미 기반 근접 중복 제거 — 토큰 겹침이 못 잡는 '다른 표현·같은 사건'을 임베딩 유사도로.
#   요약과 별도 모델/쿼터. 한국어 뉴스는 무관해도 baseline 유사도(~0.6)가 높아 임계값을 높게 둔다.
EMBED_MODEL = "gemini-embedding-001"
SEMANTIC_DUP_THRESHOLD = 0.80

# (P0-2) 관심종목 이례성 강조 — 이 %이상 변동한 종목은 🔥로 표시하고 위로 정렬.
#   최신순이 아니라 '움직임이 큰(=이례적) 것 먼저' 보여줘 판단 우선순위를 준다.
TICKER_MOVE_FLAG = 5.0

# 볼린저 밴드 — SMA(BB_PERIOD) ± BB_K×표준편차. %B(밴드 내 위치)로 관심종목에 표시.
#   현재가가 자기 15일 변동성 대비 어디쯤인지(하단0·중심50·상단100)를 맥락으로 제공(조언 아님).
BB_PERIOD = 15   # 이동평균·표준편차 기간(일)
BB_K = 2.0       # 밴드 폭(표준편차 배수)

# (A) 미국 공매도 수급 — 공매도 비율이 이 %이상일 때만 판단 줄에 표시(노이즈 방지).
SHORT_INTEREST_FLAG = 10.0

# (P3-2) 되돌림·선반영 경고 — 당일 |등락|이 이 %↑ 이면서 볼린저 밴드 상단권(≥80)/하단권(≤20)까지
#   같은 방향으로 겹칠 때만 '이미 큰 폭 반영·추격 주의' 라벨을 붙인다(급변+통계적 과확장 동시).
#   근거: 극단 헤드라인은 이미 과반영→되돌림(Kwon&Tang), 보유종목 과잉반응(QJE). 매매신호 아님.
REVERSAL_MOVE_FLAG = 7.0

# (P3-1) 거래량 게이팅 — 당일 거래량이 직전 평균의 이 배수↑ 이면 '거래량 동반'으로 표시.
#   가격 급변이 거래량을 동반하면 실제 참여 폭발(=이례성/선반영 강도)로 본다.
VOLUME_FLAG = 1.5

# (R1) 정확도 측정 루프 — 되돌림 경고를 기록하고 이 일수 뒤 실제 가격과 대조해 적중률 측정.
#   정확도를 '주장'이 아니라 '측정'으로. prediction_log.json(Actions cache로 유지).
MEASURE_HORIZON_DAYS = 7    # 신호 기록 후 며칠 뒤 검증(달력일 ≈ 5 거래일)
MEASURE_LOG_MAX = 500       # prediction_log.json 최대 보관 신호 수(비대화 방지)

# ── (P0-4) 촉매 캘린더 ──────────────────────────────────────────
#   투자 판단의 앵커인 '예정 이벤트'(경제지표 + 관심종목 실적)를 미리 보여준다.
#   실적: yfinance(전 종목·무료) / 경제지표: FRED 공식 릴리스(FRED_API_KEY 필요)
CATALYST_DAYS_AHEAD = 14    # 경제지표: 앞으로 며칠 이내 (월간 발표라 2주는 봐야 노출됨)
CATALYST_EARN_DAYS = 21    # 실적: 앞으로 며칠 이내 (분기 발표라 더 길게)
CATALYST_MAX_ECON = 8      # 경제지표 최대 표시 건수
CATALYST_MAX_EARN = 10     # 실적 최대 표시 건수

# FRED 주요 경제지표 릴리스 (release_id, 표시 라벨, 임팩트)
#   release_id는 FRED 릴리스 고유번호 — 키로 실측 검증 후 확정.
FRED_RELEASES = [
    (10, "미 소비자물가 CPI",        "High"),
    (50, "미 고용상황(고용·실업률)",  "High"),
    (53, "미 GDP",                  "High"),
    (54, "미 개인소비지출 PCE",      "Medium"),
    (46, "미 생산자물가 PPI",        "Medium"),
    (9,  "미 소매판매",             "Medium"),
]

# (R3) 한국 경제지표 캘린더.
#   한국은 미래 발표일 무료 API가 없어 직접 등록(금통위 등 비정기) + CPI는 catalysts가 알고리즘 계산.
#   dict 형식: {"date","name","impact","confirmed"}. confirmed=False면 렌더에 '(잠정)' 표기.
#   공식 발표일 확정 시 kr_calendar.json 에서 confirmed=true 로 바꾸면 '(잠정)'이 사라진다.
#   출처(연 1회 갱신): 한국은행 금통위 일정 www.bok.or.kr, 통계청 공표일정 kostat.go.kr
KR_ECONOMIC_EVENTS = [   # 아래는 폴백 기본값 — 실제 편집은 kr_calendar.json 에서.
    {"date": "2026-07-16", "name": "한국 금통위 기준금리", "impact": "High", "confirmed": False},
    {"date": "2026-08-27", "name": "한국 금통위 기준금리", "impact": "High", "confirmed": False},
]


def _apply_kr_calendar_json():
    """kr_calendar.json 이 있으면 한국 경제 캘린더를 그걸로 대체(공식 발표일 유지관리용)."""
    global KR_ECONOMIC_EVENTS
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kr_calendar.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        events = [e for e in data if e.get("date") and e.get("name")]
        if events:
            KR_ECONOMIC_EVENTS = events
    except Exception as e:
        print(f"⚠️  kr_calendar.json 파싱 실패({e}) — 내장 기본 캘린더 사용")


_apply_kr_calendar_json()

# ── 블록별 기사 개수 ────────────────────────────────────────────
HEADLINE_PER_REGION = 5  # 1) 헤드라인 — 국내/해외 각각 몇 개씩
HEADLINE_MAX_LEN = 100   # 1) 헤드라인 한 줄 최대 글자 수 (100자 미만)
MAX_MARKET = 2           # 2) 시장 항목당 기사 수
MAX_SECTOR = 2           # 3) 섹터·지역당 기사 수
MAX_TICKER = 2           # 4) 종목당 (이슈) 기사 수
TICKER_CANDIDATES = 12   # 종목당 이슈 선별 전 후보 기사 수
EXCERPT_MAX_LEN = 300    # 기사 발췌문 최대 글자 수 (300자 미만)
BLOOMBERG_EXCERPT_LEN = 120  # 블룸버그 본문은 1~2줄만(링크는 하단 Source에 있음)
MAX_BLOOMBERG = 5        # 블룸버그 공식 RSS 표시 건수(해외 섹션)
SOURCE_PER_REGION = 6    # 맨 끝 Source 링크 모음 — 한국/미국 각각 건수
SOURCE_BLOOMBERG = 3     # Source에 하이퍼링크로 넣을 블룸버그 주요 기사 수

# 블룸버그 공식 RSS (퍼블리셔 제공 요약 포함 — 합법적 신디케이션 피드)
BLOOMBERG_FEEDS = [
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://feeds.bloomberg.com/technology/news.rss",
]


# ════════════════════════════════════════════════════════════════
#  0) 주요 지수 시세 — (이름, 야후 심볼, 국기/아이콘)
#     맨 위 대시보드에 실시간 지수 + 공포탐욕지수로 표시
# ════════════════════════════════════════════════════════════════
INDICES = [
    ("코스피",   "^KS11",   "🇰🇷"),
    ("코스닥",   "^KQ11",   "🇰🇷"),
    ("나스닥",   "^IXIC",   "🇺🇸"),
    ("나스닥선물", "NQ=F",   "🇺🇸"),   # 야간선물 — 한국 투자자가 밤새 보는 선행지표
    ("S&P500",  "^GSPC",   "🇺🇸"),
    ("다우",     "^DJI",    "🇺🇸"),
    ("VIX",     "^VIX",    "🌐"),
    ("원/달러",  "KRW=X",   "💵"),
    ("금",       "GC=F",    "🥇"),
    ("은",       "SI=F",    "🥈"),
    ("비트코인", "BTC-USD", "₿"),
]


# ════════════════════════════════════════════════════════════════
#  2) 시장 전체 주요 이슈
# ════════════════════════════════════════════════════════════════
MARKET_QUERIES = [
    ("코스피",        "코스피 지수",          "ko", "국내"),
    ("코스닥",        "코스닥 지수",          "ko", "국내"),
    ("나스닥",        "나스닥 지수",          "ko", "해외"),
    ("S&P500·다우",   "미국 증시 다우 S&P500", "ko", "해외"),
    ("환율·금리",     "원달러 환율 미국 금리",  "ko", "해외"),
]


# ════════════════════════════════════════════════════════════════
#  3) 섹터별 주요 소식 (국내 + 해외 둘 다)
#     섹터명: [(검색어, 언어, 지역), ...]
# ════════════════════════════════════════════════════════════════
SECTORS = {
    "AI 인프라": [
        ("AI 인프라 투자",       "ko", "국내"),
        ("AI infrastructure",   "en", "해외"),
    ],
    "피지컬AI(로보틱스)": [
        ("피지컬 AI 로봇",          "ko", "국내"),
        ("physical AI humanoid robot", "en", "해외"),
    ],
    "데이터센터": [
        ("데이터센터",     "ko", "국내"),
        ("data center",   "en", "해외"),
    ],
    "전력기기": [
        ("전력기기 변압기",                "ko", "국내"),
        ("electrical equipment transformer", "en", "해외"),
    ],
    "전력인프라": [
        ("전력망 전력 인프라",          "ko", "국내"),
        ("power grid infrastructure",  "en", "해외"),
    ],
    "반도체": [
        ("반도체",                "ko", "국내"),
        ("semiconductor chips",  "en", "해외"),
    ],
    "양자컴퓨팅": [
        ("양자컴퓨터",        "ko", "국내"),
        ("quantum computing", "en", "해외"),
    ],
}


# ════════════════════════════════════════════════════════════════
#  4) 관심 종목
# ════════════════════════════════════════════════════════════════
TICKERS = [
    # ── 해외 ──────────────────────────────────────────────
    ("스페이스X",     "SpaceX",                  "en", "해외"),
    ("테슬라",        "Tesla TSLA stock",        "en", "해외"),
    ("테라다인",      "Teradyne TER stock",      "en", "해외"),
    ("리게티컴퓨팅",  "Rigetti Computing RGTI",  "en", "해외"),
    ("알파벳A",       "Alphabet GOOGL stock",    "en", "해외"),
    ("플래닛랩스",    "Planet Labs PL stock",    "en", "해외"),
    ("코어위브",      "CoreWeave CRWV stock",    "en", "해외"),
    ("램리서치",      "Lam Research LRCX stock", "en", "해외"),
    ("ASML홀딩",      "ASML stock",              "en", "해외"),
    ("엔비디아",      "Nvidia NVDA stock",       "en", "해외"),
    ("마이크로소프트","Microsoft MSFT stock",    "en", "해외"),
    ("비스트라에너지","Vistra VST stock",        "en", "해외"),
    ("오클로",        "Oklo OKLO stock",         "en", "해외"),
    ("로켓랩",        "Rocket Lab RKLB stock",   "en", "해외"),
    ("SOXL",          "SOXL semiconductor ETF",  "en", "해외"),
    ("SPY",           "SPY S&P 500 ETF",         "en", "해외"),
    ("SCHD",          "SCHD dividend ETF",       "en", "해외"),
    ("QLD",           "QLD ProShares QQQ ETF",   "en", "해외"),
    # ── 국내 ──────────────────────────────────────────────
    ("HL만도",        "HL만도 주가",       "ko", "국내"),
    ("한미반도체",    "한미반도체 주가",   "ko", "국내"),
    ("일진전기",      "일진전기 주가",     "ko", "국내"),
    ("현대차",        "현대차 주가",       "ko", "국내"),
    ("현대오토에버",  "현대오토에버 주가", "ko", "국내"),
    ("LS ELECTRIC",   "LS일렉트릭 주가",   "ko", "국내"),
    ("SK하이닉스",    "SK하이닉스 주가",   "ko", "국내"),
    ("LG CNS",        "LG CNS 주가",       "ko", "국내"),
    ("삼성전자",      "삼성전자 주가",     "ko", "국내"),
    ("코리아써키트",  "코리아써키트 주가", "ko", "국내"),
    ("HD현대일렉트릭","HD현대일렉트릭 주가", "ko", "국내"),
    ("대한광통신",    "대한광통신 주가",   "ko", "국내"),
]

# ── 관심종목 → 야후 심볼 (P0-1: 뉴스에 price action 첨부) ─────────
#   · 뉴스 제목 옆에 '현재가·등락%·52주 위치'를 붙여 판단 맥락을 제공.
#   · 비상장(예: 스페이스X)은 심볼이 없어 생략(가격 미표기).
#   · 심볼은 실제 야후에서 데이터가 나오는지 검증 후에만 등록(정확도 우선).
#   · KRX: 코스피 .KS / 코스닥 .KQ
TICKER_SYMBOLS = {
    "스페이스X":     None,          # 비상장
    "테슬라":        "TSLA",
    "테라다인":      "TER",
    "리게티컴퓨팅":  "RGTI",
    "알파벳A":       "GOOGL",
    "플래닛랩스":    "PL",
    "코어위브":      "CRWV",
    "램리서치":      "LRCX",
    "ASML홀딩":      "ASML",
    "엔비디아":      "NVDA",
    "마이크로소프트": "MSFT",
    "비스트라에너지": "VST",
    "오클로":        "OKLO",
    "로켓랩":        "RKLB",
    "SOXL":          "SOXL",
    "SPY":           "SPY",
    "SCHD":          "SCHD",
    "QLD":           "QLD",
    "HL만도":        "204320.KS",
    "한미반도체":    "042700.KS",
    "일진전기":      "103590.KS",
    "현대차":        "005380.KS",
    "현대오토에버":  "307950.KS",
    "LS ELECTRIC":   "010120.KS",
    "SK하이닉스":    "000660.KS",
    "LG CNS":        "064400.KS",
    "삼성전자":      "005930.KS",
    "코리아써키트":  "007810.KS",
    "HD현대일렉트릭": "267260.KS",
    "대한광통신":    "010170.KS",
}


# (개인화) watchlist.json 이 있으면 관심종목을 그걸로 대체 — 코드 수정 없이 편집.
#   위 TICKER_SYMBOLS/TICKERS 는 파일이 없거나 파싱 실패 시 쓰이는 폴백 기본값.
# (R7) 종목별 속보 알림 on/off — watchlist.json 각 종목의 "alert":false 면 속보 확증 대상에서 제외.
#   기본 true. 비어 있으면(.get 기본값) 모든 종목 알림 대상.
TICKER_ALERT = {}


def _apply_watchlist_json():
    global TICKER_SYMBOLS, TICKERS, TICKER_ALERT
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            wl = json.load(f)
        ts = {w["label"]: w.get("symbol") for w in wl}
        tk = [(w["label"], w.get("query") or w["label"],
               w.get("lang", "ko"), w.get("region", "국내")) for w in wl]
        if ts and tk:                    # 비어 있으면 기본값 유지(안전)
            TICKER_SYMBOLS, TICKERS = ts, tk
            TICKER_ALERT = {w["label"]: w.get("alert", True) for w in wl}   # (R7)
    except Exception as e:
        print(f"⚠️  watchlist.json 파싱 실패({e}) — 내장 기본 관심종목 사용")


_apply_watchlist_json()


# ════════════════════════════════════════════════════════════════
#  5) 관심 종목 '이슈' 선별 키워드
#     · INCLUDE 단어가 하나라도 있으면 '특정 이슈'로 보고 채택
#     · EXCLUDE 단어가 제목에 있으면 '단순 주가/과거 시황'으로 보고 제외
#     · 이슈가 하나도 없는 종목은 아예 표시하지 않음
# ════════════════════════════════════════════════════════════════
ISSUE_INCLUDE = [
    # 한국어 — 구체적 사건
    "계약", "수주", "체결", "협약", "MOU", "공급", "납품", "인수", "합병",
    "증설", "신제품", "출시", "양산", "특허", "승인", "허가", "규제", "제휴",
    "상장", "리콜", "착공", "준공", "수출", "구축", "개발 성공", "공장", "유치",
    # 영어 — 구체적 사건
    "deal", "contract", "order", "partnership", "supply", "acquire",
    "acquisition", "merger", "launch", "unveil", "announce", "expansion",
    "approval", "patent", "ipo", "stake", "sign", "wins", "secures",
]
ISSUE_EXCLUDE = [
    "마감시황", "마감", "종가", "장중", "시황", "목표주가", "투자의견",
    "신고가", "급등", "급락", "강세 마감", "약세 마감",
]


# ════════════════════════════════════════════════════════════════
#  6) 속보·급변 알림 (별도 워크플로 alerts.yml — 10분 주기)
# ════════════════════════════════════════════════════════════════
# 지수 급변 알림 대상 (지수 시세 대시보드와 별개로, 주가지수만)
ALERT_INDICES = [
    ("코스피",   "^KS11", "🇰🇷"),
    ("코스닥",   "^KQ11", "🇰🇷"),
    ("나스닥",   "^IXIC", "🇺🇸"),
    ("나스닥선물", "NQ=F", "🇺🇸"),   # 야간선물 급변도 속보로(정규장/야선 세션 라벨로 구분)
    ("S&P500",  "^GSPC", "🇺🇸"),
    ("다우",     "^DJI",  "🇺🇸"),
]
# 전일 종가 대비 |변동%| 이 단계들을 넘을 때마다 단계별 1회 알림
ALERT_INDEX_BANDS = [5, 8, 15, 20]   # 5%↑ 급변 / 8·15·20%는 서킷브레이커 수준
ALERT_FNG_DELTA = 15                 # 공포탐욕지수가 직전 알림 대비 이만큼 변하면 알림
ALERT_LOOKBACK_MIN = 90              # 이 시간 이내 발행된 기사만 속보로 인정
ALERT_MAX_PER_RUN = 6                # 한 번 실행에서 보낼 최대 속보 수(과알림 방지)
# (Layer 5) 내용 유사도 중복 억제 — 출처가 달라 제목 표현이 조금 달라도 '같은 내용'이면 재발송 방지.
#   최근 발송 제목과의 문자 bigram Jaccard 유사도가 THRESHOLD 이상이면 같은 사건으로 보고 제외.
#   L4(사건 지문=대상:방향)를 보완: 지문이 못 잡는 근접 재탕(다른 언론사의 재송고·복붙 헤드라인)을 컷.
#   API 불필요(순수 문자열) — alerts 워크플로엔 임베딩 키가 없어 무의존 방식을 쓴다.
ALERT_DUP_SIM_THRESHOLD = 0.6        # 0~1. 높일수록 '거의 동일'만 컷(과억제↓), 낮출수록 공격적
ALERT_DUP_WINDOW_HOURS = 18          # 이 시간 안에 발송한 제목들과만 비교(상태 파일 비대화 방지)

# ── (섹터 속보) 관심 섹터의 '엄청난' 호재/악재만 실시간 속보로 ──────────
#   config.SECTORS 검색어로 찾은 기사에, 아래 강한 키워드가 제목에 있을 때만 인정한다.
#   일반 시황·소식(단순 급등락·평범한 계약 등)은 무시 — 구조적·대형 사건만 통과(과알림 방지).
#   국내+해외. 노이즈 억제는 기존 L1(90분)·L2(회고컷)·L5(유사도) + 섹터 지문(섹터:방향, 18h)을 재사용.
ALERT_SECTOR_ENABLE = True
ALERT_SECTOR_MAX_PER_RUN = 2         # 한 실행에서 보낼 최대 섹터 속보 수
# (2차 AI 판정) 키워드로 좁힌 후보를 Gemini가 재판정 — 정말 '엄청난'지 + 오늘 새 사건인지 / 지난·반복 속보인지.
#   후보가 있을 때만 1회 호출(대부분 실행은 후보 0 → 호출 없음). 키 없거나 실패 시 키워드 판정으로 자동 폴백.
ALERT_AI_JUDGE = True
ALERT_SECTOR_POSITIVE = [            # 🟢 엄청난 호재 → ⭐
    "사상 최대", "사상최대", "역대 최대", "역대최대", "최대 실적", "어닝 서프라이즈", "실적 서프라이즈",
    "대규모 수주", "초대형 수주", "조 단위", "초대형 계약", "대규모 투자", "대규모 증설",
    "빅딜", "대형 인수", "세계 최초", "상용화 성공", "폭등",
    "record high", "record order", "massive investment", "landmark deal",
    "blowout earnings", "breakthrough",
]
ALERT_SECTOR_NEGATIVE = [            # 🔴 엄청난 악재 → ❗
    "수출 금지", "수출금지", "수출 규제", "수출규제", "수출 통제", "수출통제", "수출 제한", "수출제한",
    "전면 금지", "전면 제재", "금수 조치", "관세 폭탄",
    "생산 중단", "가동 중단", "셧다운", "대규모 리콜", "집단소송", "반독점 제소",
    "파산", "디폴트", "공급망 붕괴", "폭발 사고", "대형 화재", "폭락", "붕괴",
    "export ban", "export curb", "export control", "sanction", "shutdown", "mass recall",
    "antitrust", "bankruptcy", "supply chain crisis", "plunge", "collapse",
]

# (경쟁위협) 미·한 외 경쟁국(특히 중국)의 '기술 약진'도 보유 종목엔 악재 → ❗ [섹터·경쟁위협]으로.
#   조건: 제목에 (경쟁국 주체 RIVAL) + (약진/추월 신호 RIVAL_TECH)가 모두 있을 때만.
#   예) "중국 딥시크, OpenAI GPT 능가·가성비 우위" / "화웨이, 반도체 세계 최초 양산".
#   섹터 투자 검색어로는 잘 안 잡혀 ALERT_RIVAL_QUERIES로 별도 검색도 한다.
ALERT_SECTOR_RIVAL = [              # 미·한 외 경쟁 주체(국가·기업·모델)
    "중국", "중국산", "중국발", "화웨이", "딥시크", "deepseek", "kimi", "문샷", "moonshot",
    "알리바바", "텐센트", "바이두", "샤오미", "비야디", "byd", "일본", "대만", "유럽",
    "china", "chinese", "huawei", "baidu", "alibaba", "tencent", "japan", "taiwan",
]
ALERT_SECTOR_RIVAL_TECH = [         # 약진·추월·가성비 등 '위협' 신호
    "능가", "추월", "앞질", "앞서", "제치", "제쳐", "압도", "우위", "최고 성능", "성능 우위",
    "가성비", "저비용", "세계 최초", "세계 최고", "돌파", "약진", "굴기", "따라잡", "맹추격",
    "outperform", "surpass", "beat", "beats", "leapfrog", "state-of-the-art", "sota",
    "cheaper", "rival", "breakthrough", "world's first", "world first",
]
ALERT_RIVAL_QUERIES = [             # (경쟁위협 전용 검색) 섹터명, 검색어, 언어, 지역
    ("AI",     "중국 AI OR 인공지능 능가 OR 오픈소스 모델",     "ko", "해외"),
    ("AI",     "China AI model OR Chinese LLM breakthrough",   "en", "해외"),
    ("반도체",  "중국 반도체 OR 중국산 칩 기술",                "ko", "해외"),
    ("반도체",  "China semiconductor breakthrough OR Chinese chip", "en", "해외"),
    ("로보틱스", "중국 휴머노이드 로봇 OR 중국 로봇",            "ko", "해외"),
]
# (P3 확장) 속보 가격·거래량 확증 — 속보가 관심종목을 지목하면 실제 |등락|이 이 %↑ 일 때
#   '📈 실제 반응 동반'(진짜 이례성)으로 확증, 미만이면 '📉 가격 반응 미미'(선반영/영향 제한 가능).
#   발송은 억제하지 않고 주석만 붙인다(뉴스가 가격을 선행할 수 있어 신속성 보존).
ALERT_CONFIRM_MOVE = 3.0

# (세션별 제외) 한 시장 정규장이 열려 있으면, 닫힌 반대편 시장의 '지수 속보'는 stale → 제외.
#   · 미 정규장(KST 밤)엔 코스피/코스닥 지수 속보 제외
#   · 코스피 정규장(KST 낮)엔 나스닥/뉴욕증시 지수 속보 제외
#   제목에 아래 시장 키워드가 '한쪽만' 있을 때만 판정 — 전쟁·지정학 등 비시장 속보는 영향 없음.
ALERT_KR_MARKET_KW = ["코스피", "코스닥", "kospi", "kosdaq"]
ALERT_US_MARKET_KW = ["나스닥", "nasdaq", "s&p", "다우", "dow", "뉴욕증시", "뉴욕 증시", "미국증시", "미국 증시"]

# (R2) 속보 뉴스 시장 반응 확증 — 제목의 시장 키워드 → 확인할 지수(들) 매핑.
#   이미 조회한 indices 를 재사용해(추가 호출 0) '제목만 요란하고 시장은 무반응'인 속보를 가려낸다.
#   후보 여러 개면 |등락| 가장 큰 것을 대표 반응으로 본다(야간엔 선물이 움직임).
ALERT_MARKET_MAP = [
    (["코스피", "kospi"],                       ["코스피"]),
    (["코스닥", "kosdaq"],                       ["코스닥"]),
    (["나스닥", "nasdaq"],                       ["나스닥선물", "나스닥"]),
    (["s&p", "s&p500", "에스앤피"],              ["S&P500"]),
    (["다우", "dow", "wall street", "월가"],     ["다우"]),
]
ALERT_MARKET_MOVE = 1.5   # 지수 |등락|이 이 %↑면 '실제 시장 반응 동반'으로 확증
# 지정학·리스크 속보 → VIX(공포지수) 급등으로 시장 반응 확증
ALERT_GEO_KEYWORDS = ["전쟁", "미사일", "공격", "침공", "테러", "긴급",
                      "war", "missile", "attack", "invasion", "strike"]
ALERT_VIX_SPIKE = 5.0     # VIX가 이 %↑ 오르면 지정학 속보의 '공포 반응' 확증

# (주말 모드) 토·일(KST)엔 국내·미국장이 모두 닫혀 지수 급등락 속보가 의미 없다 →
#   ① 지수 급변·공포탐욕 알림을 끄고 ② 뉴스 속보는 아래 키워드(전쟁·지정학 / 심각한 경제 충격)를
#   포함한 기사만 통과시킨다. '코스피 폭락' 같은 지수 기사는 주말엔 보내지 않는다.
#   (공휴일은 제외 — 한국 공휴일에도 미국장은 열릴 수 있어 지수 알림을 살려둔다.)
ALERT_WEEKEND_ONLY = [
    # 전쟁·지정학
    "전쟁", "미사일", "공격", "침공", "테러", "교전", "공습", "사상자", "계엄", "핵실험",
    "war", "missile", "attack", "invasion", "airstrike", "terror", "nuclear test",
    # 심각한 경제 충격(구조적 타격 — 단순 지수 등락 아님)
    "금융위기", "디폴트", "채무불이행", "국가부도", "파산", "뱅크런", "구제금융",
    "신용등급 강등", "긴급 금리", "무역전쟁", "관세 폭탄", "제재", "유가 폭등", "환율 위기",
    "financial crisis", "default", "bankruptcy", "bank run", "bailout", "downgrade",
    "emergency rate", "trade war", "sanction", "oil shock",
]

# 속보 검색 — (검색어, 언어, 지역)
ALERT_NEWS_QUERIES = [
    ("코스피 OR 코스닥 폭락 OR 급락 OR 사이드카 OR 서킷브레이커", "ko", "국내"),
    ("증시 폭락 OR 급등 OR 패닉 OR 쇼크",                        "ko", "국내"),
    ("stock market crash OR plunge OR trading halt OR circuit breaker", "en", "해외"),
    ("Nasdaq OR S&P 500 plunge OR tumble OR selloff",            "en", "해외"),
    ("전쟁 OR 공격 OR 미사일 OR 사상자 OR 긴급 금리",            "ko", "해외"),
    ("war OR attack OR missile strike OR emergency rate",        "en", "해외"),
]
# 제목에 아래 단어가 있어야 '속보'로 인정 (과알림 방지)
ALERT_KEYWORDS = [
    "급락", "폭락", "급등", "폭등", "사이드카", "서킷브레이커", "패닉", "쇼크",
    "사상", "비상", "전쟁", "공격", "미사일", "긴급", "속보", "붕괴", "강타",
    "crash", "plunge", "plummet", "halt", "circuit breaker", "sidecar",
    "surge", "soar", "panic", "war", "attack", "strike", "emergency",
    "breaking", "tumble", "rout", "selloff", "collapse",
]

# (D-3) 속보 심각도 티어 — 높은 순으로 먼저 발송(HIGH>MID>기타). 영어는 소문자로 비교.
ALERT_SEVERITY_HIGH = [
    "서킷브레이커", "사이드카", "폭락", "폭등", "패닉", "붕괴", "전쟁", "미사일",
    "circuit breaker", "sidecar", "crash", "plunge", "collapse", "war", "panic",
]
ALERT_SEVERITY_MID = [
    "급락", "급등", "쇼크", "강타", "공격", "긴급", "비상", "사상",
    "selloff", "plummet", "tumble", "surge", "soar", "attack", "emergency", "rout",
]

# ── (Layer 2) 속보 제외 — 회고·주간·마감 컬럼 ────────────────────
#   제목에 아래 단어가 있으면 '지난 이벤트를 다시 다루는 기사'로 보고 속보에서 제외.
#   (issues.py 의 ISSUE_EXCLUDE 와 같은 패턴 — 새로 발행돼 날짜 필터를 통과하는
#    회고/전망 컬럼이 속보로 오탐되는 것을 막는다)
ALERT_NEWS_EXCLUDE = [
    "이번주", "이번 주", "지난주", "지난 주", "금주", "주간", "주말", "주末", "주말머니",
    "마감", "시황", "총정리", "정리", "돌아보기", "되돌아", "리뷰", "회고", "브리핑", "결산",
    "week ahead", "week in review", "recap", "roundup", "wrap", "in review",
]

# ── (Layer 4) 사건 단위 중복 억제 ───────────────────────────────
# 같은 '사건 지문'(대상+방향)이 이 시간(시간 단위) 안에 이미 알림됐으면 재알림 안 함.
# 18h 선택 이유: 전일 장중(09~15시) 사건 알림 후 다음날 아침(08~09시) 재탕하는
#   회고 기사(≈17~23h 뒤)를 대체로 억제하면서, 다음날 장중에 '실제로 새로' 터지는
#   같은 방향 사건(전일 사건으로부터 대개 창 밖)은 통과시키는 균형점.
ALERT_EVENT_WINDOW_HOURS = 18

# 사건 지문 - 대상 엔티티 (위에서부터 우선 매칭, 별칭은 소문자로 비교)
ALERT_EVENT_ENTITIES = [
    ("코스피",   ["코스피", "kospi"]),
    ("코스닥",   ["코스닥", "kosdaq"]),
    ("나스닥",   ["나스닥", "nasdaq"]),
    ("S&P500",  ["s&p 500", "s&p500", "s&p", "에스앤피"]),
    ("다우",     ["다우", "dow"]),
    ("비트코인", ["비트코인", "bitcoin", "btc"]),
    ("환율",     ["원/달러", "원달러", "환율"]),
    ("금리",     ["연준", "fed", "금리", "rate"]),
    ("지정학",   ["전쟁", "미사일", "공격", "war", "missile", "attack", "strike"]),
]

# 사건 지문 - 방향 (위에서부터 우선 매칭)
#   · UP/DOWN 을 나눠 급락↔급등(반대 방향)은 다른 사건으로 본다.
#   · 서킷브레이커/사이드카는 '양방향' 발동이라 방향 지표에서 제외 — 같이 붙는
#     급락/급등이 방향을 정한다(방향어 없이 halt 만 있으면 지문 None → dedup 건너뜀=안전).
#     심각도 에스컬레이션은 check_indices 의 지수 밴드 알림이 별도로 담당.
ALERT_EVENT_DIRECTIONS = [
    ("DOWN",  ["급락", "폭락", "하락", "붕괴", "쇼크", "패닉", "crash", "plunge",
               "plummet", "tumble", "rout", "selloff", "sink", "collapse", "panic"]),
    ("UP",    ["급등", "폭등", "상승", "반등", "surge", "soar", "rally", "jump", "spike", "rebound"]),
    ("SHOCK", ["전쟁", "미사일", "공격", "긴급", "비상", "war", "missile", "attack",
               "strike", "emergency"]),
]


# ════════════════════════════════════════════════════════════════
# (R8) 임계값 외부화 — settings.json 이 있으면 아래 튜닝값을 덮어쓴다(코드 수정 없이 민감도 조정).
#   파일 맨 끝에서 실행 → 다른 모듈이 `from config import ...` 하기 전에 최종값이 반영된다.
# ════════════════════════════════════════════════════════════════
_TUNABLE = {
    "TICKER_MOVE_FLAG", "REVERSAL_MOVE_FLAG", "VOLUME_FLAG", "SHORT_INTEREST_FLAG",
    "ALERT_CONFIRM_MOVE", "ALERT_MARKET_MOVE", "ALERT_VIX_SPIKE",
    "SEMANTIC_DUP_THRESHOLD", "MEASURE_HORIZON_DAYS", "ALERT_FNG_DELTA",
    "BB_PERIOD", "BB_K",
}


def _apply_settings_json():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
        for k, v in s.items():
            if k in _TUNABLE and isinstance(v, (int, float)) and not isinstance(v, bool):
                globals()[k] = v
    except Exception as e:
        print(f"⚠️  settings.json 파싱 실패({e}) — 내장 기본 임계값 사용")


_apply_settings_json()


# ════════════════════════════════════════════════════════════════
# (마켓 뷰/클로징) Up & Down — '시장이 주목한' 급등·급락 종목
#   관심종목이 아니라 시장 전체 랭킹. 잡주·레버리지 ETN 노이즈를 걸러 의미 있는 움직임만.
# ════════════════════════════════════════════════════════════════
MOVERS_COUNT = 5              # UP / DOWN 각각 표시할 종목 수
MOVERS_KR_MIN_VALUE = 50000   # 국내(코스피) 거래대금 하한(백만원) = 500억 — 대형주 중심
MOVERS_US_MIN_MCAP = 5e9      # 해외(나스닥) 시총 하한($) = 50억달러 — 중·대형주 중심

# Up & Down 이유 뉴스 — 이 시간 이내 기사만 (오늘 움직임의 이유여야 하므로)
MOVERS_REASON_HOURS = 30


# ════════════════════════════════════════════════════════════════
# (매크로 발표일 속보) 미 CPI·연준 FOMC 발표일엔 급락 키워드가 없어도 관련 내용을 속보로.
#   투자 핵심 정보(물가·금리)라 별도 경로로 확실히 발송. label별 하루 1회.
#   · release_id : FRED 릴리스 번호로 발표일 자동 감지(CPI=10, 고용=50)
#   · dates_key  : macro_calendar.json 의 키(FOMC 등 FRED로 감지 안 되는 것)
#   · after_et   : 발표 시각(ET) — 이 시각 이후에만 발송(결과/성명 확보). [시,분]
#   · summarize  : True면 헤드라인들을 AI로 2~3문장 정리(연준 성명 등), False면 최신 헤드라인 그대로
MACRO_ALERT_EVENTS = [
    {
        "label": "미 소비자물가(CPI)",
        "release_id": 10,
        "after_et": [8, 35],            # CPI는 ET 08:30 발표
        "queries": [("미국 CPI 소비자물가 지수 발표", "ko"),
                    ("US CPI inflation report", "en")],
        "keywords": ["cpi", "소비자물가", "인플레이션", "물가", "inflation", "consumer price"],
        "summarize": False,             # 제목에 수치가 있어 헤드라인 그대로
    },
    {
        "label": "미 연준 FOMC 통화정책",
        "dates_key": "FOMC",            # macro_calendar.json 의 FOMC 일정
        "after_et": [14, 5],            # FOMC 성명은 ET 14:00 발표
        "queries": [("연준 FOMC 기준금리 결정 파월", "ko"),
                    ("Fed FOMC rate decision statement Powell", "en")],
        "keywords": ["fomc", "연준", "기준금리", "파월", "fed", "powell",
                     "rate decision", "금리 동결", "금리 인하", "금리 인상", "통화정책"],
        "summarize": True,              # 연준 성명 내용을 AI로 정리
    },
]

# macro_calendar.json — FRED로 감지 안 되는 발표일(FOMC 등). 연 1회 갱신(Fed 공식 일정).
MACRO_DATES = {}


def _apply_macro_calendar_json():
    global MACRO_DATES
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_calendar.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            MACRO_DATES = json.load(f)
    except Exception as e:
        print(f"⚠️  macro_calendar.json 파싱 실패({e})")


_apply_macro_calendar_json()

# 📐 stock-news-bot — 파이프라인 & 아키텍처

> 국내·해외 주식 뉴스와 시세를 모아 **디스코드 웹훅**으로 발송하는 서버리스 봇.
> 상시 서버 없이 **GitHub Actions 크론**이 곧 런타임이다.
> 이 문서는 고도화 작업의 기준 문서(현행 구조의 SSOT)로 유지한다.

- 저장소: `github.com/JKKook/stock-news-bot` (public, 기본 브랜치 `main`)
- 언어/런타임: Python 3.12 (CI), 로컬 `.venv`는 3.11
- 외부 유료 API: **DeepL(번역) 하나뿐**. 뉴스·시세·공포탐욕지수는 전부 무료 RSS/공개 엔드포인트.

---

## 1. 큰 그림 — 두 개의 독립 파이프라인

성격이 다른 두 파이프라인이 **각자의 진입점 / 워크플로 / 실행 주기**로 돈다.

| 파이프라인 | 진입점 | 워크플로 | 주기 | 성격 |
|---|---|---|---|---|
| **정기 브리핑** | `main.py` | `.github/workflows/schedule.yml` | KST 09:00 / 16:00 / 23:00 (하루 3회) | 그 시점 전체 요약을 항상 발송 |
| **속보·급변 감시** | `alerts.py` | `.github/workflows/alerts.yml` | 10분마다 | 조건 충족 시에만 발송(상태 기반) |

두 파이프라인은 `collect` / `market` / `notify` / `translate`의 헬퍼를 공유한다.

---

## 2. 모듈 맵 (책임 분리)

| 파일 | 책임 | 핵심 함수 |
|---|---|---|
| `config.py` | **모든 튜닝 파라미터의 SSOT** — 수집 대상·개수 상한·이슈 사전·알림 임계값 | (상수 모음) |
| `collect.py` | 뉴스 수집(구글뉴스 RSS·야후·블룸버그) + 헤드라인/Source 가공 | `collect`, `yahoo_headline`, `bloomberg_items`, `build_headlines`, `build_source_links` |
| `market.py` | 실시간 시세(야후 chart API) + CNN 공포탐욕지수 | `get_indices`, `get_fear_greed` |
| `issues.py` | 관심종목 기사 중 '특정 이슈'만 선별(단순 시황 제거) | `is_issue`, `filter_issues` |
| `translate.py` | 영문 제목·발췌문 → 한국어 일괄 번역(DeepL, 실패 시 원문 유지) | `translate_items`, `translate_text` |
| `notify.py` | 디스코드 메시지 조립 + 웹훅 발송(길이 분할·rate limit 대응) | `build_messages`, `send` |
| `events.py` | 속보 기사에서 '사건 지문'(대상+방향) 추출 — Layer 4 중복 억제용 | `fingerprint` |
| `main.py` | 정기 브리핑 오케스트레이션 | `main` |
| `alerts.py` | 속보·급변 감시 오케스트레이션 + 상태 관리 | `main`, `check_indices`, `check_news`, `check_fng` |

> **설정 중심 설계**: 수집 대상·임계값을 바꾸려면 대부분 `config.py`만 수정하면 된다. 다른 모듈은 config를 소비만 한다.

---

## 3. 파이프라인 A — 정기 브리핑 (`main.py`)

```
① 수집 ─────────────────────────────────────────────
   get_indices(INDICES)        시세 10종            [market.py]  야후 chart API
   get_fear_greed()            CNN 공포탐욕지수      [market.py]  CNN dataviz
   yahoo_headline()            대표 헤드라인 1건     [collect.py] 야후 RSS
   collect(MARKET_QUERIES)     시장 뉴스            [collect.py] ┐
   collect(_flatten_sectors()) 섹터별(7섹터×국내/해외) [collect.py] │ 구글뉴스 검색 RSS
   collect(TICKERS)            관심종목 후보(종목당 12건)[collect.py] ┘
   bloomberg_items(BLOOMBERG_FEEDS) 블룸버그 공식 RSS [collect.py]
        │
② 필터 ▼  filter_issues()   종목 기사 중 '이슈'만 선별   [issues.py]  INCLUDE/EXCLUDE 키워드
        │
③ 번역 ▼  translate_items()  en→ko 일괄 번역           [translate.py] DeepL, 실패 시 원문
        │
④ 가공 ▼  build_headlines() / build_source_links()     [collect.py]  지역별 최신순·중복제거
        │
⑤ 조립 ▼  build_messages()   디스코드 메시지 배열 생성   [notify.py]
        │
⑥ 발송 ▼  send()             웹훅 POST(flags=4 임베드 억제)[notify.py]
```

### 발송 메시지 레이아웃 (`notify.build_messages`)
1. 구분선 + 헤더(브리핑 시각 KST)
2. 📊 **대시보드** — 주요 지수 시세 + CNN 공포탐욕지수(게이지 바 + 과거 추이)
3. 📈 Yahoo Finance 대표 헤드라인 (브리핑 내 유일한 본문 링크)
4. 🔥 오늘의 헤드라인 — 🇰🇷국내 / 🇺🇸해외 각 N개
5. 🇰🇷 **국내 증시** — 시장 뉴스 / 섹터별 소식 / 관심 종목
6. 🇺🇸 **해외 증시** — 시장 뉴스 / 섹터별 소식 / 관심 종목 / 🏦 블룸버그 주요 기사
7. 🔗 Source (주요 기사 링크) — 한국 / 미국 / 블룸버그 (제목 하이퍼링크)
8. 구분선

### 메시지 분할 규칙 (`notify._emit`)
- 디스코드 2000자 제한 → **1900자(`DISCORD_LIMIT`)** 기준으로 분할.
- 한 블록(제목+발췌문)은 **쪼개지 않음**.
- `# ` 로 시작하는 **지역 헤더는 항상 새 메시지로 시작**.
- 발송 시 메시지 사이 0.7초 간격 + `429`(rate limit) 시 `retry_after`만큼 대기 후 재전송.

---

## 4. 파이프라인 B — 속보·급변 감시 (`alerts.py`)

```
load_state()  ← alert_state.json  (actions/cache 로 실행 간 이어받기)
   │
   ├─ check_indices()  지수 전일比 ±5/8/15/20% 단계 돌파 시 (단계별 1회)
   ├─ check_fng()      공포탐욕지수가 직전 알림 대비 ±15 변동 시
   └─ check_news()     속보 키워드 게이트를 통과한 90분 내 새 기사
   │
save_state()  → alert_state.json  (sent 키 최근 300개 롤링, 중복 발송 방지)
   │
send()  조건 충족분(속보 기사 내용)만 발송 — 지수 대시보드 미첨부
```

### 상태 관리가 핵심
러너는 매 실행마다 초기화되므로 "이미 보낸 알림"을 기억할 저장소가 필요하다.
`alert_state.json` 파일 하나를 **GitHub Actions 캐시로 실행 간 전달**해 DB 없이 중복 발송을 막는다.

- `check_indices`: 밴드(`ALERT_INDEX_BANDS = [5,8,15,20]`)를 **더 높은 단계로 돌파할 때만** 1회 알림. 날짜(`day`)가 바뀌면 밴드 리셋.
- `check_news`: `ALERT_NEWS_QUERIES`로 검색 후 아래 **5중 게이트**를 통과한 기사만. 최대 `ALERT_MAX_PER_RUN(6)`건.
- `check_fng`: 첫 실행은 기준값만 저장, 이후 `ALERT_FNG_DELTA(15)` 이상 변동 시.

### `check_news` 5중 중복/노이즈 필터 (핵심)
같은 사건이 시차·언론사 차이로 여러 번 오는 '정보 환각'을 막는 5단 방어:

| Layer | 무엇 | 근거 |
|---|---|---|
| **L1 날짜** | `pub is None or pub < cutoff` → 날짜 없는 기사 + 90분 초과 기사 제외 | `alerts.py` check_news |
| **L2 컬럼 제외** | 제목에 `ALERT_NEWS_EXCLUDE`("이번주·주말·마감·시황·정리"…) 있으면 제외 — 오늘 발행됐지만 지난 사건을 되짚는 회고/전망 컬럼 차단 | `config.ALERT_NEWS_EXCLUDE` |
| **L3 속보 키워드** | 제목에 `ALERT_KEYWORDS` 있어야 속보 인정 | `config.ALERT_KEYWORDS` |
| **L4 사건 지문** | `events.fingerprint()` = `대상:방향`(예 `코스피:DOWN`). 같은 지문이 `ALERT_EVENT_WINDOW_HOURS(18h)` 내 이미 알림됐으면 억제 | `events.py`, `state["events"]` |
| **L5 내용 유사도** | 정규화 제목의 **문자 bigram Jaccard** 유사도가 `ALERT_DUP_SIM_THRESHOLD(0.6)` 이상이면, `ALERT_DUP_WINDOW_HOURS(18h)` 내 발송분과 같은 내용으로 보고 억제 — 지문이 못 잡는 '출처만 다른 재탕' 컷(API 무의존) | `alerts._title_sim`, `state["sent_titles"]` |

- **L4가 문자열 dedup의 한계를 보완**: 언론사마다 제목이 달라도 `대상+방향`이 같으면 하나의 사건으로 묶어 재알림을 막는다.
- **L5가 L4의 사각을 메움**: 지문 추출이 애매하거나(대상·방향 불명) 지문은 달라도 표현만 살짝 바뀐 재송고 헤드라인을, 실제 제목 문자 유사도로 다시 한 번 컷한다. 같은 실행 내 선정분과도 비교(런 내 중복 방지). 임베딩 키가 없는 alerts 워크플로에서도 도는 순수 문자열 방식.
- **기사별 속보 타이틀**: 상단은 중립 헤더(`📣 실시간 시장 알림 — HH:MM`)만 두고, 각 기사는 내용에서 뽑은 타이틀을 단다 — `events.headline()` → `🚨 코스피 급락` / `📈 나스닥 급등` / `🌍 지정학 리스크`(전쟁·지정학) / 방향 불명 시 `🚨 속보`. 지수 대시보드는 첨부하지 않는다.
- **방향 구분**: 급락↔급등(UP/DOWN)은 다른 사건. 서킷브레이커/사이드카는 양방향 발동이라 방향 지표에서 제외(같이 붙는 급락/급등이 방향 결정). 심각도 에스컬레이션은 `check_indices` 밴드가 담당.
- **트레이드오프**: 18h 창은 전일 사건의 다음날 아침 회고를 억제하되, 다음날 장중의 '진짜 새' 같은 방향 사건은 대체로 창 밖이라 통과. 창을 늘리면 회고 억제↑·새 사건 민감도↓.
- 상태: `state["events"]`(지문→마지막 알림 ISO시각, 창 지나면 자동 정리) + `state["sent"]`(기사 키 최근 300) + `state["sent_titles"]`(발송 제목 `[iso, 정규화]` 최근 300, L5 유사도 비교용).

---

## 5. 데이터 소스

| 데이터 | 소스 | 인증 | 사용처 |
|---|---|---|---|
| 뉴스(시장/섹터/종목/속보) | Google News 검색 RSS | 없음 | collect / alerts |
| 대표 헤드라인 | Yahoo Finance RSS | 없음 | main |
| 시세 10종 | Yahoo `query1.finance` chart API | 없음(UA 위장) | market |
| 공포탐욕지수 | CNN dataviz 엔드포인트 | 없음(UA 위장) | market |
| 블룸버그 | Bloomberg 공식 신디케이션 RSS | 없음 | collect |
| 번역 | DeepL API | **무료 키** | translate |

- **수집 대상 정의는 전부 `config.py`**: `INDICES`(시세 10종), `MARKET_QUERIES`, `SECTORS`(7개 섹터), `TICKERS`(관심종목 16개), `BLOOMBERG_FEEDS`.
- `LOOKBACK_HOURS = 12` — 브리핑은 최근 12시간 내 기사만.

---

## 6. CI/CD 연결 구조

### 트리거 (`on:`)
- `schedule.cron` — **UTC 기준**. 브리핑 `0 0,7,14 * * *`, 알림 `*/10 * * * *`.
  - GitHub 크론은 부하 시 **지연·누락 가능**(정시 보장 없음). 알림 파이프라인의 상태 기반 중복제거가 이를 흡수.
- `workflow_dispatch: {}` — 두 워크플로 모두 Actions 탭에서 **수동 실행** 가능.

### 시크릿 주입
```yaml
env:
  DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
  DEEPL_API_KEY:       ${{ secrets.DEEPL_API_KEY }}
```
- 코드는 `os.environ.get()`으로만 읽음 → 시크릿이 없어도 **죽지 않음**:
  - 웹훅 없으면 `notify.send()`는 콘솔 출력으로 폴백.
  - DeepL 키 없거나 실패 시 `translate`는 원문(영어) 유지.
- 저장소 **Settings → Secrets and variables → Actions**에 등록.
- `.env` / `alert_state.json`은 `.gitignore`로 커밋 제외.

### 상태 지속성 (alerts 전용)
```yaml
concurrency:              # 실행 겹침 방지 → 상태파일 경합 차단
  group: alerts
  cancel-in-progress: false

- uses: actions/cache@v4  # 실행 간 alert_state.json 이어받기 (롤링 키 패턴)
  with:
    path: alert_state.json
    key: alert-state-${{ github.run_id }}
    restore-keys: alert-state-
```
- `key`는 매번 새 `run_id`로 저장(최신본 갱신), `restore-keys`로 직전 실행분 복원.
- ⚠️ `actions/cache`는 **7일 미접근 시 삭제** → 워크플로가 오래 멈추면 중복 억제 이력이 리셋될 수 있음.

### 두 워크플로의 CI 차이
- `schedule.yml`: 순수 실행(`checkout → setup-python → pip install → python main.py`).
- `alerts.yml`: `setup-python`에 `cache: "pip"` + 상태 캐시 + concurrency 가드 추가.

---

## 7. 빌드 & 실행

- **빌드 도구 없음** — 순수 파이썬 스크립트(컴파일/번들/패키징 단계 없음).
- **의존성**(`requirements.txt`): `feedparser`, `requests`, `deepl` 3개.
  - (로컬 `.venv`엔 `deep-translator` 잔재가 있으나 requirements엔 없음 — 미사용.)
- **CI = 런타임**: Actions가 매 실행마다 설치 후 스크립트 실행. 상시 서버 불필요.
- **로컬 실행**:
  ```bash
  .venv/bin/python main.py     # 정기 브리핑
  .venv/bin/python alerts.py   # 속보 감시
  ```
  - 로컬 `.env`에 `DEEPL_API_KEY`만 있음 → 웹훅 미설정 시 발송 대신 콘솔 출력.

---

## 8. 설계 특징 (고도화 시 유지할 원칙)

- **장애 내성**: 시세·번역·개별 피드가 실패해도 `try/except`로 건너뛰고 계속 진행 — 봇이 멈추지 않는다.
- **관심종목은 '이슈'만**: 단순 주가 등락 기사는 `issues.py`가 걸러 시그널만 남긴다.
- **중복 억제**: 제목 정규화 키(`title.replace(" ", "")`)로 헤드라인·Source·속보 전반에서 중복 제거.
- **설정과 로직 분리**: 수집 대상/임계값은 `config.py`, 동작은 각 모듈.

---

## 9. 현재 한계 / 고도화 후보

- **테스트·린트 CI 없음** — 현재는 "설치 → 실행"뿐. PR 검증 파이프라인 부재.
- **크론 정확도** — `*/10`은 정시 보장 아님(부하 시 밀림).
- **캐시 만료** — 7일 미접근 시 상태 리셋 가능.
- **번역 비용/한도** — DeepL 무료 키 월 50만 자 한도. 발췌문 번역량 증가 시 초과 우려.
- **소스 견고성** — 야후/CNN 비공식 엔드포인트는 스키마 변경 시 조용히 실패(빈 섹션).
- **종목/섹터 하드코딩** — 관심종목·섹터가 `config.py`에 고정. 동적 관리(외부 설정/DB) 여지.

---

_최종 갱신 기준 커밋: `7b5e3a9` (스포일러 제거 → 섹터별 소식·관심 종목 펼친 형태로 복구)_

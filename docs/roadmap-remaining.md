# 🧩 남은 고도화 백로그 (2026-07-06 기준)

> P0~P3 + 2026-07-06 배치(속보 확증·세션 구분·요약 폴백·국내 수급·데이터 위생·의미 dedup·호라이즌 풀버전·watchlist 외부화)까지 완료.
> 별도 인프라: Cloudflare Workers cron 스케줄러로 GitHub cron 지연 해결(→ [scheduling-setup.md](scheduling-setup.md)).
> 아래는 **아직 안 한 것들** — 가치·난이도·리스크와 함께. 상세 진행분은 [research-roadmap.md](research-roadmap.md) 참고.
> 경계 유지: **맥락·근거만 제공, 매수/매도 신호 없음.**
>
> _2026-07-06: 백로그 R1~R9 전부 구현 완료. 아래는 완료 기록 + 유지보수 관찰 지점._

---

## ✅ 완료됨
- **R1. 정확도 측정 루프** (2026-07-06 구현) — 되돌림 경고를 `prediction_log.json`에 기록하고
  `MEASURE_HORIZON_DAYS`(7일) 뒤 실제 가격으로 채점 → 브리핑에 `📏 판정 정확도` 적중률 표시.
  `measure.py`, `notify._reversal_hits`(공유 판정), schedule.yml 캐시 persist. 실측 검증 완료.
- **R2. 속보 시장 반응 확증** (2026-07-06) — 지수·지정학 속보를 이미 조회한 indices/VIX 움직임과 대조.
  지수 |등락|≥`ALERT_MARKET_MOVE` → '📈 실제 반응', 미만 → '📉 시장 반응 미미'(과대 제목 폭로).
  지정학 키워드+VIX 급등 → '공포 급등'. `alerts._market_confirm`, `config.ALERT_MARKET_MAP`. 추가 호출 0.
  (당초 '모든 뉴스→티커' 일반화 대신 지수·지정학 매핑으로 실용화. 개별종목은 P3 `_confirm_move`가 담당.)
- **R3. 국내 경제 캘린더 정확화** (2026-07-06) — `kr_calendar.json` 외부화 + `confirmed` 플래그
  (공식 발표일 확정 시 `true` → '(잠정)' 자동 제거). CPI는 알고리즘 계산(매월 2번째 영업일)으로 항상 신선,
  확정 CPI와 중복제거. `config._apply_kr_calendar_json`, `catalysts._kr_cpi_dates`.
  (무료 발표일 API가 없어 자동수집 대신 '유지관리 쉬운 구조 + 잠정 명시'로 실용화 — 사용자가 연 1회 공식일 반영.)

- **R4. 의미 dedup 확대** (2026-07-06) — 임베딩 근접중복 제거를 헤드라인뿐 아니라 **테마·관심종목 뉴스**까지.
  그룹 교차 dedup(`semantic.dedupe_groups`, 우선순위 관심종목>테마, 임베딩 1회). 실측·통합 검증 완료.

- **R5. 국내 시장 수급(개인/외국인/기관)** (2026-07-06) — 코스피·코스닥 투자자별 순매매를 네이버 모바일
  integration API로 조회(`market.kr_market_flow`, `notify._kr_market_flow_blocks`). 개인 순매매 포함 →
  CNN 공포탐욕(미국 편향) 보완하는 국내 수급·심리 지표. 실측 검증(코스피 개인 +2.7조 등).
- **R6. 코스피200 선물(야간 세션)** (2026-07-06) — 네이버 `FUT` 시세를 대시보드에 추가(`market.get_kr_futures`).
  야간 세션(18:00~05:00)엔 야간선물 시세를 반영 → 나스닥선물과 함께 밤사이 국내 방향성(특히 KST 23시 브리핑).
  (전용 '야간선물' 무료 심볼은 없어 FUT로 대체 — 정규/야간 세션 시세 표시. 주간엔 현물과 유사.)
- **R7. 종목별 속보 알림 on/off** (2026-07-06) — watchlist.json `"alert":false` 시 속보 확증 제외(`config.TICKER_ALERT`).
- **R8. 임계값 외부화** (2026-07-06) — 민감도 값들을 `settings.json`으로(코드 수정 없이 조정). config 로더가 override,
  import 전파 확인(`config._apply_settings_json`).
- **R9. 음력 공휴일 자동계산** (2026-07-06) — `korean_lunar_calendar`로 설·추석(연휴)·부처님오신날을 해마다 자동 계산
  → 정규 브리핑 스킵(`config.is_kr_holiday`). 2026·2027 실측 검증.

**✅ 백로그 R1~R9 전부 완료 (2026-07-06). 신규 아이디어가 나오면 아래에 추가.**

---

## 🔧 유지보수 관찰 지점 (기능 아님)
- **스크래핑 취약**: 국내 수급(네이버), 향후 국내 캘린더 — 레이아웃 변경 시 조용히 생략됨(그레이스풀). 주기적 확인 필요.
- **무료 API 쿼터**: Gemini 요약(폴백 있음)·임베딩. 한도 초과 시 해당 섹션만 생략.
- **yfinance/야후 비공식 API**: 과호출·형식 변경 리스크. 지수·시세·PER·실적 의존.

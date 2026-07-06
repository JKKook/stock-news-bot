# ⏰ 정시 스케줄링 — Cloudflare Workers Cron 설정 가이드

> **문제**: GitHub Actions 예약 cron(`schedule:`)은 best-effort라 매일 1~4시간씩 지연됨(특히 UTC 00:00).
> **해법**: Cloudflare Worker의 정시 cron이 GitHub `workflow_dispatch` API를 호출.
> **원리**: dispatch로 트리거된 런은 **지연 없이 즉시 시작**된다(지연은 `schedule:` 이벤트에만 걸림).
> 기존 파이프라인·시크릿은 그대로. 트리거 방식만 교체한다.

파일: [scheduler/worker.js](../scheduler/worker.js) · [scheduler/wrangler.toml](../scheduler/wrangler.toml)

---

## 1. GitHub PAT 발급 (dispatch 호출용)
1. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token
2. **Repository access**: Only select repositories → `stock-news-bot`
3. **Repository permissions** → **Actions: Read and write** (이것만 있으면 됨)
4. 만료일 설정 후 생성 → 토큰 문자열 복사(한 번만 보임)

## 2. Cloudflare 계정 + wrangler
```bash
npm install -g wrangler      # 또는 npx wrangler 사용
cd scheduler
wrangler login               # 브라우저로 CF 계정 인증(무료 계정 OK)
```

## 3. 토큰을 secret 으로 주입
```bash
wrangler secret put GITHUB_TOKEN
# 프롬프트에 1단계 PAT 붙여넣기 (코드/깃에 절대 커밋하지 말 것)
```

## 4. 배포
```bash
wrangler deploy
```
배포되면 CF가 `wrangler.toml`의 cron(UTC)에 맞춰 자동 실행한다.

## 5. 동작 확인
```bash
wrangler tail                # 실시간 로그 — "✅ dispatch ... 성공" 확인
```
- 로컬 테스트(발화 시뮬레이션):
  ```bash
  wrangler dev --test-scheduled
  # 다른 터미널에서:
  curl "http://localhost:8787/__scheduled?cron=0+0,7,14+*+*+*"   # 브리핑
  curl "http://localhost:8787/__scheduled?cron=*/10+*+*+*+*"     # 속보
  ```
- GitHub → Actions 탭에서 `event: workflow_dispatch` 런이 즉시 뜨는지 확인.

## 6. 컷오버 — GitHub `schedule:` 제거 (중복 발송 방지) ⚠️
CF dispatch가 정상 확인되면, **GitHub 예약 cron을 제거**해야 한다.
안 그러면 지연된 GitHub schedule 런 + CF dispatch 런이 **둘 다 돌아 중복 발송**된다.

`.github/workflows/schedule.yml` 과 `alerts.yml` 에서 `on:` 의 `schedule:` 블록만 삭제하고
`workflow_dispatch: {}` 는 남긴다. 예:
```yaml
on:
  # schedule:                     ← 삭제(또는 주석) — CF Worker가 대신 트리거
  #   - cron: "0 0,7,14 * * *"
  workflow_dispatch: {}
```
> 컷오버는 **CF가 확실히 도는 걸 확인한 뒤**에. 그 전엔 GitHub schedule을 백업으로 남겨둘 것.

---

## 시간 바꾸기
- 브리핑 시각: `wrangler.toml` 의 `0 0,7,14 * * *`(UTC) 수정 + `worker.js` 분기 문자열도 **동일하게** 수정 → `wrangler deploy`.
  - 예) KST 08시 아침 브리핑 = UTC 23시 → `0 23,7,14 * * *` (분기 문자열도 일치시킬 것).
- 속보 주기: `*/10` → `*/15` 등.

## 비용·한계
- Cloudflare Workers **무료 플랜**으로 충분(요청 10만/일, cron 트리거 포함). 속보 10분×144 + 브리핑 3 ≈ 147회/일.
- CF cron도 매우 드물게 수십 초 지연될 수 있으나 GitHub schedule(수 시간)과는 차원이 다름.
- PAT 만료 시 dispatch가 401로 실패 → `wrangler tail` 로그로 감지. 만료 전 갱신.

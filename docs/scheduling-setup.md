# ⏰ 정시 스케줄링 — Cloudflare Workers Cron 설정 가이드 (상세)

> **문제**: GitHub Actions 예약 cron(`schedule:`)은 best-effort라 매일 1~4시간 지연(특히 UTC 00:00).
> **해법**: Cloudflare Worker의 정시 cron이 GitHub `workflow_dispatch` API 호출.
> **원리**: dispatch로 트리거된 런은 **지연 없이 즉시 시작**(지연은 `schedule:` 이벤트에만 걸림).
> 기존 파이프라인·시크릿은 그대로 두고 **트리거만 교체**한다.

## ✅ 이미 준비된 것 (코드/검증 완료)
- [scheduler/worker.js](../scheduler/worker.js) — cron 분기해 브리핑/속보 dispatch (**라우팅 로직 검증 완료**)
- [scheduler/wrangler.toml](../scheduler/wrangler.toml) — cron: UTC 00·07·14(=KST 09·16·23) + 10분마다
- [scheduler/package.json](../scheduler/package.json) — `npm run` 스크립트 (**wrangler 3.114 / node 22 설치 검증 완료**)

## 🙋 사용자가 직접 해야 하는 것 (계정 인증 필요 — 대행 불가)
아래 4단계뿐입니다. 순서대로 따라 하시면 됩니다.

---

### 1️⃣ GitHub PAT 발급 (dispatch 호출용 열쇠)
1. GitHub 로그인 → 우측 상단 프로필 → **Settings**
2. 좌측 맨 아래 **Developer settings**
3. **Personal access tokens → Fine-grained tokens** → **Generate new token**
4. 입력:
   - **Token name**: `cf-scheduler-dispatch`
   - **Expiration**: 90 days(권장) 또는 원하는 기간
   - **Repository access**: **Only select repositories** → `JKKook/stock-news-bot` 선택
   - **Permissions** → **Repository permissions** → **Actions** 항목을 **Read and write** 로 설정
     - (다른 권한은 건드릴 필요 없음)
5. **Generate token** → 나온 토큰 문자열 **복사** (`github_pat_...`, 이 화면에서만 보임)

> ⚠️ 이 토큰은 **절대 깃/코드에 넣지 마세요.** 아래 3단계에서 Cloudflare 시크릿으로만 넣습니다.

---

### 2️⃣ Cloudflare 계정 + wrangler 로그인
1. [dash.cloudflare.com](https://dash.cloudflare.com) 무료 가입(신용카드 불필요)
2. 터미널에서:
   ```bash
   cd scheduler
   npm install          # wrangler 로컬 설치 (한 번만)
   npm run login        # 브라우저 열림 → Cloudflare 계정 인증 → Allow
   ```

---

### 3️⃣ 배포 + 토큰 주입
```bash
cd scheduler
npm run deploy         # Worker 를 Cloudflare 에 업로드 (cron 자동 등록됨)
npm run secret         # 프롬프트에 1단계 PAT 붙여넣기 → Enter
```
- 순서 중요: **deploy 먼저**(Worker 생성) → **secret** 주입. secret 넣은 뒤부터 dispatch가 인증됨.
- 시크릿만 바꿀 땐 `npm run secret` 재실행(재배포 불필요).

---

### 4️⃣ 동작 확인
```bash
cd scheduler
npm run tail           # 실시간 로그 스트림
```
- 다음 cron 시각(예: KST 09·16·23시, 매 10분)에 로그에 **`✅ dispatch schedule.yml 성공`** / **`✅ dispatch alerts.yml 성공`** 이 떠야 정상.
- GitHub → 저장소 → **Actions** 탭에서 **`event: workflow_dispatch`** 런이 **즉시** 뜨는지 확인(지연 없음).
- 즉시 테스트하려면 Cloudflare 대시보드 → Workers → `stock-news-scheduler` → **Triggers/Cron → "Trigger scheduled event"** 로 수동 발화 가능.

---

## 🔀 5️⃣ 컷오버 — GitHub `schedule:` 제거 (중복 발송 방지) ⚠️
CF dispatch가 정상 도는 걸 **확인한 뒤**, GitHub 예약 cron을 제거해야 합니다.
안 그러면 **지연된 GitHub schedule 런 + CF dispatch 런이 둘 다 돌아 중복 발송**됩니다.

`.github/workflows/schedule.yml` 과 `alerts.yml` 의 `on:` 에서 `schedule:` 만 지우고 `workflow_dispatch:` 는 남깁니다:
```yaml
on:
  # schedule:                     ← 삭제(또는 주석). CF Worker가 대신 트리거.
  #   - cron: "0 0,7,14 * * *"
  workflow_dispatch: {}
```
> 이 편집은 **말씀해 주시면 제가 바로 처리**해 드립니다(CF 확인 후).

---

## 🛠️ 유지보수 / 트러블슈팅

**시간 바꾸기**
- 브리핑 시각: `wrangler.toml` 의 `0 0,7,14 * * *`(UTC) **와** `worker.js` 의 분기 문자열을 **똑같이** 수정 → `npm run deploy`.
  - 예) KST 08시 아침 = UTC 23시 → `0 23,7,14 * * *`
- 속보 주기: `*/10` → `*/15` 등.

**자주 나는 문제**
| 증상 | 원인 | 해결 |
|---|---|---|
| `tail`에 `❌ dispatch ... 401` | PAT 미설정/만료/권한부족 | `npm run secret` 재주입, PAT에 Actions:R/W 확인 |
| `❌ ... 404` | 저장소/워크플로명 오타 | `worker.js` OWNER/REPO/파일명 확인 |
| 중복 발송 | GitHub `schedule:` 아직 살아있음 | 위 5번 컷오버 수행 |
| `wrangler dev` 로컬 실행 안 됨 | workerd 설치 스크립트 미승인(npm) | `npm approve-scripts` 후 재설치 (배포엔 무관) |

**비용·한계**
- Cloudflare Workers **무료 플랜**으로 충분(요청 10만/일). 속보 144 + 브리핑 3 ≈ 147회/일.
- CF cron도 드물게 수십 초 지연 가능하나 GitHub schedule(수 시간)과는 차원이 다름.
- 주말·공휴일 스킵은 그대로 `main.py`가 처리 → CF는 무조건 dispatch만.

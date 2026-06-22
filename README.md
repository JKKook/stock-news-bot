# 📰 주식 이슈 브리핑 봇

국내·해외 주식 관련 이슈를 **하루 3번 자동으로 모아 디스코드로 보내주는** 봇입니다.
뉴스 본문 발췌는 Bing 뉴스(무료, API 키 불필요)에서, 대표 헤드라인 링크는 Yahoo Finance에서 가져옵니다.
영어 기사는 구글 번역으로 자동 한국어 번역됩니다.

브리핑 구성:
1. 📈 Yahoo Finance 대표 헤드라인 (브리핑 내 유일한 링크)
2. 🔥 오늘의 헤드라인 10개 (지역_짧은제목)
3. 📊 시장 전체: 코스피 / 코스닥 / 나스닥 / S&P·다우 / 환율·금리
4. 🏭 섹터별: AI인프라 / 피지컬AI / 데이터센터 / 전력기기 / 전력인프라 / 반도체 / 양자컴퓨팅 (국내+해외)
5. ⭐ 관심 종목: 해외·국내 (자유롭게 변경 가능)

기사는 링크 대신 **제목 + 본문 발췌문(인용)** 텍스트로 보여줍니다.
실행: GitHub Actions가 **한국시간 오전 9시 · 오후 4시 · 오후 11시** 자동 실행.
관심 종목은 단순 주가변동은 빼고 **구체적 이슈(계약·수주·제품 등)** 가 있는 종목만 표시합니다.

---

## 1단계 — 디스코드 웹훅 주소 만들기 (5분)

봇이 메시지를 보낼 "주소"를 만드는 과정입니다.

1. 디스코드에서 소식을 받을 **채널** 옆 ⚙️(편집) 클릭
2. **연동(Integrations)** → **웹후크(Webhooks)** → **새 웹후크** 클릭
3. 이름을 정하고(예: 주식봇), **웹후크 URL 복사** 클릭
4. 복사한 주소를 잘 보관하세요. (예: `https://discord.com/api/webhooks/...`)

> 이 주소를 아는 사람은 누구나 채널에 글을 쓸 수 있으니 **공개하지 마세요.**

---

## 2단계 — GitHub에 올리기

1. https://github.com 가입(무료) 후 새 저장소(repository) 생성 → 이름 예: `stock-news-bot`
2. 이 폴더(`stock-news-bot`)를 그 저장소에 올립니다. 터미널에서:

   ```bash
   cd ~/stock-news-bot
   git init
   git add .
   git commit -m "주식 이슈 브리핑 봇"
   git branch -M main
   git remote add origin https://github.com/<내아이디>/stock-news-bot.git
   git push -u origin main
   ```

---

## 3단계 — 디스코드 주소를 GitHub에 비밀로 등록

1. 올린 GitHub 저장소 페이지 → **Settings** 탭
2. 왼쪽 **Secrets and variables** → **Actions** 클릭
3. **New repository secret** 클릭
4. Name 칸에 정확히: `DISCORD_WEBHOOK_URL`
5. Secret 칸에 1단계에서 복사한 웹훅 주소 붙여넣기 → **Add secret**

---

## 4단계 — 작동 확인

1. GitHub 저장소 → **Actions** 탭
2. 왼쪽 **주식 이슈 브리핑** 클릭 → 오른쪽 **Run workflow** 버튼으로 지금 바로 한 번 실행
3. 디스코드 채널에 브리핑이 도착하면 성공! 이후엔 하루 3번 자동으로 옵니다.

---

## 관심 종목 바꾸기

[config.py](config.py) 파일의 `TICKERS` 목록만 고치면 됩니다.

```python
TICKERS = [
    ("삼성전자",   "삼성전자 주가",   "ko"),   # 한국 뉴스
    ("애플",       "Apple AAPL stock", "en"),  # 미국 뉴스
    # ("카카오",   "카카오 주가",     "ko"),   # 이렇게 추가
]
```

- 형식: `("보여줄 이름", "검색어", "언어")` — 언어는 한국 `"ko"`, 미국 `"en"`
- 발송 시간을 바꾸려면 [.github/workflows/schedule.yml](.github/workflows/schedule.yml) 의 `cron` 수정
  (`"0 0,12 * * *"` 은 UTC 기준 → 한국시간 +9시간)

---

## 내 컴퓨터에서 테스트해보기

```bash
cd ~/stock-news-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 디스코드 없이 화면 출력만 보기
.venv/bin/python main.py

# 실제로 디스코드로 보내보기
DISCORD_WEBHOOK_URL="여기에_웹훅주소" .venv/bin/python main.py
```

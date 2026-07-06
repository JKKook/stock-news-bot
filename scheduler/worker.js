// ─────────────────────────────────────────────────────────────
//  Cloudflare Worker — 정시 cron 스케줄러
//  GitHub Actions 예약 cron(schedule:)의 수 시간 지연을 우회한다.
//  CF cron(정시·신뢰성 높음)이 GitHub workflow_dispatch API를 호출 →
//  dispatch 로 트리거된 런은 '지연 없이' 즉시 시작된다.
//
//  · CF cron 은 UTC 기준. wrangler.toml 의 crons 와 아래 분기 문자열이 정확히 일치해야 함.
//  · GITHUB_TOKEN 은 wrangler secret 으로 주입(Fine-grained PAT, Actions: Read and write).
//  · 주말·공휴일 스킵은 그대로 main.py 가 처리하므로 여기선 무조건 dispatch 만 한다.
// ─────────────────────────────────────────────────────────────

const OWNER = "JKKook";
const REPO = "stock-news-bot";
const BRIEFING_WF = "schedule.yml"; // 정규 브리핑
const ALERTS_WF = "alerts.yml";     // 속보·급변

async function dispatch(workflow, env) {
  const url = `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${workflow}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "cf-worker-scheduler",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (!res.ok) {
    const text = await res.text();
    console.log(`❌ dispatch ${workflow} 실패: ${res.status} ${text}`);
    throw new Error(`dispatch ${workflow} failed: ${res.status}`);
  }
  console.log(`✅ dispatch ${workflow} 성공`);
}

export default {
  // CF cron 이 발화하면 호출. event.cron = 방금 매칭된 cron 표현식.
  async scheduled(event, env, ctx) {
    if (event.cron === "0 0,7,14 * * *") {
      ctx.waitUntil(dispatch(BRIEFING_WF, env)); // 정규 브리핑 (KST 09·16·23시)
    } else if (event.cron === "*/10 * * * *") {
      ctx.waitUntil(dispatch(ALERTS_WF, env));    // 속보 (10분마다)
    } else {
      console.log(`알 수 없는 cron: ${event.cron}`);
    }
  },

  // 헬스 체크용(브라우저로 열면 상태만 반환 — 트리거 기능 없음).
  async fetch() {
    return new Response("stock-news scheduler alive", { status: 200 });
  },
};

// ─────────────────────────────────────────────────────────────
//  Cloudflare Worker — 시장 개장/마감 기준 정시 스케줄러
//  GitHub Actions 예약 cron(schedule:)은 매일 수 시간 지연되므로,
//  CF cron(정시)이 GitHub workflow_dispatch API를 호출한다(dispatch 런은 즉시 시작).
//
//  발송 시각(리서치 노트 — 해당 시장 섹션만):
//    · KST 08:30  [마켓 뷰]     코스피/코스닥 개장 전   → focus=KR, kind=view
//    · KST 16:30  [마켓 클로징] 코스피/코스닥 마감 후   → focus=KR, kind=closing
//    · ET 09:30   [마켓 뷰]     나스닥 개장            → focus=US, kind=view
//    · ET 16:10   [마켓 클로징] 나스닥 마감 후          → focus=US, kind=closing
//    · 10분마다   속보·급변 감시
//
//  ⏰ 서머타임: 미국장은 UTC 기준 시각이 EDT/EST에 따라 1시간 밀린다.
//     → cron을 두 시각(EDT/EST 후보) 모두에 걸어두고, 실제 뉴욕 현지시각이
//       목표(09:30 / 16:10)와 일치할 때만 발송한다. 별도 설정 없이 자동 대응.
// ─────────────────────────────────────────────────────────────

const OWNER = "JKKook";
const REPO = "stock-news-bot";
const BRIEFING_WF = "schedule.yml"; // 정규 브리핑(리서치 노트)
const ALERTS_WF = "alerts.yml";     // 속보·급변

async function dispatch(workflow, env, inputs) {
  const url = `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${workflow}/dispatches`;
  const body = { ref: "main" };
  if (inputs) body.inputs = inputs; // alerts.yml 은 inputs 미선언 → 넘기지 않음
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "cf-worker-scheduler",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    console.log(`❌ dispatch ${workflow} 실패: ${res.status} ${await res.text()}`);
    throw new Error(`dispatch ${workflow} failed: ${res.status}`);
  }
  console.log(`✅ dispatch ${workflow} ${JSON.stringify(inputs || {})}`);
}

/** 뉴욕(ET) 현지 시:분 — 서머타임(EDT/EST)을 IANA 타임존이 자동 처리. */
function etNow(date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (t) => parseInt(parts.find((p) => p.type === t).value, 10);
  return { h: get("hour"), m: get("minute") };
}

export default {
  async scheduled(event, env, ctx) {
    const cron = event.cron;
    const now = new Date(event.scheduledTime);

    // 속보 — 10분마다
    if (cron === "*/10 * * * *") {
      ctx.waitUntil(dispatch(ALERTS_WF, env));
      return;
    }

    // 국내 — KST 08:30(UTC 23:30) 개장 전 / KST 16:30(UTC 07:30) 마감 후
    if (cron === "30 7,23 * * *") {
      const kind = now.getUTCHours() === 23 ? "view" : "closing";
      ctx.waitUntil(dispatch(BRIEFING_WF, env, { focus: "KR", kind }));
      return;
    }

    // 미국 — 나스닥 개장 09:30 ET (EDT=UTC13:30 / EST=UTC14:30 → 실제 ET로 판별)
    if (cron === "30 13,14 * * *") {
      const et = etNow(now);
      if (et.h === 9 && et.m === 30) {
        ctx.waitUntil(dispatch(BRIEFING_WF, env, { focus: "US", kind: "view" }));
      } else {
        console.log(`⏭️  나스닥 개장 아님(ET ${et.h}:${et.m}) — 서머타임 보정으로 건너뜀`);
      }
      return;
    }

    // 미국 — 나스닥 마감 후 16:10 ET (EDT=UTC20:10 / EST=UTC21:10)
    if (cron === "10 20,21 * * *") {
      const et = etNow(now);
      if (et.h === 16 && et.m === 10) {
        ctx.waitUntil(dispatch(BRIEFING_WF, env, { focus: "US", kind: "closing" }));
      } else {
        console.log(`⏭️  나스닥 마감 아님(ET ${et.h}:${et.m}) — 서머타임 보정으로 건너뜀`);
      }
      return;
    }

    console.log(`알 수 없는 cron: ${cron}`);
  },

  async fetch() {
    return new Response("stock-news scheduler alive", { status: 200 });
  },
};

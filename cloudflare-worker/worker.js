/**
 * Cloudflare Worker — Gemini API Proxy for visitfudan.com AI 助手
 * （fudan-ai-proxy 的完整原始碼；部署在 Cloudflare，不是 GitHub Pages）
 *
 * 使用 Google Gemini API 免費層(gemini-3.6-flash)，不需要信用卡。
 * API key 只存在 Worker 的環境變數（Secret）裡，前端永遠看不到。
 *
 * 健康檢查：透過 Cron Trigger 定期呼叫 Gemini API 測試是否正常，
 * 結果回報到 Plausible 自訂事件，不需要額外的第三方服務或金鑰。
 * 記得到 Cloudflare Dashboard → 這個 Worker → Settings → Trigger Events
 * → Cron Triggers 新增排程，例如每 6 小時一次的 cron expression。
 *
 * 站牌問題回報：POST /report → 在 GitHub 開 Issue（見下方 handleReport）。
 *
 * 需要的 Secrets：
 *   GEMINI_API_KEY  — Gemini API 金鑰
 *   GITHUB_TOKEN    — fine-grained token，只給 fudan-website repo 的 Issues: Read and write
 */

const ALLOWED_ORIGINS = [
  "https://www.visitfudan.com",
  "https://visitfudan.com",
];
const ALLOWED_TOOLS = ["search_bus_routes", "search_news"];
const MODEL = "gemini-3.6-flash";
const GEMINI_URL = `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`;
const PLAUSIBLE_DOMAIN = "visitfudan.com";

export default {
  async fetch(request, env) {
    // 站牌回報走 /report，其他請求照舊轉給 Gemini
    const reportRes = await handleReport(request, env);
    if (reportRes) return reportRes;

    const origin = request.headers.get("Origin") || "";
    const isAllowedOrigin = ALLOWED_ORIGINS.includes(origin);

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders(origin, isAllowedOrigin) });
    }
    if (request.method !== "POST") {
      return jsonResponse({ error: "Method not allowed" }, 405, origin, isAllowedOrigin);
    }

    // 有帶 Origin 時才檢查白名單（避免直接用 curl/Postman 測試也擋掉，但正常瀏覽器一定會帶）
    if (origin && !isAllowedOrigin) {
      return jsonResponse({ error: "Origin not allowed" }, 403, origin, isAllowedOrigin);
    }

    let body;
    try {
      body = await request.json();
    } catch (e) {
      return jsonResponse({ error: "Invalid JSON" }, 400, origin, isAllowedOrigin);
    }

    if (!Array.isArray(body.contents)) {
      return jsonResponse({ error: "contents is required" }, 400, origin, isAllowedOrigin);
    }

    if (Array.isArray(body.tools)) {
      for (const toolGroup of body.tools) {
        const decls = toolGroup.functionDeclarations || [];
        const badTool = decls.find(t => !ALLOWED_TOOLS.includes(t.name));
        if (badTool) {
          return jsonResponse({ error: `Tool not allowed: ${badTool.name}` }, 400, origin, isAllowedOrigin);
        }
      }
    }

    const upstreamBody = {
      contents: body.contents,
      system_instruction: body.system_instruction,
      tools: body.tools,
      generationConfig: { maxOutputTokens: 1000 },
    };

    try {
      const geminiRes = await fetch(`${GEMINI_URL}?key=${env.GEMINI_API_KEY}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(upstreamBody),
      });
      const data = await geminiRes.json();
      return jsonResponse(data, geminiRes.status, origin, isAllowedOrigin);
    } catch (e) {
      return jsonResponse({ error: "Upstream request failed" }, 502, origin, isAllowedOrigin);
    }
  },

  // Cron Trigger 觸發：定期自我檢測 Gemini API 是否正常運作
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runHealthCheck(env));
  },
};

async function runHealthCheck(env) {
  const testBody = {
    contents: [{ role: "user", parts: [{ text: "回覆兩個字：正常" }] }],
    generationConfig: { maxOutputTokens: 20 },
  };

  let status = "success";
  let detail = "";

  try {
    const res = await fetch(`${GEMINI_URL}?key=${env.GEMINI_API_KEY}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(testBody),
    });
    const data = await res.json();

    if (!res.ok) {
      status = "fail";
      const msg = (data.error && data.error.message) || JSON.stringify(data).slice(0, 150);
      // 特別標記「模型下架」這種曾經真實發生過的狀況，方便一眼看出原因
      detail = /no longer available|not found|NOT_FOUND/i.test(msg)
        ? "模型可能已下架或改名：" + msg.slice(0, 100)
        : msg.slice(0, 150);
    } else {
      const candidate = data.candidates && data.candidates[0];
      const hasText = candidate && candidate.content && candidate.content.parts && candidate.content.parts.length;
      if (!hasText) {
        status = "fail";
        detail = "API 回應成功但沒有內容，格式可能有變";
      }
    }
  } catch (e) {
    status = "fail";
    detail = "連線失敗：" + (e.message || String(e));
  }

  await reportToPlausible(status, detail);
}

async function reportToPlausible(status, detail) {
  try {
    await fetch("https://plausible.io/api/event", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // Plausible 的事件 API 需要一個看起來像瀏覽器的 User-Agent 才會被接受
        "User-Agent": "Mozilla/5.0 (Cloudflare-Worker-HealthCheck)",
      },
      body: JSON.stringify({
        domain: PLAUSIBLE_DOMAIN,
        name: status === "success" ? "Worker 健康檢查：成功" : "Worker 健康檢查：失敗",
        url: `https://${PLAUSIBLE_DOMAIN}/health-check`,
        props: detail ? { detail } : undefined,
      }),
    });
  } catch (e) {
    // 回報失敗就算了，不影響主要功能，不拋出例外
  }
}

function corsHeaders(origin, isAllowedOrigin) {
  return {
    "Access-Control-Allow-Origin": isAllowedOrigin ? origin : ALLOWED_ORIGINS[0],
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
}

function jsonResponse(obj, status, origin, isAllowedOrigin) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders(origin, isAllowedOrigin) },
  });
}

/* ================================================================
 * 站牌問題回報：POST /report → 在 GitHub 開一個 Issue
 * ================================================================ */

const REPORT_REPO = "whatasmalls-coder/fudan-website";
const REPORT_TYPES = {
  location: "站牌位置不對",
  info: "站名或時間有誤",
  moved: "站牌已取消或搬移",
  other: "其他",
};

// 同一個 IP 每 10 分鐘最多 5 則（存在 Worker 記憶體裡，只是擋手滑連按和簡單灌水，不是嚴格限制）
const RATE_WINDOW_MS = 10 * 60 * 1000;
const RATE_MAX = 5;
const rateBucket = new Map();

function reportJson(body, status, origin) {
  const allowed = ALLOWED_ORIGINS.includes(origin);
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Vary": "Origin", ...corsHeaders(origin, allowed) },
  });
}

function clip(v, max) {
  return String(v == null ? "" : v).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "").trim().slice(0, max);
}

// Markdown 裡使用者輸入的文字：避免 @提及、#連結到其他 issue、HTML
function md(v) {
  return v.replace(/[<>]/g, "").replace(/@/g, "@​").replace(/#(\d)/g, "#​$1");
}

function num(v, min, max) {
  const n = Number(v);
  return Number.isFinite(n) && n >= min && n <= max ? n : null;
}

// 不是 /report 的請求回傳 null，交給原本的 Gemini 代理處理
async function handleReport(request, env) {
  const url = new URL(request.url);
  if (url.pathname !== "/report") return null;

  const origin = request.headers.get("Origin") || "";
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(origin, ALLOWED_ORIGINS.includes(origin)) });
  if (request.method !== "POST") return reportJson({ ok: false, error: "method" }, 405, origin);
  // 回報一定要從網站送出（比 Gemini 代理嚴格：沒帶 Origin 也擋）
  if (!ALLOWED_ORIGINS.includes(origin)) return reportJson({ ok: false, error: "origin" }, 403, origin);
  if (!env.GITHUB_TOKEN) return reportJson({ ok: false, error: "not_configured" }, 503, origin);

  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const now = Date.now();
  const hits = (rateBucket.get(ip) || []).filter((t) => now - t < RATE_WINDOW_MS);
  if (hits.length >= RATE_MAX) return reportJson({ ok: false, error: "rate_limited" }, 429, origin);

  let data;
  try {
    const raw = await request.text();
    if (raw.length > 4000) return reportJson({ ok: false, error: "too_large" }, 413, origin);
    data = JSON.parse(raw);
  } catch (e) {
    return reportJson({ ok: false, error: "bad_json" }, 400, origin);
  }

  // 蜜罐欄位：真人看不到也不會填，機器人常會填
  if (data.website) return reportJson({ ok: true }, 200, origin);

  const stopCode = clip(data.stopCode, 10).toUpperCase();
  const stopName = clip(data.stopName, 80);
  const routeNo = clip(data.routeNo, 6);
  const routeName = clip(data.routeName, 40);
  const type = REPORT_TYPES[data.type] ? data.type : "other";
  const note = clip(data.note, 500);
  if (!/^[A-Z]{0,3}\d{1,4}$/.test(stopCode) || !stopName) return reportJson({ ok: false, error: "bad_stop" }, 400, origin);
  if (type === "other" && !note) return reportJson({ ok: false, error: "need_note" }, 400, origin);

  const loc = data.location || {};
  const lat = num(loc.lat, 21, 27);
  const lng = num(loc.lng, 118, 123);
  const acc = num(loc.accuracy, 0, 100000);
  const cur = data.currentCoords || {};
  const curLat = num(cur.lat, 21, 27);
  const curLng = num(cur.lng, 118, 123);

  const lines = [
    `**站牌**：${md(stopName)}（\`${stopCode}\`）`,
    routeNo ? `**路線**：${md(routeNo)}號 ${md(routeName)}` : null,
    `**問題類型**：${REPORT_TYPES[type]}`,
    "",
    note ? `**說明**：\n> ${md(note).replace(/\n/g, "\n> ")}` : "**說明**：（未填）",
    "",
    curLat != null && curLng != null
      ? `**目前網站上的座標**：${curLat}, ${curLng}（[Google 地圖](https://www.google.com/maps/search/?api=1&query=${curLat},${curLng})）`
      : "**目前網站上的座標**：（無）",
    lat != null && lng != null
      ? `**回報者當下的位置**：${lat}, ${lng}，精度約 ${acc != null ? Math.round(acc) : "?"} 公尺（[Google 地圖](https://www.google.com/maps/search/?api=1&query=${lat},${lng})）`
      : "**回報者當下的位置**：（未提供）",
    "",
    "---",
    "_由 visitfudan.com 公車頁「回報站牌問題」自動建立_",
  ].filter((l) => l !== null);

  const gh = await fetch(`https://api.github.com/repos/${REPORT_REPO}/issues`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "fudan-ai-proxy-report",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      title: `[站牌回報] ${stopName}（${stopCode}）— ${REPORT_TYPES[type]}`,
      body: lines.join("\n"),
      labels: ["站牌回報"],
    }),
  });

  if (!gh.ok) {
    console.log("GitHub issue create failed", gh.status, await gh.text());
    return reportJson({ ok: false, error: "github_" + gh.status }, 502, origin);
  }

  hits.push(now);
  rateBucket.set(ip, hits);
  return reportJson({ ok: true }, 200, origin);
}

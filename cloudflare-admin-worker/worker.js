/**
 * Cloudflare Worker — 老師後台 API（fudan-admin）
 *
 * 老師用 Google 帳號登入「最新消息後台」，這個 Worker 負責：
 *   1. 驗證 Google ID Token（簽章、受眾、信箱已驗證）
 *   2. 確認信箱在老師名單（TEACHER_EMAILS）裡
 *   3. 用伺服器端的 GitHub 權杖讀取／寫入 news.json
 *
 * 老師不需要 GitHub 帳號，也看不到任何權杖。
 *
 * API：
 *   GET  /news  → { items, sha, email }
 *   PUT  /news  → body { items, sha }，成功回 { sha }；sha 過期回 409
 *
 * 需要的設定（Cloudflare Dashboard → Worker → Settings → Variables and Secrets）：
 *   GOOGLE_CLIENT_ID  — Google OAuth 用戶端 ID（一般變數，不是機密）
 *   TEACHER_EMAILS    — 允許登入的老師信箱，用逗號分隔，例如 a@gmail.com,b@gmail.com
 *   GITHUB_TOKEN      — Secret。fine-grained token，只給 fudan-website 的 Contents: Read and write
 *
 * 部署方式：把這個檔案貼到一個新的 Worker（名稱 fudan-admin），再設定上面三個變數。
 */

const ALLOWED_ORIGINS = [
  "https://www.visitfudan.com",
  "https://visitfudan.com",
];
const REPO = "whatasmalls-coder/fudan-website";
const FILE_PATH = "news.json";
const MAX_ITEMS = 60;
const DATE_RE = /^\d{4}\.\d{2}\.\d{2}$/;

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const allowed = ALLOWED_ORIGINS.includes(origin);

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders(origin, allowed) });
    }
    if (!allowed) {
      return json({ error: "Origin not allowed" }, 403, origin, allowed);
    }

    const url = new URL(request.url);
    if (url.pathname !== "/news") {
      return json({ error: "Not found" }, 404, origin, allowed);
    }
    if (request.method !== "GET" && request.method !== "PUT") {
      return json({ error: "Method not allowed" }, 405, origin, allowed);
    }

    // 1–2：驗證老師身分
    const auth = await authTeacher(request, env);
    if (auth.error) {
      return json({ error: auth.error }, auth.status, origin, allowed);
    }

    try {
      if (request.method === "GET") {
        const current = await readNews(env);
        return json({ items: current.items, sha: current.sha, email: auth.email }, 200, origin, allowed);
      }

      // PUT：儲存
      let body;
      try {
        body = await request.json();
      } catch (e) {
        return json({ error: "Invalid JSON" }, 400, origin, allowed);
      }
      const check = validateItems(body.items);
      if (check.error) {
        return json({ error: check.error }, 400, origin, allowed);
      }
      if (typeof body.sha !== "string" || !body.sha) {
        return json({ error: "sha is required，請重新整理頁面後再儲存" }, 400, origin, allowed);
      }

      const content = JSON.stringify(check.items, null, 2) + "\n";
      const res = await fetch(githubUrl(), {
        method: "PUT",
        headers: githubHeaders(env, true),
        body: JSON.stringify({
          message: `後台更新最新消息（${auth.email}）`,
          content: toBase64(content),
          sha: body.sha,
        }),
      });
      if (res.status === 409 || res.status === 422) {
        return json({ error: "內容剛被別人或自動更新改過，請重新整理頁面後再編輯" }, 409, origin, allowed);
      }
      if (!res.ok) {
        return json({ error: `GitHub 儲存失敗（${res.status}）` }, 502, origin, allowed);
      }
      const data = await res.json();
      return json({ sha: data.content.sha }, 200, origin, allowed);
    } catch (e) {
      return json({ error: "伺服器發生錯誤，請稍後再試" }, 500, origin, allowed);
    }
  },
};

// ---------- 身分驗證 ----------

async function authTeacher(request, env) {
  const header = request.headers.get("Authorization") || "";
  const token = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
  if (!token) return { error: "未登入", status: 401 };
  if (!env.GOOGLE_CLIENT_ID || !env.TEACHER_EMAILS || !env.GITHUB_TOKEN) {
    return { error: "後台尚未完成設定", status: 503 };
  }

  // Google 會檢查 ID Token 的簽章與有效期限，回傳裡的 aud 才能確認是給這個網站用的
  let info;
  try {
    const res = await fetch("https://oauth2.googleapis.com/tokeninfo?id_token=" + encodeURIComponent(token));
    if (!res.ok) return { error: "登入已過期，請重新登入", status: 401 };
    info = await res.json();
  } catch (e) {
    return { error: "無法驗證登入，請稍後再試", status: 502 };
  }

  if (info.aud !== env.GOOGLE_CLIENT_ID) return { error: "登入來源不正確", status: 401 };
  if (info.email_verified !== "true" && info.email_verified !== true) {
    return { error: "此 Google 信箱尚未驗證", status: 403 };
  }
  const email = String(info.email || "").toLowerCase();
  const allowList = env.TEACHER_EMAILS.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  if (!email || !allowList.includes(email)) {
    return { error: "此帳號沒有後台權限，請聯絡管理者加入名單", status: 403 };
  }
  return { email };
}

// ---------- GitHub ----------

function githubUrl() {
  return `https://api.github.com/repos/${REPO}/contents/${FILE_PATH}`;
}

function githubHeaders(env, withJson) {
  const h = {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: "application/vnd.github+json",
    "User-Agent": "fudan-admin-worker",
  };
  if (withJson) h["Content-Type"] = "application/json";
  return h;
}

async function readNews(env) {
  const res = await fetch(githubUrl(), { headers: githubHeaders(env, false) });
  if (!res.ok) throw new Error("read failed " + res.status);
  const data = await res.json();
  const text = fromBase64(data.content.replace(/\n/g, ""));
  return { items: JSON.parse(text), sha: data.sha };
}

// ---------- 資料檢查 ----------

// 只接受我們認得的欄位；其他欄位（例如 AI 摘要 summary、seoDescription）保留，但會限制長度
const TEXT_LIMITS = { tag: 12, title: 200, summary: 2000, seoDescription: 300 };

function validateItems(items) {
  if (!Array.isArray(items)) return { error: "items 必須是陣列" };
  if (items.length < 1) return { error: "至少需要保留一則消息" };
  if (items.length > MAX_ITEMS) return { error: `最多 ${MAX_ITEMS} 則` };

  const out = [];
  for (let i = 0; i < items.length; i++) {
    const it = items[i] || {};
    const n = i + 1;
    if (typeof it.date !== "string" || !DATE_RE.test(it.date)) {
      return { error: `第 ${n} 則的日期格式要是 2026.08.01` };
    }
    for (const key of ["tag", "title", "summary", "seoDescription"]) {
      if (it[key] != null && typeof it[key] !== "string") {
        return { error: `第 ${n} 則的「${key}」格式錯誤` };
      }
    }
    if (typeof it.title !== "string" || !it.title.trim()) {
      return { error: `第 ${n} 則缺少標題` };
    }
    if (typeof it.link !== "string" || !isHttps(it.link)) {
      return { error: `第 ${n} 則的連結必須是 https 網址` };
    }
    const clean = { date: it.date, tag: (it.tag || "").trim(), title: it.title.trim(), link: it.link.trim() };
    for (const key of ["summary", "seoDescription"]) {
      if (typeof it[key] === "string" && it[key].trim()) clean[key] = it[key].trim();
    }
    for (const [key, max] of Object.entries(TEXT_LIMITS)) {
      if (typeof clean[key] === "string" && clean[key].length > max) {
        return { error: `第 ${n} 則的「${key}」太長（上限 ${max} 字）` };
      }
    }
    out.push(clean);
  }
  return { items: out };
}

function isHttps(s) {
  try {
    return new URL(s).protocol === "https:";
  } catch (e) {
    return false;
  }
}

// ---------- 工具 ----------

function toBase64(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

function fromBase64(b64) {
  const bin = atob(b64);
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

function corsHeaders(origin, allowed) {
  const h = {
    "Access-Control-Allow-Methods": "GET, PUT, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
  if (allowed) h["Access-Control-Allow-Origin"] = origin;
  return h;
}

function json(obj, status, origin, allowed) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      ...corsHeaders(origin, allowed),
    },
  });
}

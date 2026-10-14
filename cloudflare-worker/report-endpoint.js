/*
 * 站牌問題回報：POST /report → 在 GitHub 開一個 Issue
 *
 * 這段程式碼要貼進 Cloudflare 上的 fudan-ai-proxy Worker（不是在 GitHub Pages 上執行）。
 * 安裝步驟見同資料夾的 README.md。
 *
 * 需要的 Secret：GITHUB_TOKEN（fine-grained token，只給 fudan-website 這個 repo 的 Issues: Read and write）
 */

const REPORT_REPO = 'whatasmalls-coder/fudan-website';
const REPORT_ALLOWED_ORIGINS = ['https://visitfudan.com', 'https://www.visitfudan.com'];
const REPORT_TYPES = {
  location: '站牌位置不對',
  info: '站名或時間有誤',
  moved: '站牌已取消或搬移',
  other: '其他',
};

// 同一個 IP 每 10 分鐘最多 5 則（存在 Worker 記憶體裡，只是擋手滑連按和簡單灌水，不是嚴格限制）
const RATE_WINDOW_MS = 10 * 60 * 1000;
const RATE_MAX = 5;
const rateBucket = new Map();

function reportCors(origin) {
  const allow = REPORT_ALLOWED_ORIGINS.includes(origin) ? origin : REPORT_ALLOWED_ORIGINS[1];
  return {
    'Access-Control-Allow-Origin': allow,
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Vary': 'Origin',
  };
}

function reportJson(body, status, origin) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', ...reportCors(origin) },
  });
}

function clip(v, max) {
  return String(v == null ? '' : v).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '').trim().slice(0, max);
}

// Markdown 裡使用者輸入的文字：避免 @提及、#連結到其他 issue、HTML
function md(v) {
  return v.replace(/[<>]/g, '').replace(/@/g, '@​').replace(/#(\d)/g, '#​$1');
}

function num(v, min, max) {
  const n = Number(v);
  return Number.isFinite(n) && n >= min && n <= max ? n : null;
}

/**
 * 在 Worker 的 fetch handler 最前面呼叫：
 *   const r = await handleReport(request, env); if (r) return r;
 * 不是 /report 的請求會回傳 null，交給原本的 Gemini 代理處理。
 */
async function handleReport(request, env) {
  const url = new URL(request.url);
  if (url.pathname !== '/report') return null;

  const origin = request.headers.get('Origin') || '';
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: reportCors(origin) });
  if (request.method !== 'POST') return reportJson({ ok: false, error: 'method' }, 405, origin);
  if (!REPORT_ALLOWED_ORIGINS.includes(origin)) return reportJson({ ok: false, error: 'origin' }, 403, origin);
  if (!env.GITHUB_TOKEN) return reportJson({ ok: false, error: 'not_configured' }, 503, origin);

  const ip = request.headers.get('CF-Connecting-IP') || 'unknown';
  const now = Date.now();
  const hits = (rateBucket.get(ip) || []).filter((t) => now - t < RATE_WINDOW_MS);
  if (hits.length >= RATE_MAX) return reportJson({ ok: false, error: 'rate_limited' }, 429, origin);

  let data;
  try {
    const raw = await request.text();
    if (raw.length > 4000) return reportJson({ ok: false, error: 'too_large' }, 413, origin);
    data = JSON.parse(raw);
  } catch (e) {
    return reportJson({ ok: false, error: 'bad_json' }, 400, origin);
  }

  // 蜜罐欄位：真人看不到也不會填，機器人常會填
  if (data.website) return reportJson({ ok: true }, 200, origin);

  const stopCode = clip(data.stopCode, 10).toUpperCase();
  const stopName = clip(data.stopName, 80);
  const routeNo = clip(data.routeNo, 6);
  const routeName = clip(data.routeName, 40);
  const type = REPORT_TYPES[data.type] ? data.type : 'other';
  const note = clip(data.note, 500);
  if (!/^[A-Z]{0,3}\d{1,4}$/.test(stopCode) || !stopName) return reportJson({ ok: false, error: 'bad_stop' }, 400, origin);
  if (type === 'other' && !note) return reportJson({ ok: false, error: 'need_note' }, 400, origin);

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
    '',
    note ? `**說明**：\n> ${md(note).replace(/\n/g, '\n> ')}` : '**說明**：（未填）',
    '',
    curLat != null
      ? `**目前網站上的座標**：${curLat}, ${curLng}（[Google 地圖](https://www.google.com/maps/search/?api=1&query=${curLat},${curLng})）`
      : '**目前網站上的座標**：（無）',
    lat != null
      ? `**回報者當下的位置**：${lat}, ${lng}，精度約 ${acc != null ? Math.round(acc) : '?'} 公尺（[Google 地圖](https://www.google.com/maps/search/?api=1&query=${lat},${lng})）`
      : '**回報者當下的位置**：（未提供）',
    '',
    '---',
    '_由 visitfudan.com 公車頁「回報站牌問題」自動建立_',
  ].filter((l) => l !== null);

  const gh = await fetch(`https://api.github.com/repos/${REPORT_REPO}/issues`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${env.GITHUB_TOKEN}`,
      'Accept': 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'fudan-ai-proxy-report',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      title: `[站牌回報] ${stopName}（${stopCode}）— ${REPORT_TYPES[type]}`,
      body: lines.join('\n'),
      labels: ['站牌回報'],
    }),
  });

  if (!gh.ok) {
    console.log('GitHub issue create failed', gh.status, await gh.text());
    return reportJson({ ok: false, error: 'github_' + gh.status }, 502, origin);
  }

  hits.push(now);
  rateBucket.set(ip, hits);
  return reportJson({ ok: true }, 200, origin);
}

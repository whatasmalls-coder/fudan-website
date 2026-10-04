/**
 * sw.js — 復旦高中網站 Service Worker
 *
 * 目標：讓「校車路線查詢」在離線或收訊不穩時依然可用（校車資料不常變動，
 * 很適合快取），同時不影響 AI 助手、公告等需要即時資料的功能正常運作。
 *
 * 快取策略：
 * 1. 首頁 / 校車頁面（HTML）：先試網路，拿到最新版本；連不上網路才用快取版本頂替
 * 2. 字型、圖示、共用 JS：網路狀況好的時候才需要重抓，用「快取優先」策略
 * 3. Google Fonts（外部 CDN，若有使用到）：先用快取立即顯示，背景偷偷更新（stale-while-revalidate）
 * 4. news.json / calendar.json：優先拿最新資料，離線時退回上次抓到的版本
 * 5. AI 相關的 API 呼叫（Cloudflare Worker、Gemini、Plausible、GitHub API）：
 *    完全不快取，一律直接放行，避免使用者收到過期或錯誤的 AI 回應
 *
 * 版本號：改動快取內容時，記得把 CACHE_NAME 的版本號往上加一，
 * 讓舊的快取被自動清掉，使用者才會拿到最新版本。
 */

const CACHE_VERSION = 'v14';
const CACHE_NAME = `fd-cache-${CACHE_VERSION}`;

const PRECACHE_URLS = [
  '/',
  '/bus-search/',
  '/calendar/',
  '/offline.html',
  '/manifest.json',
  '/favicon.ico',
  '/favicon-32.png',
  '/apple-touch-icon.png',
  '/icon-192.png',
  '/icon-512.png',
  '/icon-512-maskable.png',
  '/js/ai-shared.js',
  '/js/fuse.min.js',
  '/js/exam-countdown.js',
  '/js/routes.json',
  // 校曆、公告資料也先存起來：離線時校曆頁的搜尋／篩選、首頁的近期行事都還能用
  '/calendar.json',
  '/news.json',
  '/js/stops-coords.json',
  // fonts:precache:start
  '/fonts/NotoSansTC-400-700-all.b473b866.woff2',
  '/fonts/NotoSansTC-400-all.4d2c196b.woff2',
  '/fonts/NotoSansTC-500-all.1a5642ad.woff2',
  '/fonts/NotoSansTC-700-all.a26ddd89.woff2',
  '/fonts/NotoSerifTC-600-700-all.7983f369.woff2',
  '/fonts/NotoSerifTC-600-all.e0f2258b.woff2',
  '/fonts/NotoSerifTC-700-all.af36fcf9.woff2',
  // fonts:precache:end
];

// 這些網域的請求（AI API、分析、CMS）永遠不快取，一律直接走網路
const NEVER_CACHE_HOSTS = [
  'workers.dev',
  'generativelanguage.googleapis.com',
  'plausible.io',
  'api.github.com',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      // 個別檔案快取失敗不應該讓整個安裝失敗（例如某個字型檔一時抓不到），
      // 用 Promise.allSettled 讓能快取的先快取起來
      return Promise.allSettled(
        PRECACHE_URLS.map((url) => cache.add(url))
      );
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      )
    ).then(trimOldFonts)
  );
  self.clients.claim();
});

// 字型檔名帶雜湊，字型一更新就換新檔名；舊檔如果不刪，會一直堆在使用者手機裡。
// fonts/manifest.json 列出目前網站在用的字型檔，不在清單上的就刪掉。
// （沒有雜湊的舊檔名，例如 NotoSerifTC-900-subset.woff2，不會被動到）
const HASHED_FONT = /\/fonts\/[^/]+\.[0-9a-f]{8}\.woff2$/;
async function trimOldFonts() {
  try {
    const res = await fetch('/fonts/manifest.json', { cache: 'no-store' });
    if (!res.ok) return;
    const files = (await res.json()).files || {};   // { 檔名: {字型資訊} }
    const names = Array.isArray(files) ? files : Object.keys(files);
    const current = new Set(names.map((f) => '/fonts/' + f));
    if (!current.size) return;
    const cache = await caches.open(CACHE_NAME);
    for (const req of await cache.keys()) {
      const path = new URL(req.url).pathname;
      if (HASHED_FONT.test(path) && !current.has(path)) await cache.delete(req);
    }
  } catch (e) { /* 離線或清單讀不到：下次再清 */ }
}

function isNeverCacheRequest(url) {
  return NEVER_CACHE_HOSTS.some((host) => url.hostname.includes(host));
}

// 網路優先：先試網路，成功就更新快取；失敗才退回快取版本
// cacheKey：存進快取用的網址（頁面會去掉 ?q= 之類的參數，見下方 navigate）
async function networkFirst(request, cacheKey = request) {
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(cacheKey, response.clone());
    }
    return response;
  } catch (e) {
    const cached = await caches.match(cacheKey);
    if (cached) return cached;
    throw e;
  }
}

// 快取優先：有快取就直接用，背景不特別更新（適合幾乎不變的靜態資源）
// onNew：快取裡沒有、剛從網路抓到新檔時要做的事（字型用來清掉舊版字型檔）
async function cacheFirst(request, onNew) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response && response.ok) {
    const cache = await caches.open(CACHE_NAME);
    await cache.put(request, response.clone());
    if (onNew) onNew();
  }
  return response;
}

// 先用快取立即回應，同時在背景偷偷抓新版本存起來，下次就會是新的
async function staleWhileRevalidate(request) {
  const cached = await caches.match(request);
  const fetchPromise = fetch(request).then((response) => {
    if (response && response.ok) {
      caches.open(CACHE_NAME).then((cache) => cache.put(request, response.clone()));
    }
    return response;
  }).catch(() => cached);
  return cached || fetchPromise;
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return; // POST（例如 AI 對話）一律不攔截

  const url = new URL(request.url);

  // AI / 分析 / CMS 相關 API：完全不經過 Service Worker 處理
  if (isNeverCacheRequest(url)) return;

  // 頁面導覽（直接輸入網址或點連結進來）：網路優先，離線時退回快取
  if (request.mode === 'navigate') {
    // 網址後面的 ?q=龍潭、?cat=段考 只是給頁面程式讀的，HTML 內容都一樣，
    // 所以快取時一律存成不帶參數的網址：一頁只存一份，不會每搜一次就多存一份整頁。
    const pageKey = url.origin + url.pathname;
    // 沒網路、又沒快取過這頁時，顯示離線備用頁，而不是瀏覽器的錯誤畫面
    event.respondWith(networkFirst(request, pageKey).catch(() => caches.match('/offline.html')));
    return;
  }

  // 站內的公告資料／校曆資料：想要盡量新，但離線時仍能看到上次抓到的版本
  if (url.origin === self.location.origin &&
      (url.pathname === '/news.json' || url.pathname === '/calendar.json')) {
    event.respondWith(networkFirst(request));
    return;
  }

  // 站內字型：檔名帶內容雜湊（scripts/build_fonts.py 產生），內容一變就換檔名，
  // 同一個網址永遠是同一份檔案，所以直接快取優先就好
  if (url.origin === self.location.origin && url.pathname.startsWith('/fonts/')) {
    // 抓到新字型檔 = 網站字型更新過，順便清掉用不到的舊字型檔
    event.respondWith(cacheFirst(request, () => {
      const done = trimOldFonts();
      try { event.waitUntil(done); } catch (e) { /* 事件已結束也沒關係，清理照樣會跑 */ }
    }));
    return;
  }

  // 站內其他靜態資源（圖示、共用 JS、路線資料）：先用快取立即回應、背景抓新版，
  // 下次造訪就是新的。（以前是快取優先，改了 routes.json 或 JS 要等 CACHE_VERSION
  // 變了使用者才拿得到新版）
  if (url.origin === self.location.origin) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }

  // 外部 CDN（Google Fonts，若有使用到）：先用快取立即顯示，背景更新
  if (url.hostname.includes('fonts.googleapis.com') ||
      url.hostname.includes('fonts.gstatic.com')) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }

  // 其他不特別處理的請求，交給瀏覽器正常處理
});

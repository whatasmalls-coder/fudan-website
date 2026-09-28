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

const CACHE_VERSION = 'v9';
const CACHE_NAME = `fd-cache-${CACHE_VERSION}`;

const PRECACHE_URLS = [
  '/',
  '/bus-search/',
  '/calendar/',
  '/manifest.json',
  '/favicon.ico',
  '/favicon-32.png',
  '/favicon-512.png',
  '/apple-touch-icon.png',
  '/icon-192.png',
  '/icon-512.png',
  '/icon-512-maskable.png',
  '/js/ai-shared.js',
  '/js/fuse.min.js',
  '/js/routes.json',
  '/js/stops-coords.json',
  // fonts:precache:start
  '/fonts/NotoSansTC-400-all.9fdbf9ae.woff2',
  '/fonts/NotoSansTC-500-all.6623b2b7.woff2',
  '/fonts/NotoSansTC-700-all.b0d1db15.woff2',
  '/fonts/NotoSerifTC-600-all.206320a5.woff2',
  '/fonts/NotoSerifTC-700-all.8fc8ad42.woff2',
  // fonts:precache:end
  '/fonts/NotoSerifTC-900-subset.woff2',
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
    )
  );
  self.clients.claim();
});

function isNeverCacheRequest(url) {
  return NEVER_CACHE_HOSTS.some((host) => url.hostname.includes(host));
}

// 網路優先：先試網路，成功就更新快取；失敗才退回快取版本
async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch (e) {
    const cached = await caches.match(request);
    if (cached) return cached;
    throw e;
  }
}

// 快取優先：有快取就直接用，背景不特別更新（適合幾乎不變的靜態資源）
async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response && response.ok) {
    const cache = await caches.open(CACHE_NAME);
    cache.put(request, response.clone());
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
    event.respondWith(networkFirst(request));
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
    event.respondWith(cacheFirst(request));
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

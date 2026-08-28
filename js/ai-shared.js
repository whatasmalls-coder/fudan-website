/**
 * ai-shared.js — 首頁 AI 助手（浮動聊天框）與 /bus-search/ 頁面（AI 語意搜尋 fallback）
 * 共用的工具函式庫。
 *
 * 這兩個功能用的是不同架構（聊天框讓 Gemini 自己決定要不要呼叫搜尋工具；
 * 校車頁面是規則式搜尋失敗後才請 AI 猜關鍵字），沒辦法、也不需要完全合併成同一套邏輯，
 * 但底下這兩個工具函式在兩邊都會用到，抽成共用檔案，之後只要改一個地方，兩邊同步生效：
 *
 * 1. buildLandmarkKeywords()：從站牌名稱中抽取常見路名/地標關鍵字
 * 2. createTTLCache()：帶有效期限的 localStorage 快取，用法一致
 *
 * 使用方式：在需要的頁面 <script> 前加一行
 *   <script src="/js/ai-shared.js"></script>
 * 之後就能直接使用 window.FdAiShared.buildLandmarkKeywords(...) 等函式。
 */
(function (global) {
  'use strict';

  var LANDMARK_SUFFIX_PATTERN = /[\u4e00-\u9fa5]{2,8}(?:路|街|巷|段|社區|國小|國中|高中|大學|醫院|市場|工業區|購物中心|大樓|大道|橋|站|廟|公園|加油站|農會)/g;
  var DEFAULT_BLOCKLIST = ['公車站', '加油站', '公車', '大樓', '市場']; // 太通用、沒有辨識度的詞，排除

  /**
   * 從站牌名稱陣列中抽取常見的路名/社區/地標關鍵字，依出現頻率排序。
   * @param {Array<{name:string}>} stops - 站牌物件陣列，每個至少要有 name 欄位
   * @param {Object} [options]
   * @param {number} [options.limit=120] - 最多回傳幾個關鍵字，避免 prompt 過長
   * @param {string[]} [options.blocklist] - 要排除的通用詞，預設用內建清單
   * @returns {string[]} 依出現頻率排序的關鍵字陣列
   */
  function buildLandmarkKeywords(stops, options) {
    options = options || {};
    var limit = options.limit || 120;
    var blocklist = options.blocklist || DEFAULT_BLOCKLIST;
    var counts = {};

    (stops || []).forEach(function (stop) {
      var name = stop && stop.name;
      if (!name) return;
      var matches = name.match(LANDMARK_SUFFIX_PATTERN);
      if (!matches) return;
      matches.forEach(function (m) {
        m = m.replace(/^[(（]|[)）]$/g, '').trim();
        if (m.length < 2 || blocklist.indexOf(m) !== -1) return;
        counts[m] = (counts[m] || 0) + 1;
      });
    });

    return Object.keys(counts)
      .sort(function (a, b) { return counts[b] - counts[a]; })
      .slice(0, limit);
  }

  /**
   * 建立一個帶有效期限的 localStorage 快取工具。
   * @param {string} storageKey - localStorage 的 key 名稱
   * @param {number} maxAgeMs - 快取有效時間（毫秒）
   * @param {number} [maxEntries=100] - 最多保留幾筆，超過時清掉最舊的
   * @returns {{get:function(string):*, set:function(string,*):void}}
   */
  function createTTLCache(storageKey, maxAgeMs, maxEntries) {
    maxEntries = maxEntries || 100;

    function readAll() {
      try {
        return JSON.parse(localStorage.getItem(storageKey)) || {};
      } catch (e) {
        return {};
      }
    }
    function writeAll(cache) {
      try {
        localStorage.setItem(storageKey, JSON.stringify(cache));
      } catch (e) {
        // localStorage 滿了或被瀏覽器封鎖時，安靜失敗即可，不影響主要功能
      }
    }

    return {
      get: function (key) {
        var cache = readAll();
        var entry = cache[key];
        if (!entry) return null;
        if (Date.now() - entry.ts > maxAgeMs) {
          delete cache[key];
          writeAll(cache);
          return null;
        }
        return entry.value;
      },
      set: function (key, value) {
        var cache = readAll();
        cache[key] = { value: value, ts: Date.now() };
        var keys = Object.keys(cache);
        if (keys.length > maxEntries) {
          keys.sort(function (a, b) { return cache[a].ts - cache[b].ts; });
          delete cache[keys[0]];
        }
        writeAll(cache);
      }
    };
  }

  global.FdAiShared = {
    buildLandmarkKeywords: buildLandmarkKeywords,
    createTTLCache: createTTLCache
  };
})(window);

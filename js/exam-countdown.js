/*
 * 段考倒數卡片（首頁、校曆頁共用）
 *
 * 從 /calendar.json 找出接下來的「定期評量」和「學測」，
 * 同一個名稱、連續幾天的事件合併成一場考試，最多顯示兩場。
 *
 * 用法：頁面放一個 <div data-exam-countdown></div>，再載入這支 script（defer）。
 * 容器在畫面外時會等快捲到才去抓資料，不影響首頁一開始的速度。
 */
(function () {
  var boxes = document.querySelectorAll('[data-exam-countdown]');
  if (!boxes.length) return;

  var WD = ['日', '一', '二', '三', '四', '五', '六'];
  var MATCH = /定期評量|段考|學測|統測/;

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function ymd(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function parse(s) { return new Date(s + 'T00:00:00'); }
  function md(s) { var d = parse(s); return (d.getMonth() + 1) + '/' + d.getDate() + '（' + WD[d.getDay()] + '）'; }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  // 標題裡常見的括號備註（例如「學測(1/22~1/24)」）不用顯示
  function cleanTitle(t) { return String(t).replace(/[（(][^）)]*[）)]\s*$/, '').trim(); }

  function groupExams(events, today) {
    var list = events
      .filter(function (e) { return e && e.date && MATCH.test(e.title || ''); })
      .sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; });
    var groups = [];
    list.forEach(function (e) {
      var title = cleanTitle(e.title);
      var last = groups.length ? groups[groups.length - 1] : null;
      // 同名、且和上一天相隔不到 7 天（中間可能隔週末）就算同一場
      if (last && last.title === title && (parse(e.date) - parse(last.end)) / 864e5 <= 7) {
        last.end = e.date;
        last.days.push(e.date);
      } else {
        groups.push({ title: title, start: e.date, end: e.date, days: [e.date] });
      }
    });
    return groups.filter(function (g) { return g.end >= today; }).slice(0, 2);
  }

  function render(box, groups, today) {
    if (!groups.length) { box.hidden = true; return; }
    var html = groups.map(function (g) {
      var diff = Math.round((parse(g.start) - parse(today)) / 864e5);
      var big, unit, cls = '';
      if (g.start <= today) { big = '進行中'; unit = ''; cls = ' is-now'; }
      else if (diff === 1) { big = '明天'; unit = ''; cls = ' is-soon'; }
      else { big = String(diff); unit = '天'; if (diff <= 7) cls = ' is-soon'; }
      // 單天顯示星期；多天只顯示起訖日，手機上兩張卡片並排才放得下
      var range = g.start === g.end ? md(g.start) : md(g.start).replace(/（.）/, '') + '–' + md(g.end).replace(/（.）/, '');
      return '<a class="exam-cd-item' + cls + '" href="/calendar/?q=' + encodeURIComponent(g.title) + '">' +
        '<span class="exam-cd-label">' + (unit ? '距離' : '') + esc(g.title) + '</span>' +
        '<span class="exam-cd-num">' + (unit ? '還有 ' : '') + '<b>' + big + '</b>' + (unit ? ' ' + unit : '') + '</span>' +
        '<span class="exam-cd-date">' + range + (g.days.length > 1 ? '・' + g.days.length + ' 天' : '') + '</span>' +
        '</a>';
    }).join('');
    box.innerHTML = html;
    box.hidden = false;
  }

  var dataPromise = null;
  function load() {
    if (!dataPromise) {
      dataPromise = fetch('/calendar.json')
        .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
        .then(function (d) { return (d && d.events) || []; });
    }
    return dataPromise;
  }

  function start(box) {
    load().then(function (events) {
      var today = ymd(new Date());
      render(box, groupExams(events, today), today);
    }).catch(function () { box.hidden = true; });
  }

  Array.prototype.forEach.call(boxes, function (box) {
    box.classList.add('exam-cd');
    box.setAttribute('role', 'group');
    box.setAttribute('aria-label', '段考倒數');
    if ('IntersectionObserver' in window && box.hasAttribute('data-lazy')) {
      var io = new IntersectionObserver(function (entries) {
        if (entries[0].isIntersecting) { io.disconnect(); start(box); }
      }, { rootMargin: '1200px 0px' });
      io.observe(box);
    } else {
      start(box);
    }
  });
})();

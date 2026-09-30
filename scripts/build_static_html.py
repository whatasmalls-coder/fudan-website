"""把公車路線和校曆事件「預先寫進」網頁 HTML。

為什麼：
  bus-search 和 calendar 的內容原本都是打開網頁後才用 JavaScript 從 JSON 載入，
  Google 不一定會等程式跑完才收錄，所以搜「復旦 校車 龍潭」「復旦 段考」時
  網站很難出現。這支腳本把同樣的內容直接寫進 HTML：
    - Google 一抓就讀得到所有路線、站名、校曆事件
    - 使用者打開時也能馬上看到內容（不用等 JSON 下載完）
  頁面上的 JavaScript 載入後會照常重新產生內容（搜尋、篩選、今天標示都不變），
  所以畫面跟原本一樣。

另外也會：
  - 把校曆頁標題、說明裡的「XXX學年度第X學期」換成校曆資料對應的學期
  - 把 sitemap.xml 裡校曆頁的 <lastmod> 設成校曆資料更新日期
  - 公車頁標題、說明、統計文字裡的「N條路線、N個停靠站」跟著 routes.json 更新

寫入位置（兩段註解之間的內容會被整段換掉）：
  bus-search/index.html   <!--prerender:routes:start--> … <!--prerender:routes:end-->
  calendar/index.html     <!--prerender:calendar:start--> … <!--prerender:calendar:end-->
  index.html              <!--prerender:news:start--> … <!--prerender:news:end-->（首頁近期公告前 5 則）

用法：
  python scripts/build_static_html.py          # 重新產生
  python scripts/build_static_html.py --check  # 內容過期就 exit 1（CI 用）

只用 Python 標準函式庫。
"""
import argparse
import datetime as dt
import html
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WD = "一二三四五六日"  # date.weekday(): 週一 = 0
MONTHS = ["一月", "二月", "三月", "四月", "五月", "六月", "七月", "八月", "九月", "十月", "十一月", "十二月"]

SHARE_SVG = ('<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="18" cy="5" r="2.5"/><circle cx="6" cy="12" r="2.5"/>'
             '<circle cx="18" cy="19" r="2.5"/><path d="M8.2 10.8l7.6-4.6M8.2 13.2l7.6 4.6"/></svg>')
TOGGLE_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>'


def e(s) -> str:
    return html.escape(str(s), quote=True)


def routes_html() -> str:
    routes = json.loads((ROOT / "js/routes.json").read_text("utf-8"))
    out = []
    for r in routes:
        stops = r["stops"]
        rows = "".join(
            f'<div class="stop-row"><div class="stop-dot" aria-hidden="true"></div>'
            f'<div class="stop-code">{e(s["code"])}</div><div class="stop-time">{e(s["time"])}</div>'
            f'<div class="stop-name">{e(s["name"])}</div></div>'
            for s in stops)
        out.append(
            f'<div class="route-card" data-route-no="{e(r["no"])}" style="animation:none">'
            f'<div class="route-head"><div class="route-no">{e(r["no"])}</div>'
            f'<div class="route-info" role="button" tabindex="0" aria-expanded="false">'
            f'<div class="route-name">{e(r["name"])}</div>'
            f'<div class="route-time">首站發車 {e(r["time"])} · 共 {len(stops)} 站</div></div>'
            f'<button type="button" class="share-btn" aria-label="複製「{e(r["name"])}」的分享連結">{SHARE_SVG}</button>'
            f'<div class="route-toggle">{TOGGLE_SVG}</div></div>'
            f'<div class="stop-list"><div class="stop-list-inner">{rows}</div></div></div>')
    return "".join(out)


def calendar_html() -> str:
    data = json.loads((ROOT / "calendar.json").read_text("utf-8"))
    events = sorted(data.get("events") or [], key=lambda x: x.get("date", ""))
    by_month = {}
    for ev in events:
        try:
            d = dt.date.fromisoformat(ev["date"])
        except (KeyError, ValueError):
            continue
        by_month.setdefault((d.year, d.month), {}).setdefault(d, []).append(ev)
    out = []
    for (y, m), days in by_month.items():
        out.append(f'<section class="month-section"><h2 class="month-title">{MONTHS[m - 1]}'
                   f'<span class="month-year">{y}</span></h2>')
        for d, evs in days.items():
            weekend = " is-weekend" if d.weekday() >= 5 else ""
            items = "".join(
                f'<div class="event-item"><span class="event-tag tag-{e(x.get("category", "其他"))}">'
                f'{e(x.get("category", "其他"))}</span><span class="event-title">{e(x.get("title", ""))}</span></div>'
                for x in evs)
            out.append(f'<div class="day-row"><div class="day-date"><div class="day-num">{d.day}</div>'
                       f'<div class="day-weekday{weekend}">週{WD[d.weekday()]}</div></div>'
                       f'<div class="day-events">{items}</div></div>')
        out.append('</section>')
    return "".join(out)


def semester_label() -> str:
    """從校曆第一筆事件推算「115學年度第1學期」這種字樣（8 月以後開始 = 第 1 學期）。"""
    data = json.loads((ROOT / "calendar.json").read_text("utf-8"))
    dates = sorted(x["date"] for x in data.get("events") or [] if x.get("date"))
    if not dates:
        return ""
    d = dt.date.fromisoformat(dates[0])
    if d.month >= 8:
        return f"{d.year - 1911}學年度第1學期"
    return f"{d.year - 1912}學年度第2學期"


def sync_calendar_meta(text: str) -> str:
    """校曆頁的標題、說明、眉標裡的學期字樣跟著校曆資料更新，新學期不用手動改。"""
    label = semester_label()
    return re.sub(r"\d{3}學年度第[12]學期", label, text) if label else text


def sync_bus_counts(text: str) -> str:
    """公車頁上寫死的路線數、站數（標題、說明、統計文字）跟著 routes.json 更新。"""
    routes = json.loads((ROOT / "js/routes.json").read_text("utf-8"))
    n_routes, n_stops = len(routes), sum(len(r["stops"]) for r in routes)
    text = re.sub(r"\d+條路線、\d+(個停靠站|站時刻表)", lambda m: f"{n_routes}條路線、{n_stops}{m.group(1)}", text)
    text = re.sub(r'(id="statTotal">)\d+', lambda m: m.group(1) + str(n_routes), text)
    text = re.sub(r'(id="statStops">)\d+', lambda m: m.group(1) + str(n_stops), text)
    text = re.sub(r"共 <b>\d+</b> 條校車路線", f"共 <b>{n_routes}</b> 條校車路線", text)
    return text


def sync_sitemap() -> bool:
    """sitemap.xml 裡 /calendar/ 的 <lastmod> 設成校曆資料的更新日期。"""
    p = ROOT / "sitemap.xml"
    data = json.loads((ROOT / "calendar.json").read_text("utf-8"))
    day = str(data.get("updatedAt") or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return False
    text = p.read_text("utf-8")
    new = re.sub(r"(<loc>https://www\.visitfudan\.com/calendar/</loc>\s*<lastmod>)[^<]*(</lastmod>)",
                 lambda m: m.group(1) + day + m.group(2), text)
    if new != text:
        p.write_text(new, "utf-8")
        return True
    return False


NEWS_TAG_CLASS = {"校車": "tag-bus", "防疫": "tag-health", "防災": "tag-safety", "獎助學金": "tag-scholarship",
                  "競賽": "tag-contest", "招生": "tag-admission", "研習": "tag-workshop", "榮譽": "tag-honor",
                  "行政": "tag-admin"}
EXT_SVG = ('<svg class="ext-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3h7v7"/><path d="M21 3l-9 9"/>'
           '<path d="M19 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"/></svg>')


def news_items():
    items = json.loads((ROOT / "news.json").read_text("utf-8"))
    return items if isinstance(items, list) else []


def news_html() -> str:
    """首頁「近期公告」前 5 則，格式跟 index.html 裡的程式產生的一樣。"""
    out = []
    for n in news_items()[:5]:
        link = str(n.get("link", ""))
        if not re.match(r"https?://", link):
            link = "#"
        cls = NEWS_TAG_CLASS.get(n.get("tag", ""), "")
        out.append(f'<a class="news-item" href="{e(link)}" target="_blank" rel="noopener">'
                   f'<div class="news-date">{e(n.get("date", ""))}</div>'
                   f'<div><span class="news-tag {cls}">{e(n.get("tag", ""))}</span>'
                   f'<div class="news-title">{e(n.get("title", ""))}{EXT_SVG}</div></div>'
                   f'<div class="news-arrow">→</div></a>')
    return "".join(out)


def sync_news_key(text: str) -> str:
    """把前 5 則公告的識別字串寫到 #newsList 的 data-key，頁面程式發現一樣就不重畫。"""
    key = "\n".join(f'{n.get("link", "")}|{n.get("title", "")}' for n in news_items()[:5])
    return re.sub(r'<div class="news-list" id="newsList"(?: data-key="[^"]*")?>',
                  lambda m: f'<div class="news-list" id="newsList" data-key="{e(key)}">', text, count=1)


TARGETS = [
    ("bus-search/index.html", "routes", routes_html),
    ("calendar/index.html", "calendar", calendar_html),
    ("index.html", "news", news_html),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    stale = []
    for rel, key, fn in TARGETS:
        p = ROOT / rel
        text = p.read_text("utf-8")
        pat = re.compile(rf"(<!--prerender:{key}:start-->).*?(<!--prerender:{key}:end-->)", re.S)
        if not pat.search(text):
            print(f"{rel}: 找不到 prerender:{key} 標記")
            return 1
        new = pat.sub(lambda m: m.group(1) + fn() + m.group(2), text, count=1)
        if key == "calendar":
            new = sync_calendar_meta(new)
        elif key == "routes":
            new = sync_bus_counts(new)
        elif key == "news":
            new = sync_news_key(new)
        if new != text:
            stale.append(rel)
            if not args.check:
                p.write_text(new, "utf-8")
    if not args.check and sync_sitemap():
        print("已更新 sitemap.xml 的校曆日期")
    if stale:
        print(("需要重新產生：" if args.check else "已更新：") + "、".join(stale))
    else:
        print("預先寫入的內容已是最新 ✓")
    return 1 if (args.check and stale) else 0


if __name__ == "__main__":
    sys.exit(main())

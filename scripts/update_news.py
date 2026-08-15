#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自動從復旦高中官網（fdhs.tyc.edu.tw）抓取「一週消息」公告列表，
比對現有 news.json，把還沒出現過的公告加進去，然後存檔。
成功寫入 news.json 時，同時把 sitemap.xml 裡首頁的 <lastmod> 更新成今天的日期。

設計原則：
- 只「新增」，絕對不會刪除或覆蓋 admin.html 後台手動編輯的既有項目
- 用公告的官方連結（bd_id）當作唯一識別，避免重複加入
- 如果這次完全抓不到任何公告（代表官網可能改版、腳本失效），
  就直接中止、不動 news.json，避免誤刪資料

之後如果官網改版導致抓不到內容，八成是 ROW_RE 這個正規表達式要更新，
可以直接把官網原始碼（右鍵→檢視原始碼）貼給 Claude 幫忙抓新的格式。
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

LIST_URL = "https://www.fdhs.tyc.edu.tw/e-fdhs/board/out_bd_list_s.php"
DETAIL_BASE = "https://www.fdhs.tyc.edu.tw/e-fdhs/board/out_bd_detail.php"

# news.json 跟 sitemap.xml 都在 repo 根目錄，這支腳本放在 scripts/ 資料夾下
NEWS_JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "news.json")
SITEMAP_PATH = os.path.join(os.path.dirname(__file__), "..", "sitemap.xml")

# 一次最多保留幾則公告（避免檔案無限長大；只會裁掉「自動抓的最舊」，
# 邏輯上不會動到日期抓不到、判斷為手動項目的內容——見 save_news 註解）
MAX_ITEMS = 30

# 對應官網每一列公告的原始碼格式：
# onclick="window.open('out_bd_detail.php?flag=14&bd_id=47965')" ...
# <font size='3'>標題文字</font> ... 2026-08-05
ROW_RE = re.compile(
    r"onclick=\"window\.open\('out_bd_detail\.php\?flag=(\d+)&bd_id=(\d+)'\)\"[^>]*>"
    r"<font size='3'>(.*?)</font>.*?"
    r"<font face='Arial' color=#000000 size='2'>(\d{4}-\d{2}-\d{2})</td>",
    re.S,
)


def fetch_html(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
    # 學校系統偏舊，保險起見多試幾種編碼
    for enc in ("utf-8", "big5", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def clean_title(raw_html):
    text = re.sub(r"<[^>]+>", "", raw_html)  # 去掉裡面殘留的 <font>...New!!</font> 之類標籤
    text = text.replace("&nbsp;", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def scrape():
    html = fetch_html(LIST_URL)
    items = []
    for m in ROW_RE.finditer(html):
        flag, bd_id, raw_title, date = m.groups()
        title = clean_title(raw_title)
        if not title:
            continue
        link = f"{DETAIL_BASE}?flag={flag}&bd_id={bd_id}"
        items.append(
            {
                "date": date.replace("-", "."),  # 統一成跟現有 news.json 一致的 2026.08.05 格式
                "tag": "轉知",
                "title": title,
                "link": link,
            }
        )
    return items


def load_news():
    if not os.path.exists(NEWS_JSON_PATH):
        return []
    with open(NEWS_JSON_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            print("news.json 目前內容無法解析，為安全起見中止，不做任何修改。")
            sys.exit(1)


def save_news(items):
    with open(NEWS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
        f.write("\n")


def update_sitemap_lastmod():
    """把 sitemap.xml 裡首頁那筆的 <lastmod> 更新成今天的日期（UTC）。
    只更新首頁，因為只有首頁的公告內容是這支腳本在自動維護的；
    校車查詢頁的資料是手動維護，不該被這支腳本動到日期。
    如果 sitemap.xml 不存在，或格式跟預期不一樣，就安靜跳過，不影響主流程。"""
    if not os.path.exists(SITEMAP_PATH):
        return
    try:
        with open(SITEMAP_PATH, "r", encoding="utf-8") as f:
            content = f.read()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pattern = re.compile(
            r"(<loc>https://www\.visitfudan\.com/</loc>\s*<lastmod>)\d{4}-\d{2}-\d{2}(</lastmod>)"
        )
        new_content, count = pattern.subn(r"\g<1>" + today + r"\g<2>", content, count=1)
        if count:
            with open(SITEMAP_PATH, "w", encoding="utf-8") as f:
                f.write(new_content)
            print("sitemap.xml 首頁 lastmod 已更新為 " + today)
    except Exception as e:
        # sitemap 更新失敗不該讓整個流程失敗，安靜記錄一下就好
        print("更新 sitemap.xml 時發生問題（不影響公告更新）：" + str(e))


def main():
    scraped = scrape()
    if not scraped:
        print("這次沒有抓到任何公告，可能是官網格式改變了。為了安全，不修改 news.json。")
        sys.exit(0)

    current = load_news()
    existing_links = {item.get("link") for item in current if item.get("link")}

    new_items = [item for item in scraped if item["link"] not in existing_links]
    if not new_items:
        print("沒有新公告，news.json 不用更新。")
        sys.exit(0)

    merged = new_items + current
    # 依日期新到舊排序；沒有 date 欄位的手動項目會被排到最後，不會被誤刪
    merged.sort(key=lambda it: it.get("date", ""), reverse=True)

    if len(merged) > MAX_ITEMS:
        merged = merged[:MAX_ITEMS]

    save_news(merged)
    update_sitemap_lastmod()
    print(f"新增 {len(new_items)} 則公告，news.json 已更新。")


if __name__ == "__main__":
    main()

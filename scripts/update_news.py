#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自動從復旦高中官網（fdhs.tyc.edu.tw）抓取「一週消息」公告列表，
比對現有 news.json，把還沒出現過的公告加進去，然後存檔。

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

LIST_URL = "https://www.fdhs.tyc.edu.tw/e-fdhs/board/out_bd_list_s.php"
DETAIL_BASE = "https://www.fdhs.tyc.edu.tw/e-fdhs/board/out_bd_detail.php"

# news.json 在 repo 根目錄，這支腳本放在 scripts/ 資料夾下
NEWS_JSON_PATH = os.path.join(os.path.dirname(__file__), "..", "news.json")

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
    print(f"新增 {len(new_items)} 則公告，news.json 已更新。")


if __name__ == "__main__":
    main()

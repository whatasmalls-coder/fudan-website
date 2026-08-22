import os
import json
import hashlib
import time
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup
import anthropic

client = anthropic.Anthropic()
NEWS_PATH = "news.json"
TIMEOUT = 10

def fetch_page_text(url: str) -> str:
    """抓公告頁面純文字內容，失敗回傳空字串"""
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")
        # 移除不需要的標籤
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:4000]  # 避免過長，截斷
    except Exception as e:
        print(f"[warn] 抓取頁面失敗 {url}: {e}")
        return ""

def enrich_announcement(title: str, page_text: str) -> dict:
    content_for_prompt = page_text if page_text else title
    prompt = f"""你是校園網站編輯。請針對以下公告產生結構化資料，只回傳 JSON，不要有其他文字或說明：

標題：{title}
內容：{content_for_prompt}

回傳格式：
{{
  "summary": "50字內摘要，客觀轉述重點",
  "seoDescription": "120字內的SEO meta description"
}}"""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        print(f"[warn] AI 加工失敗，改用 fallback: {e}")
        return {
            "summary": title[:50],
            "seoDescription": title[:120]
        }

def content_hash(title: str, page_text: str) -> str:
    raw = (title + page_text[:1000]).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]

def main():
    with open(NEWS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    enriched_count = 0
    updated_count = 0

    for item in items:
        url = item.get("link")
        if not url:
            continue

        page_text = fetch_page_text(url)
        new_hash = content_hash(item["title"], page_text)

        if "summary" not in item:
            # 全新公告，第一次加工
            enriched = enrich_announcement(item["title"], page_text)
            item["summary"] = enriched["summary"]
            item["seoDescription"] = enriched["seoDescription"]
            item["contentHash"] = new_hash
            item["enrichedAt"] = datetime.now(timezone.utc).isoformat()
            item["updatedAt"] = None
            enriched_count += 1
            time.sleep(1)  # 避免對官網和API打太快

        elif item.get("contentHash") != new_hash and page_text:
            # 偵測到內容變更，重新加工
            print(f"[偵測到變更] {item['title']}")
            enriched = enrich_announcement(item["title"], page_text)
            item["summary"] = enriched["summary"]
            item["seoDescription"] = enriched["seoDescription"]
            item["contentHash"] = new_hash
            item["updatedAt"] = datetime.now(timezone.utc).isoformat()
            updated_count += 1
            time.sleep(1)

    with open(NEWS_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    print(f"完成：新加工 {enriched_count} 筆，偵測更新 {updated_count} 筆")

if __name__ == "__main__":
    main()

"""幫 news.json 的每則公告產生 AI 摘要（summary）和 SEO 說明（seoDescription）。

AI 來源（免費，不需要 Anthropic 帳號付費）：
  1. 有設定 GitHub secret GEMINI_API_KEY → 直接呼叫 Google Gemini API
  2. 沒有 → 透過網站現有的 Cloudflare Worker（fudan-ai-proxy），跟網站 AI 助手用同一個

失敗時的處理（跟以前不同）：
  以前 AI 失敗會「偷偷」用截斷的標題當摘要，而且之後永遠不會再重試。
  現在失敗就不寫入摘要，下次排程會自動重試；以前留下的假摘要（= 標題前 50 字）
  也會被認出來、逐步補成真的摘要。全部失敗時 workflow 會顯示失敗，才看得到問題。

需要：pip install requests beautifulsoup4
"""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

NEWS_PATH = "news.json"
TIMEOUT = 15
PROXY_URL = os.environ.get("AI_PROXY_URL", "https://fudan-ai-proxy.whatasmalls.workers.dev")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
# 每次最多呼叫幾次 AI。Gemini 免費額度是跟網站 AI 助手、公車 AI 搜尋共用的，
# 所以慢慢補就好（公告每 2 小時更新一次，一天也能補好幾十則），不要把額度用光。
MAX_CALLS = int(os.environ.get("ENRICH_MAX_CALLS", "4"))
PAUSE = float(os.environ.get("ENRICH_PAUSE", "13"))         # 每次呼叫間隔秒數（免費額度大約每分鐘 5 次）


def fetch_page_text(url: str) -> str:
    """抓公告頁面純文字內容，失敗回傳空字串"""
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)[:4000]
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 抓取頁面失敗 {url}: {e}")
        return ""


def ask_ai(prompt: str) -> str:
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"}}
    if GEMINI_KEY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
        resp = requests.post(url, params={"key": GEMINI_KEY}, json=body, timeout=40)
    else:
        # Worker 只接受網站來源的請求，所以帶上網站的 Origin / Referer
        resp = requests.post(PROXY_URL, json=body, timeout=40, headers={
            "Origin": "https://www.visitfudan.com",
            "Referer": "https://www.visitfudan.com/",
        })
    resp.raise_for_status()
    data = resp.json()
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts).strip()


def enrich_announcement(title: str, page_text: str):
    """回傳 {"summary", "seoDescription"}；失敗回傳 None（不要用假資料頂替）"""
    content = page_text if page_text else title
    prompt = f"""你是校園網站編輯。請針對以下公告產生結構化資料，只回傳 JSON，不要有其他文字或說明，不要用 markdown code block：

標題：{title}
內容：{content}

回傳格式：
{{"summary": "50字內摘要，客觀轉述重點，使用繁體中文", "seoDescription": "120字內的SEO meta description，使用繁體中文"}}"""
    try:
        text = ask_ai(prompt)
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
        result = json.loads(text)
        summary = str(result.get("summary", "")).strip()
        seo = str(result.get("seoDescription", "")).strip()
        if not summary:
            raise ValueError("AI 回傳沒有 summary")
        return {"summary": summary[:80], "seoDescription": (seo or summary)[:160]}
    except Exception as e:  # noqa: BLE001
        detail = ""
        resp = getattr(e, "response", None)
        if resp is not None:
            detail = f" HTTP {resp.status_code}: {resp.text[:200]}"
        print(f"[warn] AI 加工失敗（下次排程會重試）：{title[:30]}… {e}{detail}")
        # 暫時性問題（額度用完、Gemini 忙線 5xx、逾時、連線失敗）：這次先停，下次排程再試，不算壞掉
        quota = (resp is not None and (resp.status_code == 429 or resp.status_code >= 500)) or \
            isinstance(e, (requests.Timeout, requests.ConnectionError))
        if os.environ.get("GITHUB_ACTIONS"):
            # 在 GitHub Actions 的執行結果頁面顯示成黃色警告，方便查原因
            msg = f"{type(e).__name__}: {e}{detail}".replace("\n", " ")[:300]
            print(f"::warning title=AI 摘要失敗::{msg}")
        return "quota" if quota else None


def content_hash(title: str, page_text: str) -> str:
    return hashlib.sha256((title + page_text[:1000]).encode("utf-8")).hexdigest()[:16]


def is_placeholder(item: dict) -> bool:
    """以前 AI 失敗時寫入的假摘要：summary 就是標題前 50 字"""
    s = item.get("summary")
    return not s or s == item.get("title", "")[:50]


def main() -> int:
    with open(NEWS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    print(f"AI 來源：{'Gemini API（' + GEMINI_MODEL + '）' if GEMINI_KEY else 'Cloudflare Worker ' + PROXY_URL}")
    calls = ok = failed = 0
    quota_hit = False

    # 先補「還沒有真正摘要」的公告，剩下的額度才拿去更新內容有變動的舊公告
    order = sorted(items, key=lambda i: 0 if is_placeholder(i) else 1)
    for item in order:
        url = item.get("link")
        if not url:
            continue
        placeholder = is_placeholder(item)
        page_text = fetch_page_text(url)
        new_hash = content_hash(item["title"], page_text)
        changed = item.get("contentHash") != new_hash and bool(page_text) and not placeholder
        if not placeholder and not changed:
            continue
        if calls >= MAX_CALLS or quota_hit:
            continue
        if calls:
            time.sleep(PAUSE)
        calls += 1
        enriched = enrich_announcement(item["title"], page_text)
        if enriched == "quota":
            # 額度用完或 Gemini 忙線：這次就停，別再打（也留額度給網站上的 AI 功能）
            failed += 1
            quota_hit = True
            continue
        if not enriched:
            failed += 1
            continue
        ok += 1
        item["summary"] = enriched["summary"]
        item["seoDescription"] = enriched["seoDescription"]
        item["contentHash"] = new_hash
        now = datetime.now(timezone.utc).isoformat()
        if changed:
            print(f"[偵測到變更] {item['title']}")
            item["updatedAt"] = now
        else:
            item["enrichedAt"] = now
            item.setdefault("updatedAt", None)

    with open(NEWS_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

    left = sum(1 for i in items if is_placeholder(i))
    print(f"完成：成功 {ok} 筆、失敗 {failed} 筆；還沒有真正摘要的公告 {left} 筆")
    if quota_hit:
        print("Gemini 暫時無法使用（額度用完或忙線），下次排程再繼續補")
    # 有呼叫但全部失敗、而且不是額度問題 → 讓 workflow 顯示失敗，才不會像以前一樣默默壞掉
    return 1 if calls and not ok and not quota_hit else 0


if __name__ == "__main__":
    sys.exit(main())

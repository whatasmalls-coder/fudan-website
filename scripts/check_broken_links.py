import json
import time
import requests
from datetime import datetime, timezone

NEWS_PATH = "news.json"
REPORT_PATH = "link_check_report.json"
TIMEOUT = 20
TRIES = 3  # 學校網站偶爾回應很慢：逾時／連線失敗會再試，連續失敗才算失效（以前 8 秒逾時一次就回報，常常誤報）


def check_url(url: str) -> dict:
    last = {}
    for attempt in range(TRIES):
        if attempt:
            time.sleep(5)
        try:
            resp = requests.head(url, timeout=TIMEOUT, allow_redirects=True)
            if resp.status_code >= 400:
                resp = requests.get(url, timeout=TIMEOUT, allow_redirects=True)
            last = {"url": url, "status": resp.status_code, "ok": resp.status_code < 400}
            if last["ok"] or resp.status_code < 500:
                return last  # 成功，或 404 之類明確的錯誤：不用再試
        except requests.RequestException as e:
            last = {"url": url, "status": None, "ok": False, "error": f"{type(e).__name__}: {e}"[:200]}
    return last

def main():
    with open(NEWS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)  # 直接是陣列

    broken = []
    checked_count = 0

    for item in items:
        url = item.get("link")
        if not url:
            continue

        checked_count += 1
        result = check_url(url)

        if not result["ok"]:
            broken.append({
                "title": item["title"],
                "date": item.get("date"),
                "url": url,
                "status": result["status"],
                "error": result.get("error")
            })

    report = {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "totalChecked": checked_count,
        "brokenCount": len(broken),
        "broken": broken
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"檢查完成：共 {checked_count} 個連結，{len(broken)} 個失效")

    if broken:
        print("\n失效連結：")
        for b in broken:
            print(f"  [{b['title']}] {b['url']} → {b['status']}")

if __name__ == "__main__":
    main()

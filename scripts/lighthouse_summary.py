"""把 Lighthouse CI 跑出來的結果整理成幾行重點，印成 GitHub 的 notice 註記。

為什麼：完整報告放在 Google 的暫存空間，有時候打不開；這支腳本把每一頁的
分數、各項時間、最大內容元素是什麼、主要拖慢原因，直接顯示在 Actions 結果頁上，
不用下載報告就看得出是哪裡慢。

用法：python scripts/lighthouse_summary.py <.lighthouseci 資料夾>
只用 Python 標準函式庫。
"""
import json
import pathlib
import statistics
import sys


def main() -> int:
    folder = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".lighthouseci")
    runs = {}
    for f in sorted(folder.glob("lhr-*.json")):
        lhr = json.loads(f.read_text("utf-8"))
        runs.setdefault(lhr["finalDisplayedUrl"], []).append(lhr)
    if not runs:
        print(f"{folder} 裡找不到 lhr-*.json")
        return 0
    lines = []
    for url, lhrs in runs.items():
        # 取效能分數中位數的那一次來看細節
        lhrs.sort(key=lambda x: x["categories"]["performance"]["score"] or 0)
        mid = lhrs[len(lhrs) // 2]
        a = mid["audits"]
        scores = [round((x["categories"]["performance"]["score"] or 0) * 100) for x in lhrs]

        def ms(key):
            v = a.get(key, {}).get("numericValue")
            return "?" if v is None else f"{v / 1000:.1f}s"

        cls = a.get("cumulative-layout-shift", {}).get("numericValue", 0)
        tbt = a.get("total-blocking-time", {}).get("numericValue", 0)
        lcp_el = ""
        for item in (a.get("largest-contentful-paint-element", {}).get("details", {}).get("items") or []):
            for sub in (item.get("items") or [item]):
                node = sub.get("node") or {}
                if node.get("snippet"):
                    lcp_el = node["snippet"][:90]
                    break
            if lcp_el:
                break
        slow = [f'{x["title"]}（約 {x["details"]["overallSavingsMs"] / 1000:.1f}s）'
                for x in a.values()
                if (x.get("details") or {}).get("overallSavingsMs", 0) >= 150]
        path = url.replace("https://www.visitfudan.com", "") or "/"
        msg = (f"{path} 效能 {scores}（中位數那次）FCP {ms('first-contentful-paint')}、LCP {ms('largest-contentful-paint')}、"
               f"SI {ms('speed-index')}、TBT {tbt:.0f}ms、CLS {cls:.3f}"
               + (f"｜LCP 元素 {lcp_el}" if lcp_el else "")
               + (f"｜可改善：{'；'.join(slow[:4])}" if slow else ""))
        lines.append(msg)
        print(f"::notice title=Lighthouse {path}::{msg}")
    summary = pathlib.Path(__import__("os").environ.get("GITHUB_STEP_SUMMARY", "/dev/null"))
    with summary.open("a", encoding="utf-8") as fh:
        fh.write("## Lighthouse 重點\n\n" + "\n".join(f"- {x}" for x in lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

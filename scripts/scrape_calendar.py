import os
import json
import io
import sys
import traceback
from datetime import datetime, timezone

PDF_URL = "https://www.fdhs.tyc.edu.tw/schedule.pdf"
OUTPUT_PATH = "calendar.json"


def write_debug(stage: str, exc: Exception, extra: dict | None = None):
    """暫時性除錯：把失敗階段與例外訊息寫進 calendar_debug.json，
    這樣即使腳本本身失敗，我們還是能從 commit 出來的檔案看到真正原因。
    問題排除後這支函式與呼叫處都應該移除。"""
    debug = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "traceback": traceback.format_exc(),
    }
    if extra:
        debug.update(extra)
    with open("calendar_debug.json", "w", encoding="utf-8") as f:
        json.dump(debug, f, ensure_ascii=False, indent=2)


def mark_progress(checkpoint: str):
    """暫時性除錯：就算某一步是被 OS 直接砍掉（例如 C extension 底層崩潰，
    不是 Python 例外、try/except 完全抓不到），這個檔案在崩潰前的最後一次
    寫入還是會留在磁碟上，讓我們知道到底是哪一步之後才死掉的。
    問題排除後這支函式與呼叫處都應該移除。"""
    with open("calendar_progress.json", "w", encoding="utf-8") as f:
        json.dump({
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "last_checkpoint": checkpoint,
        }, f, ensure_ascii=False, indent=2)


mark_progress("before_any_import")

# requests / pdfplumber / anthropic 這幾個第三方套件的 import 本身也可能失敗
# （例如 pip 裝得起來但實際 import 時因為底層相依套件版本不合而炸掉），
# 而且是在任何 try/except 生效之前就會發生。之前 6 次執行都是「Install
# dependencies」步驟成功、但下一步在 3 秒內就失敗，時間短到不像是真的有
# 呼叫網路或 AI，更像是 import 階段就死掉了——所以這裡把這幾個 import
# 也包進 try/except，確保無論在哪個階段失敗，都能把真正的錯誤寫下來。
# 如果是 C extension 層級的崩潰（try/except 抓不到），就靠 mark_progress()
# 留在磁碟上的最後一個檢查點來定位問題。
try:
    import requests
    mark_progress("after_import_requests")
    import pdfplumber
    mark_progress("after_import_pdfplumber")
    import anthropic
    mark_progress("after_import_anthropic")
    client = anthropic.Anthropic()
    mark_progress("after_anthropic_client_init")
except Exception as e:
    write_debug("import_or_client_init", e)
    sys.exit(1)


def download_and_extract_text(url: str) -> str:
    resp = requests.get(url, timeout=20)
    mark_progress("after_requests_get")
    resp.raise_for_status()
    mark_progress(f"after_raise_for_status_len={len(resp.content)}")
    text_parts = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        mark_progress(f"after_pdfplumber_open_pages={len(pdf.pages)}")
        for i, page in enumerate(pdf.pages):
            text_parts.append(page.extract_text() or "")
            mark_progress(f"after_extract_text_page_{i}")
    return "\n".join(text_parts)


def parse_calendar_with_ai(raw_text: str) -> list:
    prompt = f"""以下是台灣某高中的校曆PDF擷取出的原始文字，格式是「週次」表格，橫向為星期日到六，直向為週次，日期數字後面接著當天的活動說明。月份切換時會用直書的中文字（例如「八\\n月」「九\\n月」）標示在該月第一天的日期數字附近。

這是民國115學年度第1學期校曆，學期時間約為西元2026年8月到2027年1月。民國年換算西元年：民國115年 = 西元2026年。

請將以下原始文字解析成結構化的行事曆事件清單，只回傳JSON陣列，不要有其他文字說明：

{raw_text}

回傳格式（每個事件一筆，同一天有多個活動就拆成多筆）：
[
  {{
    "date": "2026-08-31",
    "title": "開學日及開學典禮",
    "category": "行政"
  }}
]

分類請從這幾種挑選：考試、活動、假期、行政、社團、其他。
日期請務必換算成正確的西元年月日（YYYY-MM-DD格式），並根據文字裡月份切換的標記正確判斷每個日期數字屬於哪個月份。
如果是國定假日或補假（例如中秋節、國慶日、寒假開始），category請填「假期」。"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )
    text = response.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def main():
    print("下載並解析校曆PDF...")
    try:
        raw_text = download_and_extract_text(PDF_URL)
    except Exception as e:
        write_debug("download_and_extract_text", e)
        raise

    print("呼叫AI解析為結構化資料...")
    try:
        events = parse_calendar_with_ai(raw_text)
    except Exception as e:
        write_debug("parse_calendar_with_ai", e, {"raw_text_len": len(raw_text), "raw_text_preview": raw_text[:500]})
        raise

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "source": PDF_URL,
        "events": events
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"完成，共解析 {len(events)} 筆行事曆事件")


if __name__ == "__main__":
    main()

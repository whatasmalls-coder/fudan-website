import os
import json
import requests
import pdfplumber
import io
from datetime import datetime, timezone
import anthropic

client = anthropic.Anthropic()
PDF_URL = "https://www.fdhs.tyc.edu.tw/schedule.pdf"
OUTPUT_PATH = "calendar.json"


def download_and_extract_text(url: str) -> str:
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    text_parts = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
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
    raw_text = download_and_extract_text(PDF_URL)

    print("呼叫AI解析為結構化資料...")
    events = parse_calendar_with_ai(raw_text)

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

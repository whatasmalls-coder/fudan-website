import os
import json
import io
import sys
import traceback
from datetime import datetime, timezone

PDF_URL = "https://www.fdhs.tyc.edu.tw/schedule.pdf"
OUTPUT_PATH = "calendar.json"


def write_debug(stage: str, exc: Exception, extra: dict | None = None):
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


try:
    import requests
    import pdfplumber
except Exception as e:
    write_debug("import", e)
    sys.exit(1)


def main():
    resp = requests.get(PDF_URL, timeout=20)
    resp.raise_for_status()

    debug = {"updatedAt": datetime.now(timezone.utc).isoformat(), "mode": "TABLE_STRUCTURE_PROBE", "pages": []}

    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for pi, page in enumerate(pdf.pages):
            page_info = {
                "page_index": pi,
                "width": page.width,
                "height": page.height,
            }
            tables = page.find_tables()
            page_info["table_count"] = len(tables)
            table_dumps = []
            for ti, t in enumerate(tables):
                extracted = t.extract()
                table_dumps.append({
                    "table_index": ti,
                    "bbox": t.bbox,
                    "n_rows": len(extracted),
                    "n_cols": len(extracted[0]) if extracted else 0,
                    "rows": extracted,
                })
            page_info["tables"] = table_dumps

            # also dump a sample of words with positions (first 60) in case tables don't work well
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            page_info["word_count"] = len(words)
            page_info["sample_words"] = [
                {"text": w["text"], "x0": round(w["x0"], 1), "x1": round(w["x1"], 1),
                 "top": round(w["top"], 1), "bottom": round(w["bottom"], 1)}
                for w in words[:80]
            ]

            debug["pages"].append(page_info)

    with open("calendar_debug.json", "w", encoding="utf-8") as f:
        json.dump(debug, f, ensure_ascii=False, indent=2)

    print("已輸出表格結構探測結果到 calendar_debug.json")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        write_debug("probe_failed", e)
        raise

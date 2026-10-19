#!/usr/bin/env python3
"""
把 calendar.json 轉成 calendar.ics，讓學生把校曆加到手機行事曆（Google 日曆、iPhone 行事曆）。

- 訂閱網址：webcal://www.visitfudan.com/calendar.ics
- 每個事件都是整天事件（校曆只有日期，沒有時間）
- UID 用「日期＋標題」算出來，校曆重新抓取時同一筆事件的 UID 不變，
  訂閱的行事曆就會更新那筆，而不是重複出現
- DTSTAMP 用 calendar.json 的 updatedAt，內容沒變就產生一模一樣的檔案（--check 才比得出來）

用法：
  python scripts/build_ics.py          # 產生 calendar.ics
  python scripts/build_ics.py --check  # 只檢查 calendar.ics 是否和 calendar.json 同步
"""
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "calendar.json"
OUT = ROOT / "calendar.ics"
CAL_NAME = "復旦高中校曆"
SITE = "https://www.visitfudan.com/calendar/"


def esc(text: str) -> str:
    """RFC 5545 TEXT 跳脫"""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def fold(line: str) -> str:
    """每行最多 75 個位元組（UTF-8），超過就換行並以一個空白開頭接續；不能切在多位元組字的中間"""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        limit = 75 if not out else 74  # 接續行開頭的空白也算一個位元組
        if size + n > limit:
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def stamp(updated_at: str) -> str:
    dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def build() -> str:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    dtstamp = stamp(data["updatedAt"])
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//visitfudan.com//School Calendar//ZH-TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:" + esc(CAL_NAME),
        "X-WR-CALDESC:" + esc("復旦高中校曆（段考、放假、活動），資料來源：學校公告的校曆 PDF，每週自動更新"),
        "X-WR-TIMEZONE:Asia/Taipei",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    seen = {}
    for ev in sorted(data["events"], key=lambda e: (e["date"], e["title"])):
        d = date.fromisoformat(ev["date"])
        key = f'{ev["date"]}|{ev["title"]}'
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:  # 同一天同標題出現兩次時，UID 加序號避免衝突
            key += f"|{seen[key]}"
        uid = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20] + "@visitfudan.com"
        lines += [
            "BEGIN:VEVENT",
            "UID:" + uid,
            "DTSTAMP:" + dtstamp,
            "DTSTART;VALUE=DATE:" + d.strftime("%Y%m%d"),
            "DTEND;VALUE=DATE:" + (d + timedelta(days=1)).strftime("%Y%m%d"),
            "SUMMARY:" + esc(ev["title"]),
        ]
        if ev.get("category"):
            lines.append("CATEGORIES:" + esc(ev["category"]))
        lines += [
            "URL:" + SITE,
            "TRANSP:TRANSPARENT",  # 整天事件不要把使用者整天標成「忙碌」
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


def main():
    content = build()
    if "--check" in sys.argv:
        current = OUT.read_bytes().decode("utf-8") if OUT.exists() else ""
        if current != content:
            print("calendar.ics 和 calendar.json 不同步，請執行 python scripts/build_ics.py")
            sys.exit(1)
        print("calendar.ics 已同步")
        return
    OUT.write_bytes(content.encode("utf-8"))
    print(f"已產生 {OUT.name}（{content.count('BEGIN:VEVENT')} 個事件）")


if __name__ == "__main__":
    main()

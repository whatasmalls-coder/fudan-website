"""從 calendar.json 產生 calendar.ics（iCalendar 訂閱檔）。

讓學生可以把校曆訂閱到手機內建行事曆 / Google 日曆：
  webcal://www.visitfudan.com/calendar.ics
訂閱後，校曆每週自動更新時，手機上的行事曆也會跟著更新。

全部都是「整天」事件（校曆只有日期、沒有時間）。
UID 用「日期＋標題」算出來，同一個事件每次產生都一樣，行事曆 App 才不會重複新增。

用法：python scripts/build_ics.py   （scrape-calendar workflow 會在更新校曆後自動跑）
只用 Python 標準函式庫，不需要安裝任何套件。
"""
import datetime as dt
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "calendar.json"
OUT = ROOT / "calendar.ics"


def esc(text: str) -> str:
    """iCalendar TEXT 跳脫：反斜線、分號、逗號、換行。"""
    return (str(text).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n"))


def fold(line: str) -> str:
    """每行最多 75 bytes（UTF-8），超過就折行（下一行開頭加一個空白）。"""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 75:
            out.append(cur.decode("utf-8"))
            cur = b" " + b
        else:
            cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n".join(out)


def main() -> None:
    data = json.loads(SRC.read_text("utf-8"))
    events = data.get("events") or []
    updated = data.get("updatedAt") or ""
    try:
        stamp = dt.datetime.fromisoformat(updated.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except ValueError:
        stamp = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    dtstamp = stamp.strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//visitfudan.com//Fudan Senior High School Calendar//ZH-TW",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:復旦高中校曆",
        "X-WR-CALDESC:" + esc("復旦高中學期行事曆（依校方公告整理，如有異動以總務處公告為準）"),
        "X-WR-TIMEZONE:Asia/Taipei",
        "REFRESH-INTERVAL;VALUE=DURATION:P1D",
        "X-PUBLISHED-TTL:P1D",
    ]
    seen = set()
    for e in sorted(events, key=lambda e: (e.get("date", ""), e.get("title", ""))):
        date, title, cat = e.get("date"), e.get("title"), e.get("category") or "其他"
        if not date or not title:
            continue
        try:
            d = dt.date.fromisoformat(date)
        except ValueError:
            continue
        uid = hashlib.sha1(f"{date}|{title}".encode("utf-8")).hexdigest()[:16]
        if uid in seen:
            continue
        seen.add(uid)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}@visitfudan.com",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART;VALUE=DATE:{d:%Y%m%d}",
            f"DTEND;VALUE=DATE:{d + dt.timedelta(days=1):%Y%m%d}",
            "SUMMARY:" + esc(title if cat == "其他" else f"【{cat}】{title}"),
            "CATEGORIES:" + esc(cat),
            "TRANSP:TRANSPARENT",
            "URL:https://www.visitfudan.com/calendar/",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    OUT.write_text("\r\n".join(fold(l) for l in lines) + "\r\n", "utf-8", newline="")
    print(f"calendar.ics：{len(seen)} 個事件")


if __name__ == "__main__":
    main()

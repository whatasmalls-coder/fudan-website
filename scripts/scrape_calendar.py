"""
scrape_calendar.py —— 把學校官網的校曆 PDF 解析成 calendar.json

原本這支是丟給 AI 解析，但需要付費的 ANTHROPIC_API_KEY，使用者不想開通計費，
所以改成純規則解析：用 pdfplumber 的表格擷取功能（find_tables）把 PDF 讀成
正常的「列 x 欄」表格（週次 x 星期），再用規則把每個格子拆成日期＋事件標題。

完全不用呼叫任何外部 AI API，免費、也不會因為額度或計費問題而失敗。

已知的表格結構（用真實 PDF 驗證過）：
- 每一列 = 一週，第 0 欄是週次標籤（可忽略）
- 第 1、2 欄合起來是「日」（星期日），因為內容較多時 PDF 會把它切成兩個視覺欄
- 第 3~8 欄依序是「一、二、三、四、五、六」

格子內容裡常常會混進直書的月份標籤字元（例如「八」「月」各自佔一行），
這是 PDF 版面上「八月」兩個字直著排、但擷取文字時被拆到不同行造成的，
不是真正的行事曆內容，需要濾掉。
"""
import os
import re
import io
import sys
import json
import traceback
from datetime import datetime, timezone

PDF_URL = "https://www.fdhs.tyc.edu.tw/schedule.pdf"
OUTPUT_PATH = "calendar.json"

# 直書月份標籤會被拆成的零碎字元／字串，出現在格子內容中要濾掉
MONTH_FRAGMENTS = ["十二", "十一", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "月"]

WEEKDAY_LABELS = ["日", "一", "二", "三", "四", "五", "六"]

# 這些要「整句完全等於」才算假期，用包含比對的話會誤判
# 「開學日及開學典禮」「教師節活動」這種其實不是放假的日子
HOLIDAY_EXACT_NAMES = [
    "寒假開始", "暑假開始", "春假", "國慶日", "光復節", "行憲紀念日", "元旦", "教師節",
    "中秋節", "端午節", "清明節", "兒童節", "和平紀念日", "除夕", "補假",
]

CATEGORY_RULES = [
    ("考試", ["考試", "模擬考", "複習考", "定期評量", "學力檢測", "英聽測驗", "大考中心",
             "會考", "統測", "學測", "筆試", "測驗"]),
    ("活動", ["活動", "典禮", "比賽", "競賽", "講座", "週會", "成年禮", "親職", "大隊接力",
             "籃球", "拔河", "演唱會", "園遊會", "運動會", "畢業", "大會", "來訪交流"]),
    ("社團", ["社團", "幹部", "班聯會", "學生議會", "畢聯會"]),
    ("行政", ["公告", "會議", "注意事項", "榮譽", "銷過", "輔導", "健檢", "檔案", "認證",
             "抽查", "疫苗", "宣導", "入學", "招生", "自習", "視力量測", "身高體重"]),
]


def write_debug(stage: str, exc: Exception, extra: dict | None = None):
    """失敗時把階段與例外訊息寫進 calendar_debug.json 並跟著 commit 出去，
    這樣下次又失敗時不用重新排查半天，直接看這個檔案就知道卡在哪一步、
    錯誤訊息是什麼。"""
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


def download_pdf(url: str) -> bytes:
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=45)
            resp.raise_for_status()
            return resp.content
        except Exception as e:
            last_err = e
    raise last_err


def detect_semester_start(full_text: str) -> tuple[int, int]:
    """從 PDF 文字裡的「115學年度第1學期」「製表：2026/8/18」抓出學期開始的西元年月，
    這樣每年換新校曆都不用改程式碼裡的年份。
    民國學年度 + 1911 = 西元年；第1學期(上學期)從西元年8月開始，
    第2學期(下學期)從隔年2月開始。"""
    m_term = re.search(r"(\d+)\s*學年度第\s*([12])\s*學期", full_text)
    if not m_term:
        raise ValueError("找不到「XX學年度第X學期」文字，無法判斷學期年份")
    roc_year = int(m_term.group(1))
    semester = int(m_term.group(2))
    ad_year = roc_year + 1911
    if semester == 1:
        return ad_year, 8
    else:
        return ad_year + 1, 2


def strip_month_fragments(text: str) -> str:
    """濾掉直書月份標籤造成的零碎行（例如整行只有「八」或「月」），
    並把黏在行首的「月 」去掉（例如「月 力量測(健康中心)」）。"""
    lines = text.split("\n")
    kept = []
    for line in lines:
        stripped = line.strip()
        if stripped in MONTH_FRAGMENTS:
            continue
        if stripped.startswith("月") and len(stripped) > 1:
            stripped = stripped[1:].lstrip()
        if stripped:
            kept.append(stripped)
    return "\n".join(kept)


DATE_FRAGMENT_RE = re.compile(r"^[\d/～~；;）)]+$")


def merge_wrapped_lines(lines: list) -> list:
    """PDF 裡一個事件的說明文字太長時會自動換行，變成表格裡的好幾行，
    這裡把「看起來像是接續前一行、不是新事件」的行黏回去：
    - 前一行還有沒關起來的括號（例如「(第1梯次」還沒配對到「)」）
    - 這一行本身就是一段日期／括號的收尾（例如「9/2~9/3)」「(初賽)」）
    - 這一行很短又沒有起始標記（【】或數字開頭），大概是被硬拆成兩半的詞
      （例如「宣導」被拆成「宣」「導」兩行）
    判斷不到的就當作獨立事件，這是規則式解析先天的極限，
    比 AI 理解語意差一點，但完全免費、不會因為額度問題而整個失敗。"""
    merged = []
    for line in lines:
        if not merged:
            merged.append(line)
            continue
        prev = merged[-1]
        prev_open = prev.count("(") + prev.count("（") - prev.count(")") - prev.count("）")
        looks_like_continuation = (
            prev_open > 0
            or line.startswith(("(", "（"))
            or bool(DATE_FRAGMENT_RE.match(line))
            or (len(line) <= 2 and not line.startswith("【") and not line[0].isdigit())
        )
        if looks_like_continuation:
            merged[-1] = prev + line
        else:
            merged.append(line)
    return merged


DAY_CELL_RE = re.compile(r"^[O●\s]*(\d{1,2})\s*(.*)$", re.DOTALL)


def parse_day_cell(raw_cell: str):
    """把一個格子的文字拆成 (day_number, [event_title, ...])。
    格子開頭可能有一個上課日標記符號（O 或 ●），接著是日期數字，
    再接著是當天的事件說明（可能有好幾行，每行算一個事件）。
    如果格子裡完全沒有日期數字（理論上不該發生），回傳 None。"""
    if raw_cell is None:
        return None
    cleaned = strip_month_fragments(raw_cell)
    if not cleaned:
        return None
    m = DAY_CELL_RE.match(cleaned)
    if not m:
        return None
    day = int(m.group(1))
    rest = m.group(2).strip()
    if not rest:
        return day, []
    raw_lines = [line.strip() for line in rest.split("\n") if line.strip()]
    titles = merge_wrapped_lines(raw_lines)
    return day, titles


def classify(title: str) -> str:
    if title.strip() in HOLIDAY_EXACT_NAMES:
        return "假期"
    for category, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw in title:
                return category
    return "其他"


def parse_calendar_table(rows: list, start_year: int, start_month: int) -> list:
    header = rows[0]
    # 表頭應該是 ['週\n次','日',None,'一','二','三','四','五','六']，
    # 確認一下欄位順序沒有跑掉，跑掉的話直接報錯讓 write_debug 記下來，
    # 不要硬解析產生錯誤資料。
    header_days = [c.strip() if c else None for c in header[1:]]
    expected = ["日", None, "一", "二", "三", "四", "五", "六"]
    if header_days != expected:
        raise ValueError(f"表頭欄位跟預期不一樣，可能是 PDF 版面改了：{header_days}")

    events = []
    year, month = start_year, start_month
    prev_day = None

    for row in rows[1:]:
        if row is None or len(row) < 9:
            continue
        # 欄位 1+2 = 日（星期日可能被切成兩個視覺欄），欄位 3~8 = 一~六
        sunday_raw = "\n".join(x for x in [row[1], row[2]] if x)
        week_cells = [sunday_raw] + [row[i] for i in range(3, 9)]

        for cell_raw in week_cells:
            parsed = parse_day_cell(cell_raw)
            if parsed is None:
                continue
            day, titles = parsed

            # 日期數字比前一天小，代表跨月了（例如 31 之後接 1）
            if prev_day is not None and day < prev_day:
                month += 1
                if month > 12:
                    month = 1
                    year += 1
            prev_day = day

            try:
                date_str = f"{year:04d}-{month:02d}-{day:02d}"
                datetime(year, month, day)  # 驗證日期真的存在
            except ValueError:
                # 日期兜不出來就跳過這一格，並繼續處理剩下的資料，
                # 不要讓一個格子的異常把整份校曆都解析失敗
                continue

            for title in titles:
                events.append({
                    "date": date_str,
                    "title": title,
                    "category": classify(title),
                })

    return events


def main():
    print("下載校曆 PDF...")
    pdf_bytes = download_pdf(PDF_URL)

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page = pdf.pages[0]
        full_text = page.extract_text() or ""
        tables = page.find_tables()
        if not tables:
            raise ValueError("PDF 裡沒有偵測到表格結構")
        rows = tables[0].extract()

    print("判斷學期起始年月...")
    start_year, start_month = detect_semester_start(full_text)
    print(f"學期起始：{start_year}-{start_month:02d}")

    print("解析表格為結構化行事曆事件...")
    events = parse_calendar_table(rows, start_year, start_month)

    previous = []
    if os.path.exists(OUTPUT_PATH):
        try:
            with open(OUTPUT_PATH, encoding="utf-8") as f:
                previous = json.load(f).get("events") or []
        except (ValueError, OSError):
            previous = []

    # 內容完全沒變：不要重寫檔案。否則每週都會多一個只改了 updatedAt 的 commit，
    # sitemap 的更新日期也會跟著變，等於告訴 Google「校曆改了」但其實沒有。
    if events == previous:
        print(f"校曆內容沒有變動（{len(events)} 筆），不更新檔案")
        if os.path.exists("calendar_debug.json"):
            os.remove("calendar_debug.json")
        return

    # 防呆：PDF 版面改了、解析結果明顯不對時，寧可保留舊資料也不要把網站校曆變成空的。
    # （丟出例外 → 寫入 calendar_debug.json、workflow 顯示失敗，看得到要修）
    MIN_EVENTS = 30
    if len(events) < MIN_EVENTS:
        raise ValueError(f"只解析到 {len(events)} 筆事件（少於 {MIN_EVENTS} 筆），PDF 格式可能改了，保留原本的校曆")
    if previous:
        old_range = (min(e["date"] for e in previous), max(e["date"] for e in previous))
        new_range = (min(e["date"] for e in events), max(e["date"] for e in events))
        same_term = new_range[0] <= old_range[1] and old_range[0] <= new_range[1]  # 日期範圍有重疊 = 同一學期
        if same_term and len(events) < len(previous) * 0.5:
            raise ValueError(f"同一學期的事件從 {len(previous)} 筆掉到 {len(events)} 筆，解析可能出錯，保留原本的校曆")

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "source": PDF_URL,
        "events": events,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"完成，共解析 {len(events)} 筆行事曆事件")

    # 這次成功了，之前失敗留下的除錯檔案就沒有用了，清掉避免一直留在 repo 裡
    if os.path.exists("calendar_debug.json"):
        os.remove("calendar_debug.json")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        write_debug("main", e)
        raise

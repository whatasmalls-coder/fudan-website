"""重新產生 /fonts/ 底下的 Noto Sans/Serif TC 子集字型。

為什麼需要：
  字型只收錄網站「實際用到的字」來縮小檔案，但 calendar.json / news.json
  會自動更新，新公告、新校曆一出現沒收錄過的字（例如「影、宣、典、佈」），
  瀏覽器就會改用系統字型（微軟正黑體）顯示那幾個字，看起來就大小粗細不一。

用法：
  python scripts/build_fonts.py            # 檢查缺字，有缺才重新產生（CI 用這個）
  python scripts/build_fonts.py --check    # 只檢查，有缺字就 exit 1
  python scripts/build_fonts.py --force    # 不管有沒有缺字都重新產生

字集 = 現有字型已收錄的字 ∪ 所有 HTML / JSON / JS 裡出現的字
（只增不減，所以舊內容用過的字永遠不會掉）。

原始字型（Google Fonts 的可變字重版本，約 28MB）第一次需要時才下載，
存在 .font-src/（已加入 .gitignore，不會進 repo）。

需要：pip install fonttools brotli
"""
import argparse
import pathlib
import sys
import urllib.request

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "fonts"
SRC_DIR = ROOT / ".font-src"

SOURCES = {
    "sans": ("NotoSansTC[wght].ttf",
             "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf"),
    "serif": ("NotoSerifTC[wght].ttf",
              "https://raw.githubusercontent.com/google/fonts/main/ofl/notoseriftc/NotoSerifTC%5Bwght%5D.ttf"),
}

# 輸出檔名 -> (來源, 字重)
# NotoSerifTC-900-subset.woff2 是首頁大標題專用、字固定且有 unicode-range，不在這裡處理。
TARGETS = {
    "NotoSansTC-400.woff2": ("sans", 400),
    "NotoSansTC-500.woff2": ("sans", 500),
    "NotoSansTC-700.woff2": ("sans", 700),
    "NotoSerifTC-600.woff2": ("serif", 600),
    "NotoSerifTC-700.woff2": ("serif", 700),
}

# 網站內容來源：所有會顯示在頁面上的文字
CONTENT_GLOBS = ["**/*.html", "*.json", "js/*.json", "js/*.js"]
SKIP_DIRS = {".git", ".font-src", "node_modules", "fonts"}

# 基本字元：ASCII、全形標點等，確保一定存在
BASE_TEXT = "".join(chr(c) for c in range(0x20, 0x7F)) + \
    "，。、；：？！「」『』（）【】《》〈〉～…—─・·‧＋－＝／％＃＆＊＠０１２３４５６７８９"


def is_cjk(ch: str) -> bool:
    """中日韓漢字、全形標點。emoji 等 Noto TC 本來就沒有的字不算。"""
    o = ord(ch)
    return (0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF
            or 0xFF00 <= o <= 0xFFEF or 0x20000 <= o <= 0x2FFFF)


def collect_site_text() -> str:
    parts = [BASE_TEXT]
    seen = set()
    for pattern in CONTENT_GLOBS:
        for p in ROOT.glob(pattern):
            if p in seen or any(d in SKIP_DIRS for d in p.relative_to(ROOT).parts):
                continue
            seen.add(p)
            parts.append(p.read_text("utf-8", errors="ignore"))
    return "".join(parts)


def missing_chars(site_text: str) -> dict:
    """回傳 {字型檔: 缺的字}，只算 CJK 範圍（ASCII 缺了系統字型差異不明顯）。"""
    wanted = {c for c in site_text if is_cjk(c)}
    result = {}
    for name in TARGETS:
        path = FONT_DIR / name
        if not path.exists():
            result[name] = wanted
            continue
        cmap = TTFont(path).getBestCmap()
        miss = {c for c in wanted if ord(c) not in cmap}
        if miss:
            result[name] = miss
    return result


def ensure_source(key: str) -> pathlib.Path:
    filename, url = SOURCES[key]
    path = SRC_DIR / filename
    if not path.exists():
        SRC_DIR.mkdir(exist_ok=True)
        print(f"下載原始字型 {filename} …")
        urllib.request.urlretrieve(url, path)
    return path


def build(site_text: str) -> None:
    for name, (src_key, weight) in TARGETS.items():
        out = FONT_DIR / name
        codepoints = {ord(c) for c in site_text}
        if out.exists():  # 只增不減
            codepoints |= set(TTFont(out).getBestCmap())

        font = TTFont(ensure_source(src_key))
        # 先 subset 再 instance，比對整個 CJK 字型做 instance 快很多
        opts = subset.Options()
        opts.flavor = None
        opts.hinting = False
        opts.desubroutinize = True
        opts.name_IDs = ["*"]
        opts.notdef_outline = True
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=codepoints)
        sub.subset(font)

        font = instancer.instantiateVariableFont(font, {"wght": weight}, updateFontNames=False)
        font.flavor = "woff2"
        before = out.stat().st_size if out.exists() else 0
        font.save(out)
        print(f"{name}: {len(font.getBestCmap())} 字，{before // 1024}KB → {out.stat().st_size // 1024}KB")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只檢查缺字，不重新產生")
    ap.add_argument("--force", action="store_true", help="無論如何都重新產生")
    args = ap.parse_args()

    site_text = collect_site_text()
    miss = missing_chars(site_text)

    if miss:
        all_miss = sorted(set().union(*miss.values()))
        print(f"字型缺 {len(all_miss)} 字：{''.join(all_miss)}")
    else:
        print("字型覆蓋完整 ✓")

    if args.check:
        return 1 if miss else 0
    if miss or args.force:
        build(site_text)
        left = missing_chars(collect_site_text())
        if left:
            print("重新產生後仍缺字（原始字型本身沒有這些字）：",
                  "".join(sorted(set().union(*left.values()))))
    return 0


if __name__ == "__main__":
    sys.exit(main())

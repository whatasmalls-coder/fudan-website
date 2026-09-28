"""重新產生 /fonts/ 底下的 Noto Sans/Serif TC 子集字型（依頁面分片）。

為什麼要分片：
  字型只收錄網站「實際用到的字」，但公車站名、路線、校曆、公告全部塞進同一個檔，
  每個字重漲到 180–240KB，首頁、校曆一打開就要下載約 1MB 字型。
  現在每個字重拆成幾片，用 CSS unicode-range 告訴瀏覽器「哪些字在哪一片」，
  瀏覽器只會下載畫面上真的出現的字所在的那幾片：

    依「哪幾個頁面會用到這個字」分組，例如：
    all       三個主要頁面都會用到（＋ASCII、常用標點）
    home-cal  首頁和校曆都有、公車頁沒有
    cal       只有校曆用到（校曆 JSON 裡的字）
    bus       只有公車查詢用到（站名、路線）
    other     其他：404、後台、AI 對話框，以及以前收錄過、現在沒用到的字（只增不減）
  每一頁只會下載「有包含自己」的那幾片，例如校曆頁只抓 all、home-cal、cal-bus、cal。

  檔名帶內容雜湊（例如 NotoSansTC-400-cal.1a2b3c4d.woff2），字一變就換網址，
  不會有瀏覽器或 Service Worker 快取到舊字型、新字缺字的問題。

這支腳本會同時更新：
  - fonts/ 底下的字型檔（刪掉不再使用的舊檔）
  - 各頁面 HTML 裡 /*fonts:start*/ … /*fonts:end*/ 之間的 @font-face
  - 各頁面 HTML 裡 <!--fonts:preload:start--> … <!--fonts:preload:end--> 之間的 preload
  - sw.js 裡 // fonts:precache:start … // fonts:precache:end 之間的預快取清單
所以自動化 workflow commit 時要一起 add 這些 HTML 和 sw.js。

用法：
  python scripts/build_fonts.py            # 字集或分片有變才重新產生（CI 用這個）
  python scripts/build_fonts.py --check    # 只檢查，需要重新產生就 exit 1
  python scripts/build_fonts.py --force    # 不管有沒有變都重新產生

原始字型（Google Fonts 的可變字重版本，約 28MB）第一次需要時才下載，
存在 .font-src/（已加入 .gitignore，不會進 repo）。

需要：pip install fonttools brotli
"""
import argparse
import hashlib
import io
import json
import pathlib
import re
import sys
import urllib.request

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "fonts"
SRC_DIR = ROOT / ".font-src"
MANIFEST = FONT_DIR / "manifest.json"

SOURCES = {
    "sans": ("NotoSansTC[wght].ttf",
             "https://raw.githubusercontent.com/google/fonts/main/ofl/notosanstc/NotoSansTC%5Bwght%5D.ttf"),
    "serif": ("NotoSerifTC[wght].ttf",
              "https://raw.githubusercontent.com/google/fonts/main/ofl/notoseriftc/NotoSerifTC%5Bwght%5D.ttf"),
}
FAMILY = {"sans": ("Noto Sans TC", "NotoSansTC"), "serif": ("Noto Serif TC", "NotoSerifTC")}

# 要產生的字重。NotoSerifTC-900-subset.woff2 是首頁大標題專用、字固定且手寫
# unicode-range，不在這裡處理。
WEIGHTS = [("sans", 400), ("sans", 500), ("sans", 700), ("serif", 600), ("serif", 700)]

PAGE_KEYS = ["home", "cal", "bus"]
SLICES = ["all", "home-cal", "home-bus", "cal-bus", "home", "cal", "bus", "other"]


def slices_of(page):
    """某頁會用到的分片（沒有指定頁面的 404／後台，只給 all，其他字由 other 補）。"""
    return ["all"] + [s for s in SLICES if page and page in s.split("-")]

# 每個分片的內容來源。一個字如果出現在兩個以上頁面 → shared。
PAGE_SOURCES = {
    "home": ["index.html", "news.json", "calendar.json", "js/exam-countdown.js", "today/index.html"],  # 首頁「近期行事」、今日頁都會顯示校曆和公告
    "cal": ["calendar/index.html", "calendar.json", "js/exam-countdown.js"],
    "bus": ["bus-search/index.html", "js/routes.json", "js/stops-coords.json"],
}
# 不屬於特定頁面、但網站上會出現的字 → other（除非也在某頁出現）
OTHER_SOURCES = ["404.html", "admin.html", "bus-search.html", "js/ai-shared.js"]

# 各頁面：宣告哪些字重（沿用頁面原本就有的），以及要 preload 哪幾個 (字體, 字重)。
# preload 會預載該字重、這一頁會用到的所有分片。
PAGES = {
    "index.html": {"slice": "home", "weights": WEIGHTS, "preload": [("sans", 400)]},
    "calendar/index.html": {"slice": "cal",
                            "weights": [("sans", 400), ("sans", 500), ("sans", 700), ("serif", 700)],
                            "preload": [("serif", 700), ("sans", 400), ("sans", 700)]},
    "bus-search/index.html": {"slice": "bus", "weights": WEIGHTS,
                              "preload": [("sans", 400), ("sans", 700)]},
    "today/index.html": {"slice": "home",
                         "weights": [("sans", 400), ("sans", 500), ("sans", 700), ("serif", 700)],
                         "preload": [("serif", 700), ("sans", 400), ("sans", 700)]},
    "404.html": {"slice": None, "weights": [("sans", 400), ("sans", 700)], "preload": []},
    "admin.html": {"slice": None, "weights": [("sans", 400), ("sans", 500), ("sans", 700)], "preload": []},
}

# 基本字元：ASCII、全形標點等，一定放在 all
BASE_TEXT = "".join(chr(c) for c in range(0x20, 0x7F)) + \
    "，。、；：？！「」『』（）【】《》〈〉～…—─・·‧＋－＝／％＃＆＊＠０１２３４５６７８９←→↑↓"


def is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x2E80 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF
            or 0xFF00 <= o <= 0xFFEF or 0x20000 <= o <= 0x2FFFF)


_COMMENT_RE = re.compile(r"<!--(?!fonts:).*?-->|/\*.*?\*/|^[ \t]*//[^\n]*", re.S | re.M)


def read(rel: str) -> str:
    """讀檔，並拿掉 HTML / CSS / JS 註解（註解不會顯示在畫面上，不需要收錄那些字，
    也避免只是改個註解就要重新產生字型）。"""
    p = ROOT / rel
    if not p.exists():
        return ""
    text = p.read_text("utf-8", errors="ignore")
    if rel == "news.json":
        # 頁面上只會顯示公告的標題、分類、日期；AI 摘要等欄位不會出現在畫面上，不用收錄那些字
        try:
            items = json.loads(text)
            return "\n".join(f"{n.get('title', '')} {n.get('tag', '')} {n.get('date', '')}" for n in items)
        except (ValueError, AttributeError):
            return text
    return _COMMENT_RE.sub("", text) if p.suffix in (".html", ".js") else text


def cjk_set(text: str) -> set:
    """頁面上會出現的非 ASCII 字（中文、全形標點、©、– 之類）。
    字型本身沒有的字（例如 emoji）切片時會自動略過，不影響。"""
    return {ord(c) for c in text if ord(c) > 0x7F and not c.isspace() and not 0xD800 <= ord(c) <= 0xDFFF}


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text("utf-8"))
    return {}


def previously_covered() -> set:
    """舊版字型收錄過的字（只增不減），包含分片前的單一檔。"""
    cps = set()
    man = load_manifest()
    for info in man.get("files", {}).values():
        cps |= set(info["codepoints"])
    for p in FONT_DIR.glob("NotoS*TC-[0-9][0-9][0-9].woff2"):  # 分片前的舊檔
        cps |= {c for c in TTFont(p).getBestCmap() if is_cjk(chr(c))}
    return cps


def covers(slice_name: str) -> set:
    """某一片會被哪些頁面預期用到（all＝三個主要頁面，other＝不屬於任何頁面）。"""
    if slice_name == "all":
        return set(PAGE_KEYS)
    if slice_name == "other":
        return set()
    return set(slice_name.split("-"))


def assign_slices(fresh: bool = False) -> dict:
    """回傳 {slice: set(codepoints)}。

    分片要「穩定」：公告每兩小時更新、舊公告會被擠掉，如果每次都照目前內容
    重新分組，字就會在各片之間搬來搬去，每次都要重新產生字型、repo 一直長大。
    所以一個字一旦分進某一片就留在那裡（只增不減），只有在
      - 出現新字，或
      - 某頁開始用到一個字、但它所在的那片不是給這頁的（例如公車站名出現在首頁公告）
    時才會重新分配那個字。--force 會完全照目前內容重新分組（清掉累積的舊字）。
    """
    page_sets = {k: set().union(*(cjk_set(read(r)) for r in v)) for k, v in PAGE_SOURCES.items()}
    base = {ord(c) for c in BASE_TEXT}
    prev = {}
    if not fresh:
        for sl, cps in load_manifest().get("slices", {}).items():
            if sl in SLICES:
                for c in cps:
                    prev[c] = sl
    result = {s: set() for s in SLICES}
    for c in base:
        result["all"].add(c)
    for c in set().union(*page_sets.values()) - base:
        who = {k for k in PAGE_KEYS if c in page_sets[k]}
        if c in prev and who <= covers(prev[c]):
            result[prev[c]].add(c)
        else:
            result["all" if len(who) == 3 else "-".join(k for k in PAGE_KEYS if k in who)].add(c)
    used = set().union(*result.values())
    # 目前沒有頁面用到、但以前收錄過的字：留在原本那片（不搬動，避免重新產生）
    for c, sl in prev.items():
        if c not in used:
            result[sl].add(c)
    used = set().union(*result.values())
    other = set().union(*(cjk_set(read(r)) for r in OTHER_SOURCES))
    if fresh or not prev:
        other |= previously_covered()
    result["other"] |= other - used
    return result


def ensure_source(key: str) -> pathlib.Path:
    filename, url = SOURCES[key]
    path = SRC_DIR / filename
    if not path.exists():
        SRC_DIR.mkdir(exist_ok=True)
        print(f"下載原始字型 {filename} …")
        urllib.request.urlretrieve(url, path)
    return path


_instances = {}


def instance(src_key: str, weight: int) -> bytes:
    """整套字型固定成單一字重（只做一次，之後每片從這裡切）。"""
    key = (src_key, weight)
    if key not in _instances:
        font = TTFont(ensure_source(src_key))
        font = instancer.instantiateVariableFont(font, {"wght": weight}, updateFontNames=False)
        buf = io.BytesIO()
        font.save(buf)
        _instances[key] = buf.getvalue()
    return _instances[key]


def make_slice(src_key: str, weight: int, codepoints: set) -> tuple:
    font = TTFont(io.BytesIO(instance(src_key, weight)))
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.hinting = False
    opts.desubroutinize = True
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.layout_features = ["*"]
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=codepoints)
    sub.subset(font)
    buf = io.BytesIO()
    font.flavor = "woff2"
    font.recalcTimestamp = False  # 內容一樣 → 檔案一樣 → 雜湊一樣，不會每次都換檔名
    font["head"].modified = font["head"].created
    font.save(buf)
    data = buf.getvalue()
    covered = sorted(font.getBestCmap())
    return data, covered


def unicode_range(cps) -> str:
    cps = sorted(cps)
    out, i = [], 0
    while i < len(cps):
        j = i
        while j + 1 < len(cps) and cps[j + 1] == cps[j] + 1:
            j += 1
        out.append(f"U+{cps[i]:X}" if i == j else f"U+{cps[i]:X}-{cps[j]:X}")
        i = j + 1
    return ",".join(out)


def build(slices: dict) -> dict:
    files = {}
    for src_key, weight in WEIGHTS:
        fam = FAMILY[src_key][1]
        for sl in SLICES:
            if not slices[sl]:
                continue
            data, covered = make_slice(src_key, weight, slices[sl])
            h = hashlib.sha256(data).hexdigest()[:8]
            name = f"{fam}-{weight}-{sl}.{h}.woff2"
            (FONT_DIR / name).write_bytes(data)
            files[name] = {"family": src_key, "weight": weight, "slice": sl, "codepoints": covered}
            print(f"  {name}: {len(covered)} 字，{len(data) // 1024}KB")
    return {"slices": {k: sorted(v) for k, v in slices.items()}, "files": files}


def finish(manifest: dict) -> None:
    """頁面和 sw.js 都改好之後才刪舊檔、寫 manifest。
    中途出錯的話，舊字型檔都還在，頁面不會指到不存在的檔案。"""
    for p in FONT_DIR.glob("NotoS*TC-*.woff2"):
        if p.name not in manifest["files"] and "900-subset" not in p.name:
            p.unlink()
            print(f"  刪除舊檔 {p.name}")
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n", "utf-8")


def files_for(manifest: dict, src_key: str, weight: int, slice_names) -> list:
    return [n for n, f in manifest["files"].items()
            if f["family"] == src_key and f["weight"] == weight and f["slice"] in slice_names]


FACE_RE = re.compile(r"@font-face\{font-family:'Noto (?:Sans|Serif) TC';[^}]*?src:url\('/fonts/NotoS(?:ans|erif)TC-\d{3}(?:-[a-z-]+(?:\.[0-9a-f]+)?)?\.woff2'\)[^}]*\}\n?")
BLOCK_RE = re.compile(r"/\*fonts:start\*/.*?/\*fonts:end\*/\n?", re.S)
PRELOAD_OLD_RE = re.compile(r"<link rel=\"preload\" href=\"/fonts/NotoS(?:ans|erif)TC-\d{3}(?:-[a-z-]+\.[0-9a-f]+)?\.woff2\"[^>]*>")
PRELOAD_BLOCK_RE = re.compile(r"<!--fonts:preload:start-->.*?<!--fonts:preload:end-->", re.S)


def page_css(manifest: dict, cfg: dict) -> str:
    # 各片的字不重疊，瀏覽器只會下載畫面上有出現的字所在的那幾片；
    # 這頁用不到的片也照樣宣告，萬一內容更新後出現新字，至少還有字型可用。
    order = SLICES
    lines = ["/*fonts:start*/"]
    for src_key, weight in cfg["weights"]:
        fam = FAMILY[src_key][0]
        for sl in order:
            for name in files_for(manifest, src_key, weight, [sl]):
                ur = unicode_range(manifest["files"][name]["codepoints"])
                lines.append(f"@font-face{{font-family:'{fam}';font-style:normal;font-weight:{weight};"
                             f"font-display:swap;src:url('/fonts/{name}') format('woff2');unicode-range:{ur}}}")
    lines.append("/*fonts:end*/")
    return "\n".join(lines) + "\n"


def page_preload(manifest: dict, cfg: dict) -> str:
    links = []
    for i, (src_key, weight) in enumerate(cfg["preload"]):
        wanted = slices_of(cfg["slice"])
        for name in files_for(manifest, src_key, weight, wanted):
            prio = ' fetchpriority="high"' if i == 0 and not links else ""
            links.append(f'<link rel="preload" href="/fonts/{name}" as="font" type="font/woff2" crossorigin{prio}>')
    return "<!--fonts:preload:start-->" + "".join(links) + "<!--fonts:preload:end-->"


def update_pages(manifest: dict, quiet: bool = False) -> None:
    for rel, cfg in PAGES.items():
        p = ROOT / rel
        html = p.read_text("utf-8")
        css = page_css(manifest, cfg)
        if BLOCK_RE.search(html):
            html = BLOCK_RE.sub(lambda m: css, html, count=1)
        else:  # 第一次：把舊的 @font-face 換成自動產生的區塊
            m = FACE_RE.search(html)
            if not m:
                raise SystemExit(f"{rel}: 找不到 @font-face，無法插入")
            html = html[:m.start()] + "\0FONTS\0" + html[m.end():]
            html = FACE_RE.sub("", html)
            html = html.replace("\0FONTS\0", css)
        pre = page_preload(manifest, cfg)
        if PRELOAD_BLOCK_RE.search(html):
            html = PRELOAD_BLOCK_RE.sub(lambda m: pre, html, count=1)
        else:
            m = PRELOAD_OLD_RE.search(html)
            if m:
                html = html[:m.start()] + "\0PRE\0" + html[m.end():]
                html = PRELOAD_OLD_RE.sub("", html).replace("\0PRE\0", pre)
            elif cfg["preload"]:
                html = html.replace("</head>", pre + "</head>", 1)
        if html != p.read_text("utf-8"):
            p.write_text(html, "utf-8")
            print(f"  更新 {rel}")
        elif not quiet:
            print(f"  {rel} 不用改")


SW_RE = re.compile(r"( *)// fonts:precache:start\n.*?// fonts:precache:end", re.S)
SW_OLD_RE = re.compile(r"( *)'/fonts/NotoS(?:ans|erif)TC-\d{3}(?:-[a-z-]+\.[0-9a-f]+)?\.woff2',\n")


def update_sw(manifest: dict, quiet: bool = False) -> None:
    p = ROOT / "sw.js"
    js = p.read_text("utf-8")
    shared = sorted(n for n, f in manifest["files"].items() if f["slice"] == "all")

    def block(indent):
        body = "".join(f"{indent}'/fonts/{n}',\n" for n in shared)
        return f"{indent}// fonts:precache:start\n{body}{indent}// fonts:precache:end"

    if SW_RE.search(js):
        js = SW_RE.sub(lambda m: block(m.group(1)), js, count=1)
    else:
        m = SW_OLD_RE.search(js)
        if not m:
            raise SystemExit("sw.js: 找不到字型預快取清單")
        js = js[:m.start()] + "\0SW\0" + js[m.end():]
        js = SW_OLD_RE.sub("", js).replace("\0SW\0", block(m.group(1)) + "\n")
    if js != p.read_text("utf-8"):
        p.write_text(js, "utf-8")
        print("  更新 sw.js")


def needs_rebuild(slices: dict) -> bool:
    man = load_manifest()
    if not man:
        return True
    old = {k: set(v) for k, v in man.get("slices", {}).items()}
    if old != slices:
        return True
    return any(not (FONT_DIR / n).exists() for n in man["files"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只檢查，不重新產生")
    ap.add_argument("--force", action="store_true", help="完全照目前內容重新分組並重新產生（清掉累積的舊字）")
    args = ap.parse_args()

    slices = assign_slices(fresh=args.force)
    print("分片字數：" + "、".join(f"{k} {len(v)}" for k, v in slices.items()))
    stale = needs_rebuild(slices)
    print("需要重新產生" if stale else "字型分片已是最新 ✓")
    if args.check:
        return 1 if stale else 0
    if stale or args.force:
        manifest = build(slices)
        update_pages(manifest)
        update_sw(manifest)
        finish(manifest)
    else:
        # 字型沒變，也要確認每一頁的 @font-face 都指到現有的檔案
        # （例如新增了頁面，或上次自動化只 commit 了部分頁面）
        update_pages(load_manifest(), quiet=True)
        update_sw(load_manifest(), quiet=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

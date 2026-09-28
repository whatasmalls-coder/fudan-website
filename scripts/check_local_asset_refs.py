#!/usr/bin/env python3
"""
check_local_asset_refs.py — 檢查 HTML / CSS / JS 裡引用的「站內絕對路徑」
資源（字型、圖片、JSON、JS 等）是否真的存在於 repo 裡。

起因：曾經在刪除一個不再使用的字型檔時，漏看某個頁面自己還宣告了一份
指向同一檔案的 @font-face，導致上線後變成真正的 404。這支腳本就是為了
在 CI 階段就攔下這類「刪檔案但沒有清乾淨所有引用」或「打錯路徑」的問題。

只檢查以 "/" 開頭的站內絕對路徑（href="/...", src="/...", url(/...),
fetch('/...') 等），忽略外部網址（http/https）、data: URI、錨點（#...）、
mailto:/tel:。有查詢字串或錨點的路徑會先去掉再比對檔案是否存在。
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SCAN_EXTENSIONS = {'.html', '.js', '.css'}
SKIP_DIRS = {'.git', 'node_modules', 'scripts'}

# 找出 href="/x"、src="/x"、url(/x) 或 url('/x')、fetch('/x') 這幾種寫法裡的路徑
_ATTR_PATH = r'/[^"\'\s)>]+'
_URL_PATH = r'/[^"\'\s)]+'
PATTERN = re.compile(
    r'(?:href|src)\s*=\s*["\'](' + _ATTR_PATH + r')["\']'
    r'|url\(\s*["\']?(' + _URL_PATH + r')["\']?\s*\)'
    r'|fetch\(\s*["\'](' + _URL_PATH + r')["\']'
)


def local_path_from_match(m):
    for g in m.groups():
        if g:
            return g
    return None


def strip_query_and_hash(path):
    return path.split('?', 1)[0].split('#', 1)[0]


def should_check(path):
    # 排除協定相對網址（//example.com）與明顯是 API 路由的東西
    if path.startswith('//'):
        return False
    return True


def main():
    problems = []
    files_scanned = 0

    for path in REPO_ROOT.rglob('*'):
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        if path.suffix not in SCAN_EXTENSIONS:
            continue

        files_scanned += 1
        text = path.read_text(encoding='utf-8', errors='ignore')

        for m in PATTERN.finditer(text):
            raw = local_path_from_match(m)
            if not raw or not should_check(raw):
                continue
            clean = strip_query_and_hash(raw)
            if not clean or clean == '/':
                continue
            target = REPO_ROOT / clean.lstrip('/')
            # 目錄型路徑（例如 /bus-search/）視為 index.html
            if clean.endswith('/'):
                target = target / 'index.html'
            if not target.exists():
                problems.append((str(path.relative_to(REPO_ROOT)), raw))

    # sw.js 的預快取清單：只要有一個網址不存在，那一項就快取失敗、離線時打不開
    sw = REPO_ROOT / 'sw.js'
    if sw.exists():
        block = re.search(r'PRECACHE_URLS\s*=\s*\[(.*?)\];', sw.read_text(encoding='utf-8'), re.S)
        for raw in re.findall(r"'(/[^']*)'", block.group(1) if block else ''):
            clean = strip_query_and_hash(raw)
            target = REPO_ROOT / clean.lstrip('/')
            if clean.endswith('/'):
                target = target / 'index.html'
            if clean != '/' and not target.exists():
                problems.append(('sw.js（預快取）', raw))

    print(f'掃描了 {files_scanned} 個檔案')
    if problems:
        print(f'\n發現 {len(problems)} 個指向不存在檔案的站內路徑：\n')
        for src_file, ref in problems:
            print(f'  {src_file} → {ref}')
        sys.exit(1)
    else:
        print('沒有發現失效的站內路徑引用。')


if __name__ == '__main__':
    main()

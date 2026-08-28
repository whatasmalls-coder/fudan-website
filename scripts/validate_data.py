"""
資料格式驗證腳本 —— 在每次 push 時自動檢查關鍵資料檔案的結構是否正確，
避免手動編輯 news.json 或校車資料時打錯格式，導致網站在使用者端悄悄壞掉
（例如漏了逗號、欄位打錯字、日期格式錯誤等，這類錯誤肉眼很難一次看出來）。

檢查項目：
1. news.json：必須是陣列、每筆有 title/date/tag/link 四個欄位、最多 30 筆
2. bus-search/index.html 裡內嵌的路線 JSON：每條路線要有 no/name/time/stops，
   每個站牌要有 code/time/name

檢查失敗時會印出清楚的錯誤訊息並以非 0 狀態碼結束，讓 GitHub Actions 顯示紅色 ❌。
"""
import json
import re
import sys

errors = []


def check_news_json():
    try:
        with open("news.json", "r", encoding="utf-8") as f:
            items = json.load(f)
    except FileNotFoundError:
        errors.append("news.json 不存在")
        return
    except json.JSONDecodeError as e:
        errors.append(f"news.json 不是合法的 JSON：{e}")
        return

    if not isinstance(items, list):
        errors.append("news.json 最外層必須是陣列 []")
        return

    if len(items) > 30:
        errors.append(f"news.json 有 {len(items)} 筆，超過預期上限 30 筆，請確認是否要清理舊資料")

    required_fields = ["title", "date", "tag", "link"]
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"news.json 第 {i+1} 筆不是物件格式")
            continue
        for field in required_fields:
            if field not in item or not str(item.get(field, "")).strip():
                errors.append(f"news.json 第 {i+1} 筆（{item.get('title', '未知標題')}）缺少或空白的欄位：{field}")
        # 簡單檢查日期格式是否為 YYYY.MM.DD（配合 update_news.py 產生的實際格式）
        date_val = item.get("date", "")
        if date_val and not re.match(r"^\d{4}\.\d{2}\.\d{2}$", str(date_val)):
            errors.append(f"news.json 第 {i+1} 筆日期格式不是 YYYY.MM.DD：{date_val}")


def check_bus_data():
    try:
        with open("bus-search/index.html", "r", encoding="utf-8") as f:
            html = f.read()
    except FileNotFoundError:
        errors.append("bus-search/index.html 不存在")
        return

    match = re.search(
        r'<script id="routeData" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    if not match:
        errors.append("bus-search/index.html 裡找不到 id=\"routeData\" 的內嵌 JSON 區塊")
        return

    try:
        routes = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        errors.append(f"校車路線 JSON 不是合法格式：{e}")
        return

    if not isinstance(routes, list) or len(routes) == 0:
        errors.append("校車路線資料是空的或格式不是陣列")
        return

    route_required = ["no", "name", "time", "stops"]
    stop_required = ["code", "time", "name"]
    total_stops = 0

    for i, route in enumerate(routes):
        for field in route_required:
            if field not in route:
                errors.append(f"路線第 {i+1} 筆（{route.get('name', '未知')}）缺少欄位：{field}")
        stops = route.get("stops", [])
        if not isinstance(stops, list) or len(stops) == 0:
            errors.append(f"路線「{route.get('name', '未知')}」沒有任何站牌資料")
            continue
        total_stops += len(stops)
        for j, stop in enumerate(stops):
            for field in stop_required:
                if field not in stop:
                    errors.append(
                        f"路線「{route.get('name', '未知')}」第 {j+1} 站缺少欄位：{field}"
                    )

    print(f"共檢查 {len(routes)} 條路線、{total_stops} 個站牌")


def main():
    check_news_json()
    check_bus_data()

    if errors:
        print("\n❌ 資料驗證失敗：\n")
        for e in errors:
            print(f"  - {e}")
        print(f"\n共 {len(errors)} 個問題，請修正後再重新 commit。")
        sys.exit(1)
    else:
        print("✅ 資料驗證通過，news.json 與校車路線資料格式都正常。")


if __name__ == "__main__":
    main()

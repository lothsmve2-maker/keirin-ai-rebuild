import json
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE = "https://keirin.jp"

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14) "
        "AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36"
    ),
    "Referer": BASE + "/sp/raceschedule",
})

def get(url):
    r = session.get(url, timeout=25)
    r.encoding = r.apparent_encoding
    return r

def print_context(name, source, patterns, radius=5):
    lines = source.splitlines()
    found = set()

    for i, line in enumerate(lines):
        if any(re.search(p, line, re.I) for p in patterns):
            for j in range(max(0, i - radius), min(len(lines), i + radius + 1)):
                found.add(j)

    print(f"\n===== {name} ({len(lines)} lines) =====")
    for i in sorted(found):
        print(f"L{i + 1}: {lines[i][:500]}")

def main():
    print("=" * 60)
    print("KEIRIN.JP コメント通信パラメータ調査")
    print("=" * 60)

    schedule = get(BASE + "/sp/raceschedule")
    print("schedule:", schedule.status_code, len(schedule.text))

    # 本日の開催情報を取得
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")

    candidates = re.findall(
        r"""encPrm["']?\s*[:=]\s*["']([^"']+)["']""",
        schedule.text
    )

    # ページ内の開催情報から開催パラメータを探す
    soup = BeautifulSoup(schedule.text, "html.parser")
    page_text = str(soup)

    event_url = None
    event_params = []

    for m in re.finditer(r"JSJ049|kaisaiDayList|strRaceLnkPrm", page_text):
        event_params.append(page_text[max(0, m.start()-250):m.start()+500])

    print("\n開催情報候補数:", len(event_params))
    print("開催パラメータ候補:")
    for item in event_params[:8]:
        print(re.sub(r"\s+", " ", item)[:700])

    # 今日の開催パラメータが見つからなければ、
    # 調査対象の開催パラメータをログから設定して再実行できるようにする。
    event_encp = None

    for script in soup.find_all("script"):
        text = script.string or script.get_text() or ""
        if today in text and ("encPrm" in text or "JSJ049" in text):
            print("\nスケジュール内の本日関連スクリプト:")
            print(text[:5000])

    # 開催一覧APIのパラメータが取得できる場合は使用
    for value in candidates:
        if value:
            event_encp = value
            break

    print("\n日付:", today)
    print("自動検出した開催encp:", event_encp)

    # 既知の開催パラメータを使用して通信処理を調査
    # 必要なら、現在開催中のレースのencpに変更する。
    race_encp = (
        "a1Mwgr0l_Pv5qcmXHYStb3sofmLSwL6URPrXDRtm078"
    )

    race_url = BASE + "/sp/race?encp=" + race_encp
    race = get(race_url)
    print("\nレースページ:", race.status_code, len(race.text))

    rsoup = BeautifulSoup(race.text, "html.parser")
    hidden = {
        x.get("name"): x.get("value", "")
        for x in rsoup.select('input[type="hidden"][name]')
    }

    print("\n===== hidden inputs =====")
    print(json.dumps(hidden, ensure_ascii=False, indent=2)[:12000])

    scripts = []
    for tag in rsoup.find_all("script", src=True):
        src = urljoin(race_url, tag["src"])
        if "/static/js/" in src:
            scripts.append(src)

    targets = [
        "SJ0321_c.js",
        "SJ0321_v.js",
        "FSJ0315.js",
        "commonJSON.js",
        "SC0201.js",
    ]

    for target in targets:
        url = next((x for x in scripts if x.endswith("/" + target)), None)
        if not url:
            url = BASE + "/sp/static/js/" + target

        try:
            r = get(url)
            print(f"\n取得 {target}: HTTP {r.status_code}, {len(r.text)} bytes")

            patterns = [
                r"JSON_REQ_ID",
                r"getRequestGet",
                r"getRequestPost",
                r"ryController",
                r"ryDrawJSONData",
                r"ryCreateList",
                r"gReqPrmsObject",
                r"encp",
                r"kaisai",
                r"raceNo",
                r"syoriKbn",
                r"disp",
                r"params",
                r"ryTbodyRaceInfo",
                r"comment",
                r"jikou",
            ]

            print_context(target, r.text, patterns, radius=4)

        except Exception as e:
            print(f"取得失敗 {target}: {e}")

    # ページ内でコメント処理を呼び出している箇所も確認
    print_context(
        "RACE HTML",
        race.text,
        [
            r"ryTbodyRaceInfo",
            r"SJ0321",
            r"gReqPrmsObject",
            r"ryController",
            r"hhhdnPostUrl",
            r"encp",
            r"kaisaiBi",
            r"raceNo",
        ],
        radius=3,
    )

    print("\n調査終了")

if __name__ == "__main__":
    main()

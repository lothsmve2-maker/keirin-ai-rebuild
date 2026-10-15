import re
import requests
from bs4 import BeautifulSoup

BASE = "https://keirin.jp"
RACE_ENCP = "a1Mwgr0l_Pv5qcmXHYStb3sofmLSwL6URPrXDRtm078"

s = requests.Session()
s.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14) "
        "AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36"
    ),
    "Referer": BASE + "/sp/raceschedule",
})

def fetch(url):
    r = s.get(url, timeout=20)
    r.encoding = r.apparent_encoding
    return r

def show_block(name, text, patterns, before=3, after=5):
    lines = text.splitlines()
    indexes = set()

    for i, line in enumerate(lines):
        if any(re.search(p, line, re.I) for p in patterns):
            indexes.update(
                range(max(0, i - before), min(len(lines), i + after + 1))
            )

    print(f"\n--- {name} ---")
    for i in sorted(indexes):
        print(f"{i + 1}: {lines[i][:350]}")

def main():
    print("KEIRIN コメント通信・簡易調査")

    url = f"{BASE}/sp/race?encp={RACE_ENCP}"
    r = fetch(url)
    print("レースページ:", r.status_code, len(r.text), "bytes")

    soup = BeautifulSoup(r.text, "html.parser")

    # コメント関連のHTML要素
    for selector in [
        "#ryTbodyRaceInfo",
        "[id*='ry']",
        "[id*='Comment']",
    ]:
        for el in soup.select(selector)[:5]:
            print(
                "HTML:",
                el.name,
                el.get("id"),
                "text=",
                el.get_text(" ", strip=True)[:120],
            )

    # 必要なJavaScriptだけ調査
    targets = {
        "SJ0321_c.js": [
            r"JSON_REQ_ID",
            r"getRequestGet",
            r"ryController",
        ],
        "SJ0321_v.js": [
            r"ryDrawJSONData",
            r"ryCreateList",
            r"ryTbodyRaceInfo",
            r"sensyuTypeInfo",
        ],
        "FSJ0315.js": [
            r"gReqPrmsObject",
            r"getRequestGet",
            r"reqPromises",
            r"ryDrawJSONData",
            r"JSON_REQ_ID",
            r"update\(",
        ],
        "commonJSON.js": [
            r"getRequestGet:",
            r"getRequestPost:",
            r"ajaxCfg",
            r"url:",
        ],
    }

    for filename, patterns in targets.items():
        js_url = f"{BASE}/sp/static/js/{filename}"

        try:
            js = fetch(js_url)
            print(
                f"\n{filename}: HTTP {js.status_code}, "
                f"{len(js.text)} bytes"
            )
            show_block(filename, js.text, patterns)
        except Exception as e:
            print(f"{filename}: ERROR {e}")

    # HTML内の通信設定だけを表示
    show_block(
        "HTML通信設定",
        r.text,
        [
            r"hhhdnPostUrl",
            r"gReqPrmsObject",
            r"JSJ041",
            r"ryController",
            r"ryTbodyRaceInfo",
        ],
        before=2,
        after=4,
    )

    print("\n調査終了")

if __name__ == "__main__":
    main()

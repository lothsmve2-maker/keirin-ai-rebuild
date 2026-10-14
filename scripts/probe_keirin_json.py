import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "keirin_json_probe.json"

BASE_URL = "https://keirin.jp/sp/json"
TARGET_DATE = datetime.now(JST).strftime("%Y%m%d")

# 公式サイトのJavaScriptで確認したJSON識別子
REQUESTS_TO_TEST = [
    {"type": "JSJ058", "kday": TARGET_DATE},
    {"type": "JSJ083", "kday": TARGET_DATE},
    {"type": "JSJ080", "kday": TARGET_DATE, "mkbn": "2"},
    {"type": "JSJ081", "kday": TARGET_DATE, "mkbn": "2"},
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://keirin.jp/sp/top",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


def shorten(value, limit=5000):
    text = json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
    )
    if len(text) > limit:
        return text[:limit] + "\n...（表示を省略）"
    return text


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    # 先に公式ページを開き、セッションを準備する
    try:
        home = session.get(
            "https://keirin.jp/sp/top",
            timeout=20,
        )
        print("公式トップ:", home.status_code)
    except requests.RequestException as exc:
        print("トップページ取得エラー:", str(exc))

    results = []

    for params in REQUESTS_TO_TEST:
        print("\n" + "=" * 45)
        print("検証:", params)

        item = {
            "params": params,
            "url": BASE_URL,
            "status_code": None,
            "content_type": None,
            "response_bytes": 0,
            "json_parsed": False,
            "data": None,
            "text_preview": "",
            "error": None,
        }

        try:
            response = session.get(
                BASE_URL,
                params=params,
                timeout=30,
            )

            item["status_code"] = response.status_code
            item["content_type"] = response.headers.get(
                "Content-Type", ""
            )
            item["response_bytes"] = len(response.content)

            print("HTTP:", response.status_code)
            print("取得サイズ:", len(response.content))
            print("Content-Type:", item["content_type"])

            try:
                data = response.json()
                item["json_parsed"] = True
                item["data"] = data

                if isinstance(data, dict):
                    print("JSONのトップ階層キー:", list(data.keys()))

                    k_info = data.get("kInfo")
                    if isinstance(k_info, list):
                        print("開催情報件数:", len(k_info))

                        for venue in k_info[:10]:
                            if isinstance(venue, dict):
                                print(
                                    "開催情報:",
                                    json.dumps(
                                        venue,
                                        ensure_ascii=False,
                                    )[:700],
                                )

                print("JSONプレビュー:")
                print(shorten(data, 2500))

            except (ValueError, json.JSONDecodeError):
                preview = response.text[:5000]
                item["text_preview"] = preview
                print("JSONではありません。本文先頭:")
                print(preview[:1500])

            if response.status_code >= 400:
                item["error"] = (
                    f"HTTP {response.status_code}"
                )

        except requests.RequestException as exc:
            item["error"] = str(exc)
            print("取得エラー:", str(exc))

        results.append(item)

    output = {
        "generated_at": datetime.now(JST).isoformat(),
        "target_date": TARGET_DATE,
        "endpoint": BASE_URL,
        "results": results,
    }

    OUTPUT.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n" + "=" * 45)
    print("検証完了")
    print("保存先:", OUTPUT)
    print("結果件数:", len(results))


if __name__ == "__main__":
    main()

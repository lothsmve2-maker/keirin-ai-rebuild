import json
import time
from pathlib import Path

import requests

BASE = "https://keirin.jp"
DATA_FILE = Path("data/entries_today.json")
RACE_LIST_API = f"{BASE}/sp/json"
MAX_RACES = 8

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14) "
        "AppleWebKit/537.36 Chrome/131.0.0.0 "
        "Mobile Safari/537.36"
    ),
    "Referer": f"{BASE}/sp/raceschedule",
    "X-Requested-With": "XMLHttpRequest",
}


def collect_races(obj, found):
    """JSON内からレース情報を再帰的に収集する。"""

    if isinstance(obj, dict):
        encp = (
            obj.get("race_encp")
            or obj.get("race_encp_param")
        )

        if encp:
            found.append({
                "encp": str(encp),
                "venue": obj.get("venue", obj.get("venue_name", "")),
                "race_no": obj.get("race_no", obj.get("race_number", "")),
                "url": obj.get("race_url", ""),
            })

        for value in obj.values():
            if isinstance(value, (dict, list)):
                collect_races(value, found)

    elif isinstance(obj, list):
        for value in obj:
            collect_races(value, found)


def main():
    print("=" * 45)
    print("KEIRIN.JP 選手コメント取得・複数レース検証")
    print("=" * 45)

    if not DATA_FILE.exists():
        print("ERROR: data/entries_today.json がありません")
        return

    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print("JSON読込エラー:", e)
        return

    print("対象日:", data.get("target_date"))

    races = []
    collect_races(data, races)

    # encpで重複を除去
    unique = {}
    for race in races:
        unique.setdefault(race["encp"], race)

    races = list(unique.values())[:MAX_RACES]

    if not races:
        print("レース情報から race_encp を検出できませんでした")
        print("トップレベルのキー:", list(data.keys()))
        print("JSONのレース項目名を確認する必要があります")
        return

    print("検証レース数:", len(races))

    session = requests.Session()
    session.headers.update(HEADERS)

    total_riders = 0
    total_comment_fields = 0
    total_comments = 0

    for index, race in enumerate(races, start=1):
        label = (
            f"{race['venue']} {race['race_no']}R"
            if race["venue"] or race["race_no"]
            else f"検証レース{index}"
        )

        print(f"\n--- {index}/{len(races)} {label} ---")

        try:
            response = session.get(
                RACE_LIST_API,
                params={
                    "type": "JSJ041",
                    "encp": race["encp"],
                    "skbn": "1",
                },
                headers={
                    "Referer": (
                        race["url"]
                        if race["url"]
                        else f"{BASE}/sp/race?encp={race['encp']}"
                    )
                },
                timeout=20,
            )

            print("HTTP:", response.status_code)
            response.raise_for_status()
            payload = response.json()

            if not isinstance(payload, dict):
                print("JSON形式:", type(payload).__name__)
                continue

            riders = payload.get("sensyuTypeInfo", [])

            if not isinstance(riders, list):
                print("選手データ形式:", type(riders).__name__)
                continue

            total_riders += len(riders)

            print("選手数:", len(riders))
            print("resultCd:", payload.get("resultCd"))

            field_count = 0
            comment_count = 0

            for rider in riders:
                if not isinstance(rider, dict):
                    continue

                # コメントに関係する項目名を探す
                keys = [
                    key for key in rider
                    if "comment" in key.lower()
                    or "jikou" in key.lower()
                    or "sensyu" in key.lower()
                ]

                if keys:
                    field_count += 1

                for key in keys:
                    value = rider.get(key)

                    if (
                        "comment" in key.lower()
                        and isinstance(value, str)
                        and value.strip()
                    ):
                        comment_count += 1
                        print(
                            "コメントあり:",
                            rider.get("syaban", "?"),
                            rider.get("sensyuName", ""),
                            f"{key}={value[:80]}",
                        )

            total_comment_fields += field_count
            total_comments += comment_count

            print("関連項目を持つ選手数:", field_count)
            print("コメント本文あり:", comment_count)

            if riders and isinstance(riders[0], dict):
                print("先頭選手の全キー:", list(riders[0].keys()))

        except Exception as e:
            print("取得エラー:", str(e)[:200])

        time.sleep(0.3)

    print("\n" + "=" * 45)
    print("検証結果")
    print("検証レース数:", len(races))
    print("選手データ合計:", total_riders)
    print("関連項目を持つ選手数の合計:", total_comment_fields)
    print("コメント本文ありの合計:", total_comments)
    print("=" * 45)


if __name__ == "__main__":
    main()

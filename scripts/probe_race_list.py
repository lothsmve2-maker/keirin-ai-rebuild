#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from datetime import datetime, timezone, timedelta

import requests

BASE = "https://keirin.jp"
JST = timezone(timedelta(hours=9))
TIMEOUT = 25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ja-JP,ja;q=0.9",
}


def dump_json(obj, limit=1800):
    text = json.dumps(
        obj,
        ensure_ascii=False,
        indent=2,
    )
    return text[:limit]


def main():
    today = datetime.now(JST).date()

    print("=" * 60)
    print("KEIRIN.JP JSJ049 応答構造比較")
    print("対象日:", today.isoformat())
    print("=" * 60)

    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. 開催一覧を取得
    try:
        page = session.get(
            f"{BASE}/sp/raceschedule",
            timeout=TIMEOUT,
        )
        page.raise_for_status()

        response = session.get(
            f"{BASE}/sp/json",
            params={"type": "JSJ058"},
            headers={"Referer": page.url},
            timeout=TIMEOUT,
        )
        response.raise_for_status()

        schedule = response.json()

    except Exception as exc:
        print("開催一覧取得失敗:", repr(exc))
        return

    print("JSJ058 HTTP:", response.status_code)
    print("トップ階層:", type(schedule).__name__)

    if not isinstance(schedule, dict):
        print(dump_json(schedule, 3000))
        return

    events = schedule.get("kInfo", [])

    if not isinstance(events, list):
        print("kInfoがリストではありません")
        print(dump_json(schedule, 3000))
        return

    print("kInfo件数:", len(events))

    # 開催一覧で確認できた場を対象にする
    target_names = {"四日市", "豊橋", "奈良"}

    selected = []

    for event in events:
        if not isinstance(event, dict):
            continue

        venue = str(event.get("jyoName", "")).strip()
        encp = str(event.get("encPrm", "")).strip()
        start = str(event.get("kStartDate", "")).strip()

        if venue not in target_names or not encp:
            continue

        selected.append({
            "venue": venue,
            "encp": encp,
            "start": start,
            "race_num": event.get("raceNum"),
            "keys": list(event.keys()),
        })

    print("比較対象:", len(selected))

    for index, event in enumerate(selected, 1):
        venue = event["venue"]

        print()
        print("-" * 60)
        print(f'[{index}/{len(selected)}] {venue}')
        print("開催初日:", event["start"])
        print("JSJ058レース番号:", event["race_num"])
        print("JSJ058キー:", event["keys"])
        print("encPrm:", event["encp"])

        # 2. レース一覧を取得
        try:
            response = session.get(
                f"{BASE}/sp/json",
                params={
                    "type": "JSJ049",
                    "encp": event["encp"],
                },
                headers={
                    "Referer": f"{BASE}/sp/raceschedule",
                },
                timeout=TIMEOUT,
            )

            print("JSJ049 HTTP:", response.status_code)
            print("Content-Type:", response.headers.get("Content-Type"))
            print("レスポンスサイズ:", len(response.content))

            response.raise_for_status()
            payload = response.json()

        except Exception as exc:
            print("JSJ049取得失敗:", repr(exc))
            continue

        print("応答型:", type(payload).__name__)

        if not isinstance(payload, dict):
            print("応答:")
            print(dump_json(payload, 3000))
            continue

        print("トップ階層キー:", list(payload.keys()))
        print("API結果コード:", payload.get("resultCd"))
        print("APIメッセージコード:", payload.get("messageCd"))

        days = payload.get("kaisaiDayList")

        if not days:
            print("開催日時一覧:")
            print(
                json.dumps(
                    payload.get("kaisaiNitijiList"),
                    ensure_ascii=False,
                    indent=2,
                )
            )

            print("応答全文:")
            print(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                )
            )

        print("kaisaiDayList型:", type(days).__name__)

        if not isinstance(days, list):
            print("開催日リストが見つかりません")
            print("応答:")
            print(dump_json(payload, 4000))
            continue

        print("開催日リスト件数:", len(days))

        total_races = 0

        for day_index, day in enumerate(days):
            print()
            print(f"  開催日データ[{day_index}]")

            if not isinstance(day, dict):
                print("  データ型:", type(day).__name__)
                continue

            print("  キー:", list(day.keys()))

            for key in (
                "strNitiji",
                "strKaisaiDate",
                "kaisaiDate",
                "strDay",
            ):
                if key in day:
                    print(f"  {key}:", day[key])

            programs = day.get("raceProgramList")

            print(
                "  raceProgramList型:",
                type(programs).__name__,
            )

            if not isinstance(programs, list):
                print("  開催日データ:")
                print(" ", dump_json(day, 1800))
                continue

            print("  レース一覧要素数:", len(programs))
            total_races += len(programs)

            for race_index, race in enumerate(programs[:2]):
                print(f"  レースサンプル[{race_index}]:")

                if isinstance(race, dict):
                    print("    キー:", list(race.keys()))

                    for key in (
                        "strRaceNo",
                        "strRaceLnk",
                        "strRaceLnkPrm",
                        "strRaceName",
                        "strRaceTime",
                    ):
                        if key in race:
                            print(f"    {key}:", race[key])

                    print("    全データ:")
                    print("    " + dump_json(race, 1800).replace(
                        "\n", "\n    "
                    ))
                else:
                    print("    ", repr(race)[:1000])

        print()
        print(f"{venue} 合計レース一覧要素数:", total_races)

    print()
    print("=" * 60)
    print("診断終了")
    print("=" * 60)


if __name__ == "__main__":
    main()

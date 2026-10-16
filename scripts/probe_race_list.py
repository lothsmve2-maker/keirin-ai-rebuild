#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
from datetime import datetime, timezone, timedelta

import requests

BASE = "https://keirin.jp"
SCHEDULE_URL = f"{BASE}/sp/raceschedule"
SCHEDULE_API = f"{BASE}/sp/json"
RACE_LIST_API = f"{BASE}/sp/json"

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ja-JP,ja;q=0.9",
}

TIMEOUT = 25


def walk_dicts(obj, path="$"):
    if isinstance(obj, dict):
        yield path, obj

        for key, value in obj.items():
            yield from walk_dicts(
                value,
                f"{path}.{key}",
            )

    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from walk_dicts(
                value,
                f"{path}[{index}]",
            )


def first_value(obj, *keys):
    if not isinstance(obj, dict):
        return ""

    for key in keys:
        value = obj.get(key)

        if value not in (None, ""):
            return value

    return ""


def parse_date(value):
    if not value:
        return None

    value = str(value).strip()

    patterns = [
        r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})",
        r"(20\d{2})(\d{2})(\d{2})",
    ]

    for pattern in patterns:
        match = re.search(pattern, value)

        if not match:
            continue

        try:
            year, month, day = map(int, match.groups())
            return datetime(year, month, day).date()
        except ValueError:
            pass

    return None


def main():
    today = datetime.now(JST).date()

    print("=" * 60)
    print("KEIRIN.JP レース一覧API診断")
    print("対象日:", today.isoformat())
    print("=" * 60)

    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        landing = session.get(
            SCHEDULE_URL,
            timeout=TIMEOUT,
        )
        landing.raise_for_status()

        response = session.get(
            SCHEDULE_API,
            params={"type": "JSJ058"},
            headers={"Referer": landing.url},
            timeout=TIMEOUT,
        )
        response.raise_for_status()

        schedule = response.json()

    except Exception as exc:
        print("開催一覧取得エラー:", repr(exc))
        return

    print("開催一覧HTTP:", response.status_code)
    print("開催一覧トップ階層:", type(schedule).__name__)

    events = []
    seen = set()

    for path, obj in walk_dicts(schedule):
        encp = first_value(
            obj,
            "encPrm",
            "encp",
            "encprm",
        )

        venue = first_value(
            obj,
            "jyoName",
            "venueName",
            "jyo",
        )

        code = first_value(
            obj,
            "bKeirinCd",
            "KeirinCd",
            "jyoCd",
        )

        start = first_value(
            obj,
            "kStartDate",
            "startDate",
            "kaisaiDate",
            "start_date",
        )

        if not encp or not venue:
            continue

        key = (str(venue), str(code), str(start), str(encp))

        if key in seen:
            continue

        seen.add(key)

        start_date = parse_date(start)

        if start_date is not None and start_date > today:
            continue

        events.append({
            "venue": str(venue),
            "code": str(code),
            "start": str(start),
            "encp": str(encp),
            "path": path,
        })

    print("開催候補:", len(events))

    for index, event in enumerate(events, start=1):
        print()
        print("-" * 60)
        print(
            f'[{index}/{len(events)}] {event["venue"]} '
            f'開催初日={event["start"]} '
            f'場コード={event["code"]}'
        )

        print("開催データ位置:", event["path"])

        try:
            response = session.get(
                RACE_LIST_API,
                params={
                    "type": "JSJ049",
                    "encp": event["encp"],
                },
                headers={"Referer": SCHEDULE_URL},
                timeout=TIMEOUT,
            )

            print("レース一覧HTTP:", response.status_code)
            response.raise_for_status()

            payload = response.json()

        except Exception as exc:
            print("レース一覧取得エラー:", repr(exc))
            continue

        if not isinstance(payload, dict):
            print("応答型:", type(payload).__name__)
            print("応答冒頭:", repr(payload)[:500])
            continue

        print("応答トップ階層キー:", list(payload.keys()))

        days = payload.get("kaisaiDayList")

        print("kaisaiDayList型:", type(days).__name__)

        if not isinstance(days, list):
            print("kaisaiDayList以外の構造を調査:")

            for path, obj in walk_dicts(payload):
                list_keys = [
                    key for key, value in obj.items()
                    if isinstance(value, list)
                ]

                if list_keys:
                    print(
                        " ",
                        path,
                        "リスト項目:",
                        list_keys,
                    )

            print("応答JSON冒頭:")
            print(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                )[:2500]
            )
            continue

        print("開催日数:", len(days))

        total_programs = 0
        total_valid_races = 0

        for day_index, day in enumerate(days):
            if not isinstance(day, dict):
                print(
                    f"  日程[{day_index}]型:",
                    type(day).__name__,
                )
                continue

            day_name = first_value(
                day,
                "strNitiji",
                "strDay",
                "strKaisaiDate",
            )

            day_date = first_value(
                day,
                "strKaisaiDate",
                "kaisaiDate",
                "strEventDate",
            )

            programs = day.get("raceProgramList")

            print(
                f"  日程[{day_index}]",
                f"日付={day_date}",
                f"日程名={day_name}",
                f"キー={list(day.keys())}",
            )

            if not isinstance(programs, list):
                print(
                    "    raceProgramList型:",
                    type(programs).__name__,
                )
                continue

            total_programs += len(programs)

            for program in programs:
                if not isinstance(program, dict):
                    continue

                race_path = first_value(
                    program,
                    "strRaceLnk",
                )

                race_encp = first_value(
                    program,
                    "strRaceLnkPrm",
                )

                race_no = first_value(
                    program,
                    "strRaceNo",
                )

                if race_path and race_encp and race_no:
                    total_valid_races += 1

            if programs:
                print("    レース数:", len(programs))
                print(
                    "    最初のレースデータ:",
                    json.dumps(
                        programs[0],
                        ensure_ascii=False,
                    )[:1000],
                )

        print("レース一覧要素数:", total_programs)
        print("必要項目がそろったレース数:", total_valid_races)

        if not days:
            print("開催日程が空です。応答JSON:")
            print(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                )[:2500]
            )


if __name__ == "__main__":
    main()

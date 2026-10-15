#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KEIRIN.JP公式データ取得
開催情報 → レース一覧 → 個別レース → 出走表データ

出力:
data/entries_today.json

既存の診断データ、予想データ、画面は変更しない。
"""

import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup


BASE = "https://keirin.jp"
SCHEDULE_API = f"{BASE}/sp/json?type=JSJ058"
OUTPUT = Path("data/entries_today.json")

JST = timezone(timedelta(hours=9))
TIMEOUT = 25
PAUSE = 0.15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/json,"
        "application/javascript,*/*;q=0.8"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9",
}


def now_jst():
    return datetime.now(JST)


def clean(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return value
    return str(value).strip()


def first_value(obj, *keys):
    if not isinstance(obj, dict):
        return ""

    for key in keys:
        value = obj.get(key)
        if value not in (None, ""):
            return value

    return ""


def request_json(session, url):
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def parse_date(value):
    text = str(value or "")
    match = re.search(r"20\d{6}", text)

    if not match:
        return None

    try:
        return datetime.strptime(
            match.group(0), "%Y%m%d"
        ).date()
    except ValueError:
        return None


def walk_dicts(obj, path="$"):
    """JSON階層内のdictをすべて列挙する。"""

    if isinstance(obj, dict):
        yield path, obj

        for key, value in obj.items():
            yield from walk_dicts(value, f"{path}.{key}")

    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from walk_dicts(value, f"{path}[{index}]")


def find_events(payload):
    """
    JSJ058から開催情報候補を抽出する。
    暗号化パラメータを持つ開催オブジェクトを優先する。
    """

    events = []
    seen = set()

    for path, obj in walk_dicts(payload):
        enc = first_value(
            obj, "encPrm", "encp", "encprm"
        )

        venue = first_value(
            obj, "jyoName", "venueName", "jyo"
        )

        venue_code = first_value(
            obj, "bKeirinCd", "KeirinCd", "jyoCd"
        )

        start_date = first_value(
            obj,
            "kStartDate",
            "startDate",
            "kaisaiDate",
            "start_date",
        )

        if not enc or not venue:
            continue

        signature = (
            str(venue),
            str(venue_code),
            str(start_date),
            str(enc),
        )

        if signature in seen:
            continue

        seen.add(signature)

        events.append({
            "json_path": path,
            "venue": clean(venue),
            "venue_code": clean(venue_code),
            "start_date": clean(start_date),
            "encp": clean(enc),
        })

    return events


def fetch_event_races(session, event):
    """開催パラメータから開催日・レース一覧を取得する。"""

    url = f"{BASE}/sp/json?type=JSJ049"
    params = {"encp": event["encp"]}

    response = session.get(
        url,
        params=params,
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        raise ValueError("JSJ049のJSON形式がdictではありません")

    races = []

    days = payload.get("kaisaiDayList", [])

    if not isinstance(days, list):
        return races

    for day in days:
        if not isinstance(day, dict):
            continue

        day_name = first_value(
            day, "strNitiji", "strDay", "strKaisaiDate"
        )

        day_date = first_value(
            day, "strKaisaiDate", "kaisaiDate", "strEventDate"
        )

        programs = day.get("raceProgramList", [])

        if not isinstance(programs, list):
            continue

        for program in programs:
            if not isinstance(program, dict):
                continue

            race_path = first_value(
                program, "strRaceLnk"
            )

            race_encp = first_value(
                program, "strRaceLnkPrm"
            )

            race_no = first_value(
                program, "strRaceNo"
            )

            race_name = first_value(
                program, "strSyumokuName"
            )

            if not race_path or not race_encp or not race_no:
                continue

            races.append({
                "venue": event["venue"],
                "venue_code": event["venue_code"],
                "event_start_date": event["start_date"],
                "day": clean(day_name),
                "race_date": clean(day_date),
                "race_no": clean(race_no),
                "race_name": clean(race_name),
                "race_url": (
                    BASE + race_path
                    if race_path.startswith("/")
                    else f"{BASE}/{race_path}"
                ),
                "race_encp": clean(race_encp),
            })

    return races


def extract_assignment(text, variable_name):
    """
    HTML内の
    jsonData["SJ0315"] = {...};
    のJSONオブジェクトを抽出する。
    """

    patterns = [
        r"""jsonData\s*\[\s*["']"""
        + re.escape(variable_name)
        + r"""["']\s*\]\s*=\s*""",

        r"""jsonData\s*\[\s*["']"""
        + re.escape(variable_name)
        + r"""["']\s*]\s*=\s*""",
    ]

    match = None

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            break

    if not match:
        return None

    start = match.end()
    decoder = json.JSONDecoder()

    try:
        value, _ = decoder.raw_decode(text[start:].lstrip())
        return value
    except json.JSONDecodeError:
        return None


def normalize_rider(raw):
    """公式JSONの選手データを共通形式に整える。"""

    return {
        "car_no": clean(first_value(
            raw, "syaban", "shaban", "carNo"
        )),
        "registration_no": clean(first_value(
            raw, "sensyuRegistNo", "registNo"
        )),
        "name": clean(first_value(
            raw, "sensyuName", "name"
        )),
        "prefecture": clean(first_value(
            raw, "huKen", "prefecture"
        )),
        "previous_class": clean(first_value(
            raw, "prevKyuhan"
        )),
        "class": clean(first_value(
            raw, "kyuhan", "class"
        )),
        "style": clean(first_value(
            raw, "kyakusitu", "style"
        )),
        "points": clean(first_value(
            raw, "heikinTokuten", "tokuten"
        )),
        "nige": clean(first_value(
            raw, "nigeCnt"
        )),
        "makuri": clean(first_value(
            raw, "makuriCnt"
        )),
        "sashi": clean(first_value(
            raw, "sasiCnt"
        )),
        "mark": clean(first_value(
            raw, "markCnt"
        )),
        "back": clean(first_value(
            raw, "backCnt"
        )),
        "comment": "",
    }


def extract_race_data(session, race):
    """個別レースページから選手とラインを取得する。"""

    response = session.get(
        race["race_url"],
        params={"encp": race["race_encp"]},
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    base_info = extract_assignment(html, "SC0201")
    rider_info = extract_assignment(html, "SJ0315")
    line_info = extract_assignment(html, "SJ0314")

    if not isinstance(rider_info, dict):
        raise ValueError(
            "SJ0315が見つかりません。出走表JSONを抽出できませんでした"
        )

    raw_riders = rider_info.get("sensyuTypeInfo", [])

    if not isinstance(raw_riders, list):
        raw_riders = []

    riders = []

    for raw in raw_riders:
        if not isinstance(raw, dict):
            continue

        rider = normalize_rider(raw)

        if rider["car_no"] or rider["name"]:
            riders.append(rider)

    meta = {}

    if isinstance(base_info, dict):
        meta = base_info.get("C0201data", {}) or {}

    if not isinstance(meta, dict):
        meta = {}

    line_data = {}

    if isinstance(line_info, dict):
        line_data = line_info.get("narabiyoso", {}) or {}

    if not isinstance(line_data, dict):
        line_data = {}

    line_order = line_data.get("shaban", [])

    if not isinstance(line_order, list):
        line_order = []

    lines = []

    for item in line_order:
        if not isinstance(item, dict):
            continue

        lines.append({
            "position": clean(first_value(item, "ichi")),
            "car_no": clean(first_value(item, "shaban")),
        })

    # HTMLにコメント本文が存在するかは別途確認する。
    # ここでは未取得コメントを推測で埋めない。
    comment_detected = bool(
        re.search(r"選手コメント", html)
    )

    return {
        **race,
        "race_name": clean(first_value(
            meta, "syumoku"
        )) or race["race_name"],
        "venue": clean(first_value(
            meta, "joName"
        )) or race["venue"],
        "race_date_display": clean(first_value(
            meta, "txtEventDate"
        )),
        "start_time": clean(first_value(
            meta, "aftStartTime", "bfrStartTime"
        )),
        "distance_m": clean(first_value(
            meta, "kyori"
        )),
        "line_type": clean(first_value(
            line_data, "lineKeitai"
        )),
        "line_order": lines,
        "riders": riders,
        "rider_count": len(riders),
        "comment_field_detected": comment_detected,
        "comment_status": "本文未取得",
        "source": "KEIRIN.JP",
        "http_status": response.status_code,
    }


def main():
    started = now_jst()
    today = started.date().isoformat()

    result = {
        "schema_version": 1,
        "source": "KEIRIN.JP",
        "target_date": today,
        "started_at": started.isoformat(),
        "finished_at": "",
        "status": "running",
        "events_found": 0,
        "races_found": 0,
        "races_fetched": 0,
        "races_failed": 0,
        "rider_count": 0,
        "events": [],
        "races": [],
        "errors": [],
    }

    session = requests.Session()
    session.headers.update(HEADERS)

    print("=" * 55)
    print(" KEIRIN.JP 公式出走表データ取得")
    print("=" * 55)
    print("対象日:", today)

    try:
        print("[1/3] 開催情報を取得中...")

        schedule = request_json(session, SCHEDULE_API)
        all_events = find_events(schedule)

        result["events_found"] = len(all_events)

        today_events = []

        for event in all_events:
            start = parse_date(event.get("start_date"))

            # 開催初日が今日以前で、開催期間中のイベントを
            # JSJ049の開催日一覧でさらに判定する。
            if start is None or start <= started.date():
                today_events.append(event)

        print("開催候補:", len(today_events))

        print("[2/3] 本日のレース一覧を取得中...")

        all_races = []

        for event in today_events:
            try:
                races = fetch_event_races(session, event)

                # JSJ049内の各日程について、日付を特定できる
                # 場合は今日のレースだけを選択する。
                for race in races:
                    race_date = parse_date(race.get("race_date"))

                    if race_date is not None:
                        if race_date != started.date():
                            continue

                    race["event_encp"] = event["encp"]
                    all_races.append(race)

                result["events"].append({
                    "venue": event["venue"],
                    "venue_code": event["venue_code"],
                    "start_date": event["start_date"],
                    "race_count": len(races),
                })

                print(
                    " ",
                    event["venue"],
                    "レース候補:",
                    len(races),
                )

            except Exception as exc:
                result["errors"].append({
                    "stage": "race_list",
                    "venue": event.get("venue", ""),
                    "error": f"{type(exc).__name__}: {exc}",
                })

            time.sleep(PAUSE)

        # 同一レースの重複を排除
        unique_races = []
        seen = set()

        for race in all_races:
            key = (
                race["venue"],
                race["race_date"],
                race["race_no"],
            )

            if key in seen:
                continue

            seen.add(key)
            unique_races.append(race)

        result["races_found"] = len(unique_races)

        print("対象レース:", len(unique_races))
        print("[3/3] 各レースの出走表を取得中...")

        for index, race in enumerate(unique_races, 1):
            label = (
                f'{race["venue"]} '
                f'{race["race_no"]}R'
            )

            try:
                print(
                    f"  [{index}/{len(unique_races)}] {label}"
                )

                parsed = extract_race_data(session, race)

                if not parsed["riders"]:
                    raise ValueError(
                        "出走表JSONは取得できましたが選手が0人です"
                    )

                result["races"].append(parsed)
                result["races_fetched"] += 1
                result["rider_count"] += parsed["rider_count"]

            except Exception as exc:
                result["races_failed"] += 1

                result["errors"].append({
                    "stage": "race_detail",
                    "venue": race.get("venue", ""),
                    "race_no": race.get("race_no", ""),
                    "error": f"{type(exc).__name__}: {exc}",
                })

                print("    取得失敗:", exc)

            time.sleep(PAUSE)

        if result["races_fetched"] > 0 and result["races_failed"] == 0:
            result["status"] = "ok"
        elif result["races_fetched"] > 0:
            result["status"] = "partial"
        else:
            result["status"] = "error"

    except Exception as exc:
        result["status"] = "error"
        result["errors"].append({
            "stage": "main",
            "error": f"{type(exc).__name__}: {exc}",
        })

    result["finished_at"] = now_jst().isoformat()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("=" * 55)
    print("取得結果")
    print("状態:", result["status"])
    print("開催候補:", result["events_found"])
    print("レース候補:", result["races_found"])
    print("取得成功:", result["races_fetched"])
    print("取得失敗:", result["races_failed"])
    print("選手データ件数:", result["rider_count"])
    print("エラー件数:", len(result["errors"]))
    print("保存先:", OUTPUT)
    print("=" * 55)

    # GitHub Actionsで失敗を検出できるようにする
    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()

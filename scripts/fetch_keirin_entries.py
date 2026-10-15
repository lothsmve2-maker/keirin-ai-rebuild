#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""KEIRIN.JP公式出走表データ取得"""

import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE = "https://keirin.jp"
SCHEDULE_URL = f"{BASE}/sp/raceschedule"
SCHEDULE_API = f"{BASE}/sp/json?type=JSJ058"
RACE_LIST_API = f"{BASE}/sp/json"
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
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.7",
}


def now_jst():
    return datetime.now(JST)


def clean(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return value
    return str(value).strip()


def get_value(obj, *keys):
    if not isinstance(obj, dict):
        return ""

    for key in keys:
        value = obj.get(key)
        if value not in (None, ""):
            return value

    return ""


def walk_dicts(obj, path="$"):
    if isinstance(obj, dict):
        yield path, obj
        for key, value in obj.items():
            yield from walk_dicts(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from walk_dicts(value, f"{path}[{index}]")


def parse_date(value):
    text = str(value or "")

    for pattern, fmt in (
        (r"(20\d{6})", "%Y%m%d"),
        (r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", None),
    ):
        match = re.search(pattern, text)
        if not match:
            continue

        try:
            if fmt:
                return datetime.strptime(match.group(1), fmt).date()

            year, month, day = map(int, match.groups())
            from datetime import date
            return date(year, month, day)
        except (ValueError, TypeError):
            continue

    return None


def find_events(payload):
    events = []
    seen = set()

    for path, obj in walk_dicts(payload):
        encp = get_value(obj, "encPrm", "encp", "encprm")
        venue = get_value(obj, "jyoName", "venueName", "jyo")

        if not encp or not venue:
            continue

        event = {
            "json_path": path,
            "venue": clean(venue),
            "venue_code": clean(get_value(
                obj, "bKeirinCd", "KeirinCd", "jyoCd"
            )),
            "start_date": clean(get_value(
                obj,
                "kStartDate",
                "startDate",
                "kaisaiDate",
                "start_date",
            )),
            "encp": clean(encp),
        }

        key = (
            event["venue"],
            event["venue_code"],
            event["start_date"],
            event["encp"],
        )

        if key not in seen:
            seen.add(key)
            events.append(event)

    return events


def extract_assignment(html, variable_name):
    pattern = (
        r"""jsonData\s*\[\s*["']"""
        + re.escape(variable_name)
        + r"""["']\s*\]\s*=\s*"""
    )

    match = re.search(pattern, html)

    if not match:
        return None

    try:
        value, _ = json.JSONDecoder().raw_decode(
            html[match.end():].lstrip()
        )
        return value
    except json.JSONDecodeError:
        return None


def fetch_event_races(session, event):
    response = session.get(
        RACE_LIST_API,
        params={
            "type": "JSJ049",
            "encp": event["encp"],
        },
        headers={"Referer": SCHEDULE_URL},
        timeout=TIMEOUT,
        allow_redirects=True,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"JSJ049 HTTP {response.status_code}: "
            f"{response.url}; body={response.text[:200]!r}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"JSJ049 JSON解析失敗: {response.text[:200]!r}"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError("JSJ049の応答がJSONオブジェクトではありません")

    races = []

    for day in payload.get("kaisaiDayList", []):
        if not isinstance(day, dict):
            continue

        day_name = get_value(
            day, "strKaisaiNitijiName", "strNitiji",
            "strDay", "strKaisaiDate"
        )

        day_date = get_value(
            day, "strKaisaiDate", "kaisaiDate",
            "strEventDate", "kStartDate"
        )

        for item in day.get("raceProgramList", []):
            if not isinstance(item, dict):
                continue

            race_path = get_value(item, "strRaceLnk")
            race_encp = get_value(item, "strRaceLnkPrm")
            race_no = get_value(item, "strRaceNo")

            if not race_path or not race_encp or not race_no:
                continue

            races.append({
                "venue": event["venue"],
                "venue_code": event["venue_code"],
                "event_start_date": event["start_date"],
                "day": clean(day_name),
                "race_date": clean(day_date),
                "race_no": clean(race_no),
                "race_name": clean(get_value(
                    item, "strSyumokuName"
                )),
                "race_url": urljoin(BASE, race_path),
                "race_encp": clean(race_encp),
            })

    return races


def normalize_rider(raw):
    return {
        "car_no": clean(get_value(raw, "syaban", "shaban", "carNo")),
        "registration_no": clean(get_value(
            raw, "sensyuRegistNo", "registNo"
        )),
        "name": clean(get_value(raw, "sensyuName", "name")),
        "prefecture": clean(get_value(raw, "huKen", "prefecture")),
        "previous_class": clean(get_value(raw, "prevKyuhan")),
        "class": clean(get_value(raw, "kyuhan", "class")),
        "style": clean(get_value(raw, "kyakusitu", "style")),
        "points": clean(get_value(
            raw, "heikinTokuten", "tokuten"
        )),
        "nige": clean(get_value(raw, "nigeCnt")),
        "makuri": clean(get_value(raw, "makuriCnt")),
        "sashi": clean(get_value(raw, "sasiCnt")),
        "mark": clean(get_value(raw, "markCnt")),
        "back": clean(get_value(raw, "backCnt")),
        "comment": "",
    }


def extract_race_data(session, race):
    response = session.get(
        race["race_url"],
        params={"encp": race["race_encp"]},
        headers={"Referer": SCHEDULE_URL},
        timeout=TIMEOUT,
        allow_redirects=True,
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"個別レース HTTP {response.status_code}: "
            f"requested={response.url}; "
            f"body={response.text[:250]!r}"
        )

    html = response.text
    rider_info = extract_assignment(html, "SJ0315")
    line_info = extract_assignment(html, "SJ0314")
    base_info = extract_assignment(html, "SC0201")

    if not isinstance(rider_info, dict):
        raise RuntimeError(
            "SJ0315を解析できません。"
            f"URL={response.url}; title="
            f"{BeautifulSoup(html, 'html.parser').title}"
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

    if not riders:
        raise RuntimeError(
            "SJ0315は見つかりましたが選手データが0人です"
        )

    line_data = {}
    if isinstance(line_info, dict):
        line_data = line_info.get("narabiyoso", {}) or {}

    if not isinstance(line_data, dict):
        line_data = {}

    line_order = []
    for item in line_data.get("shaban", []):
        if isinstance(item, dict):
            line_order.append({
                "position": clean(get_value(item, "ichi")),
                "car_no": clean(get_value(item, "shaban")),
            })

    meta = {}
    if isinstance(base_info, dict):
        meta = base_info.get("C0201data", {}) or {}

    if not isinstance(meta, dict):
        meta = {}

    return {
        **race,
        "race_name": clean(get_value(
            meta, "syumoku"
        )) or race["race_name"],
        "venue": clean(get_value(
            meta, "joName"
        )) or race["venue"],
        "race_date_display": clean(get_value(
            meta, "txtEventDate"
        )),
        "start_time": clean(get_value(
            meta, "aftStartTime", "bfrStartTime"
        )),
        "distance_m": clean(get_value(meta, "kyori")),
        "line_type": clean(get_value(line_data, "lineKeitai")),
        "line_order": line_order,
        "riders": riders,
        "rider_count": len(riders),
        "comment_status": "本文未取得",
        "source": "KEIRIN.JP",
        "http_status": response.status_code,
        "final_url": response.url,
    }


def main():
    started = now_jst()
    today = started.date()

    result = {
        "schema_version": 2,
        "source": "KEIRIN.JP",
        "target_date": today.isoformat(),
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
    print(" KEIRIN.JP 公式出走表データ取得 v2")
    print("=" * 55)
    print("対象日:", today.isoformat())

    try:
        # 重要：診断スクリプトと同様に、まず公式日程ページへアクセス
        print("[1/3] 公式日程ページ・開催情報を取得中...")

        landing = session.get(
            SCHEDULE_URL,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        landing.raise_for_status()

        schedule_response = session.get(
            SCHEDULE_API,
            headers={"Referer": SCHEDULE_URL},
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        schedule_response.raise_for_status()
        schedule = schedule_response.json()

        all_events = find_events(schedule)
        result["events_found"] = len(all_events)

        print("開催候補:", len(all_events))
        print("[2/3] 開催ごとのレース一覧を取得中...")

        all_races = []

        for event in all_events:
            start_date = parse_date(event["start_date"])

            if start_date and start_date > today:
                continue

            try:
                races = fetch_event_races(session, event)

                # 日付が明確に取れるレースは今日以外を除外する。
                # 日付が判別不能な場合は、開催日程APIの当日開催候補を残す。
                selected = []

                for race in races:
                    race_date = parse_date(race["race_date"])

                    if race_date and race_date != today:
                        continue

                    race["event_encp"] = event["encp"]
                    selected.append(race)

                all_races.extend(selected)

                result["events"].append({
                    "venue": event["venue"],
                    "venue_code": event["venue_code"],
                    "start_date": event["start_date"],
                    "race_count": len(selected),
                })

                print(f'  {event["venue"]}: {len(selected)}レース')

            except Exception as exc:
                result["errors"].append({
                    "stage": "race_list",
                    "venue": event["venue"],
                    "error": f"{type(exc).__name__}: {exc}",
                })
                print(f'  {event["venue"]}: 一覧取得失敗: {exc}')

            time.sleep(PAUSE)

        unique_races = []
        seen = set()

        for race in all_races:
            key = (
                race["venue"],
                race["race_no"],
                race["race_encp"],
            )

            if key not in seen:
                seen.add(key)
                unique_races.append(race)

        result["races_found"] = len(unique_races)

        print("対象レース:", len(unique_races))
        print("[3/3] 個別レースの出走表を取得中...")

        for index, race in enumerate(unique_races, 1):
            label = f'{race["venue"]} {race["race_no"]}R'
            print(f"  [{index}/{len(unique_races)}] {label}")

            try:
                parsed = extract_race_data(session, race)
                result["races"].append(parsed)
                result["races_fetched"] += 1
                result["rider_count"] += parsed["rider_count"]

                print(
                    f'    成功: 選手{parsed["rider_count"]}人'
                )

            except Exception as exc:
                result["races_failed"] += 1

                result["errors"].append({
                    "stage": "race_detail",
                    "venue": race["venue"],
                    "race_no": race["race_no"],
                    "race_url": race["race_url"],
                    "race_encp": race["race_encp"],
                    "error": f"{type(exc).__name__}: {exc}",
                })

                print(f"    失敗: {exc}")

            time.sleep(PAUSE)

        if result["races_fetched"] == 0:
            result["status"] = "error"
        elif result["races_failed"] > 0:
            result["status"] = "partial"
        else:
            result["status"] = "ok"

    except Exception as exc:
        result["status"] = "error"
        result["errors"].append({
            "stage": "main",
            "error": f"{type(exc).__name__}: {exc}",
        })
        print("全体エラー:", repr(exc))

    result["finished_at"] = now_jst().isoformat()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
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

    # 取得成功が一件もない場合だけ、Actionsを失敗にする。
    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()

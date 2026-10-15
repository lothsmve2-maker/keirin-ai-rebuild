#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KEIRIN.JP 公式出走表取得
------------------------------------
・公式開催一覧を取得
・開催別レース一覧を取得
・個別レースの出走表を取得
・公式のライン情報を保存
・レース日を個別レースページから検証
・選手コメントの取得状況を記録
・車番、選手数、ライン情報を検証
・data/entries_today.json に保存

重要:
ラインのグループは公式データから確定できる場合だけ設定する。
確認できないライン構造を数値の間隔だけで推測しない。
"""

import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests


# ============================================================
# 設定
# ============================================================

BASE = "https://keirin.jp"
SCHEDULE_URL = f"{BASE}/sp/raceschedule"
SCHEDULE_API = f"{BASE}/sp/json?type=JSJ058"
RACE_LIST_API = f"{BASE}/sp/json"

OUTPUT = Path("data/entries_today.json")

JST = timezone(timedelta(hours=9))

TIMEOUT = 25
PAUSE = 0.15

SCHEMA_VERSION = 3

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


# ============================================================
# 基本関数
# ============================================================

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


def parse_date(value):
    """
    日付文字列から日付を抽出する。
    20261015 / 2026-10-15 / 2026/10/15 に対応。
    """

    if value is None:
        return None

    text = str(value).strip()

    patterns = [
        r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})",
        r"(20\d{2})(\d{2})(\d{2})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if not match:
            continue

        try:
            year, month, day = map(int, match.groups())

            return datetime(
                year, month, day
            ).date()

        except ValueError:
            continue

    return None


def request_page(session, url, params=None, referer=None):
    headers = {}

    if referer:
        headers["Referer"] = referer

    response = session.get(
        url,
        params=params,
        headers=headers,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    return response


def request_json(session, url, params=None, referer=None):
    response = request_page(
        session,
        url,
        params=params,
        referer=referer,
    )

    return response.json()


def walk_dicts(obj, path="$"):
    """JSON階層内の辞書を列挙する。"""

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


# ============================================================
# 開催情報
# ============================================================

def find_events(payload):
    """
    JSJ058から開催候補を抽出する。

    暗号化パラメータを持つ開催オブジェクトを対象とする。
    同じ開催の重複を排除する。
    """

    events = []
    seen = set()

    for path, obj in walk_dicts(payload):
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

        venue_code = first_value(
            obj,
            "bKeirinCd",
            "KeirinCd",
            "jyoCd",
        )

        start_date = first_value(
            obj,
            "kStartDate",
            "startDate",
            "kaisaiDate",
            "start_date",
        )

        if not encp or not venue:
            continue

        key = (
            str(venue),
            str(venue_code),
            str(start_date),
            str(encp),
        )

        if key in seen:
            continue

        seen.add(key)

        events.append({
            "venue": clean(venue),
            "venue_code": clean(venue_code),
            "start_date": clean(start_date),
            "encp": clean(encp),
            "source_path": path,
        })

    return events


def fetch_event_races(session, event):
    """開催パラメータから開催日ごとのレース一覧を取得する。"""

    payload = request_json(
        session,
        RACE_LIST_API,
        params={
            "type": "JSJ049",
            "encp": event["encp"],
        },
        referer=SCHEDULE_URL,
    )

    if not isinstance(payload, dict):
        raise ValueError(
            "JSJ049の応答が辞書形式ではありません"
        )

    days = payload.get("kaisaiDayList", [])

    if not isinstance(days, list):
        raise ValueError(
            "kaisaiDayListがリスト形式ではありません"
        )

    races = []

    for day in days:
        if not isinstance(day, dict):
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

        programs = day.get("raceProgramList", [])

        if not isinstance(programs, list):
            continue

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

            race_name = first_value(
                program,
                "strSyumokuName",
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
                "race_url": urljoin(
                    BASE,
                    race_path,
                ),
                "race_encp": clean(race_encp),
                "event_encp": event["encp"],
            })

    return races


# ============================================================
# 公式ページ内JSON
# ============================================================

def extract_assignment(html, variable_name):
    """
    次のようなJavaScript内JSONを抽出する。

    jsonData["SJ0315"] = {...};
    """

    pattern = (
        r"""jsonData\s*\[\s*["']"""
        + re.escape(variable_name)
        + r"""["']\s*\]\s*=\s*"""
    )

    match = re.search(pattern, html)

    if not match:
        return None

    start = match.end()

    decoder = json.JSONDecoder()

    try:
        value, _ = decoder.raw_decode(
            html[start:].lstrip()
        )

        return value

    except json.JSONDecodeError:
        return None


# ============================================================
# 選手データ
# ============================================================
def fetch_official_comments(session, race_encp, referer=None):
    """
    KEIRIN.JP公式JSONから選手コメントを取得する。
    戻り値は車番をキーにした辞書。
    取得できない場合は空辞書を返す。
    """
    url = f"{BASE}/sp/json"

    headers = {
        "Referer": referer or f"{BASE}/sp/raceschedule",
        "X-Requested-With": "XMLHttpRequest",
    }

    comments = {}

    # 公式ページと同じ通信形式で取得する
    for skbn in ("1",):
        try:
            response = session.get(
                url,
                params={
                    "type": "JSJ041",
                    "encp": race_encp,
                    "skbn": skbn,
                },
                headers=headers,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()

            if not isinstance(payload, dict):
                continue

            riders = payload.get("sensyuTypeInfo", [])

            if not isinstance(riders, list):
                continue

            for rider in riders:
                if not isinstance(rider, dict):
                    continue

                car_no = str(rider.get("syaban", "")).strip()
                comment = str(
                    rider.get("sensyuComment", "")
                ).strip()

                if car_no and comment:
                    comments[car_no] = comment

            # JSONが正常に返った場合はここで終了
            break

        except Exception as e:
            print(
                f"  コメント取得エラー: "
                f"{str(e)[:150]}"
            )

    return comments
def normalize_rider(raw):
    """公式JSONの選手データを共通形式にする。"""

    # コメントのキーは公式データに存在するものだけ利用する。
    # 存在しなければ空欄とし、推測で生成しない。
    comment = first_value(
        raw,
        "sensyuComment",
        "comment",
        "commentText",
        "strComment",
        "jikouComment",
    )

    if not isinstance(comment, str):
        comment = ""

    return {
        "car_no": clean(first_value(
            raw,
            "syaban",
            "shaban",
            "carNo",
        )),
        "registration_no": clean(first_value(
            raw,
            "sensyuRegistNo",
            "registNo",
        )),
        "name": clean(first_value(
            raw,
            "sensyuName",
            "name",
        )),
        "prefecture": clean(first_value(
            raw,
            "huKen",
            "prefecture",
        )),
        "previous_class": clean(first_value(
            raw,
            "prevKyuhan",
        )),
        "class": clean(first_value(
            raw,
            "kyuhan",
            "class",
        )),
        "style": clean(first_value(
            raw,
            "kyakusitu",
            "style",
        )),
        "points": clean(first_value(
            raw,
            "heikinTokuten",
            "tokuten",
        )),
        "nige": clean(first_value(
            raw,
            "nigeCnt",
        )),
        "makuri": clean(first_value(
            raw,
            "makuriCnt",
        )),
        "sashi": clean(first_value(
            raw,
            "sasiCnt",
        )),
        "mark": clean(first_value(
            raw,
            "markCnt",
        )),
        "back": clean(first_value(
            raw,
            "backCnt",
        )),
        "comment": comment,
        "comment_available": bool(comment.strip()),
    }


# ============================================================
# ライン情報
# ============================================================

def extract_official_lines(line_info):
    """
    公式の並び予想データを保存する。

    注意:
    ichiの数値間隔からラインを推測しない。
    公式のグループ情報が確認できない場合は、
    位置情報だけを保存する。
    """

    if not isinstance(line_info, dict):
        return {
            "line_type": "",
            "line_order": [],
            "lines": [],
            "line_status": "公式データなし",
        }

    line_data = line_info.get(
        "narabiyoso",
        {},
    )

    if not isinstance(line_data, dict):
        line_data = {}

    raw_order = line_data.get(
        "shaban",
        [],
    )

    if not isinstance(raw_order, list):
        raw_order = []

    line_order = []

    for item in raw_order:
        if not isinstance(item, dict):
            continue

        car_no = clean(first_value(
            item,
            "shaban",
            "syaban",
        ))

        position = clean(first_value(
            item,
            "ichi",
        ))

        if not car_no:
            continue

        try:
            position_value = int(position)
        except (TypeError, ValueError):
            position_value = None

        line_order.append({
            "position": position_value,
            "car_no": car_no,
        })

    # 公式の位置情報は保持するが、
    # 数値の連続性だけでラインを分割しない。
    return {
        "line_type": clean(first_value(
            line_data,
            "lineKeitai",
        )),
        "line_order": line_order,
        "lines": [],
        "line_status": "公式位置情報を保存・グループ未確定",
    }


# ============================================================
# 個別レース
# ============================================================

def extract_race_data(session, race):
    """個別レースページから出走表を取得する。"""

    response = request_page(
        session,
        race["race_url"],
        params={
            "encp": race["race_encp"],
        },
        referer=SCHEDULE_URL,
    )

    html = response.text

    base_info = extract_assignment(
        html,
        "SC0201",
    )

    rider_info = extract_assignment(
        html,
        "SJ0315",
    )

    line_info = extract_assignment(
        html,
        "SJ0314",
    )

    if not isinstance(rider_info, dict):
        raise ValueError(
            "SJ0315を抽出できません"
        )

    raw_riders = rider_info.get(
        "sensyuTypeInfo",
        [],
    )

    if not isinstance(raw_riders, list):
        raw_riders = []

    riders = []

    for raw in raw_riders:
        if not isinstance(raw, dict):
            continue

        rider = normalize_rider(raw)

        if rider["car_no"] or rider["name"]:
            riders.append(rider)
    # KEIRIN.JP公式コメントを取得
    official_comments = fetch_official_comments(
        session,
        race["race_encp"],
        referer=race.get("race_url"),
    )

    # 取得したコメントを選手データに反映
    for rider in riders:
        car_no = str(rider.get("car_no", "")).strip()
        comment = official_comments.get(car_no, "")

        if comment:
            rider["comment"] = comment
            rider["comment_available"] = True
    meta = {}

    if isinstance(base_info, dict):
        meta = base_info.get(
            "C0201data",
            {},
        ) or {}

    if not isinstance(meta, dict):
        meta = {}

    official_lines = extract_official_lines(
        line_info
    )

    race_date_display = clean(first_value(
        meta,
        "txtEventDate",
    ))

    race_date = parse_date(
        race_date_display
    )

    # 開催日程JSONの日付よりも、個別レースページの
    # 日付表示を優先して検証する。
    if race_date is not None:
        verified_race_date = race_date.isoformat()
    else:
        verified_race_date = ""

    car_numbers = [
        rider["car_no"]
        for rider in riders
        if rider["car_no"]
    ]

    line_car_numbers = [
        item["car_no"]
        for item in official_lines["line_order"]
    ]

    duplicate_car_numbers = (
        len(car_numbers) != len(set(car_numbers))
    )

    duplicate_line_numbers = (
        len(line_car_numbers)
        != len(set(line_car_numbers))
    )

    missing_line_numbers = sorted(
        set(car_numbers) - set(line_car_numbers)
    )

    unknown_line_numbers = sorted(
        set(line_car_numbers) - set(car_numbers)
    )

    comment_count = sum(
        1
        for rider in riders
        if rider["comment_available"]
    )

    if comment_count == len(riders) and riders:
        comment_status = "取得済み"
    elif comment_count > 0:
        comment_status = "一部取得"
    else:
        comment_status = "本文未取得"

    return {
        **race,

        "race_date_display": race_date_display,
        "verified_race_date": verified_race_date,

        "race_name": clean(first_value(
            meta,
            "syumoku",
        )) or race["race_name"],

        "venue": clean(first_value(
            meta,
            "joName",
        )) or race["venue"],

        "start_time": clean(first_value(
            meta,
            "aftStartTime",
            "bfrStartTime",
        )),

        "distance_m": clean(first_value(
            meta,
            "kyori",
        )),

        "line_type": official_lines["line_type"],
        "line_order": official_lines["line_order"],
        "lines": official_lines["lines"],
        "line_status": official_lines["line_status"],

        "riders": riders,
        "rider_count": len(riders),

        "validation": {
            "duplicate_car_numbers": duplicate_car_numbers,
            "duplicate_line_numbers": duplicate_line_numbers,
            "missing_line_numbers": missing_line_numbers,
            "unknown_line_numbers": unknown_line_numbers,
            "rider_count_valid": len(riders) > 0,
        },

        "comment_count": comment_count,
        "comment_status": comment_status,

        "source": "KEIRIN.JP",
        "http_status": response.status_code,
    }


# ============================================================
# メイン処理
# ============================================================

def main():
    started = now_jst()
    today = started.date()

    result = {
        "schema_version": SCHEMA_VERSION,
        "source": "KEIRIN.JP",
        "target_date": today.isoformat(),
        "started_at": started.isoformat(),
        "finished_at": "",
        "status": "running",

        "events_found": 0,
        "events_checked": 0,
        "events_with_races": 0,

        "races_found": 0,
        "races_checked": 0,
        "races_fetched": 0,
        "races_excluded_other_date": 0,
        "races_date_unknown": 0,
        "races_failed": 0,

        "rider_count": 0,
        "commented_rider_count": 0,

        "events": [],
        "races": [],
        "errors": [],
        "warnings": [],
    }

    session = requests.Session()
    session.headers.update(HEADERS)

    print("=" * 60)
    print(" KEIRIN.JP 公式出走表取得 v3")
    print("=" * 60)
    print("対象日:", today.isoformat())

    try:
        # ----------------------------------------------------
        # 1. セッション初期化・開催一覧
        # ----------------------------------------------------

        print("[1/4] 公式開催ページを初期化...")

        landing = request_page(
            session,
            SCHEDULE_URL,
        )

        schedule = request_json(
            session,
            SCHEDULE_API,
            referer=landing.url,
        )

        all_events = find_events(schedule)

        result["events_found"] = len(all_events)

        print("開催候補:", len(all_events))

        # 開催初日が今日以前の開催を調べる。
        # 開催期間中かどうかは、個別レース日付で最終判定する。
        candidate_events = []

        for event in all_events:
            start = parse_date(
                event.get("start_date")
            )

            if start is None:
                result["warnings"].append({
                    "stage": "event_date",
                    "venue": event["venue"],
                    "message": "開催初日を解析できません",
                })

                # 日付不明の開催を自動で対象にしない。
                continue

            if start <= today:
                candidate_events.append(event)

        print(
            "開催詳細の確認対象:",
            len(candidate_events),
        )

        # ----------------------------------------------------
        # 2. 開催ごとのレース一覧
        # ----------------------------------------------------

        print("[2/4] 開催ごとのレース一覧を取得...")

        candidate_races = []

        for event in candidate_events:
            event_record = {
                "venue": event["venue"],
                "venue_code": event["venue_code"],
                "start_date": event["start_date"],
                "race_count": 0,
                "status": "確認中",
            }

            try:
                races = fetch_event_races(
                    session,
                    event,
                )

                event_record["race_count"] = len(races)
                event_record["status"] = (
                    "レース一覧取得済み"
                    if races
                    else "レース一覧0件"
                )

                candidate_races.extend(races)

                if races:
                    result["events_with_races"] += 1

                print(
                    f'  {event["venue"]}: '
                    f'{len(races)}レース候補'
                )

            except Exception as exc:
                event_record["status"] = "取得失敗"

                result["errors"].append({
                    "stage": "race_list",
                    "venue": event["venue"],
                    "error": (
                        f"{type(exc).__name__}: {exc}"
                    ),
                })

                print(
                    f'  {event["venue"]}: 取得失敗 - {exc}'
                )

            result["events"].append(event_record)

            time.sleep(PAUSE)

        # レース番号・会場・暗号化パラメータで重複排除。
        unique_races = []
        seen_races = set()

        for race in candidate_races:
            key = (
                race["venue_code"],
                race["race_encp"],
            )

            if key in seen_races:
                continue

            seen_races.add(key)
            unique_races.append(race)

        result["races_found"] = len(unique_races)

        print(
            "個別ページで日付を確認するレース:",
            len(unique_races),
        )

        # ----------------------------------------------------
        # 3. 個別レースを取得して日付を検証
        # ----------------------------------------------------

        print("[3/4] 個別レースの出走表を取得...")

        for index, race in enumerate(
            unique_races,
            start=1,
        ):
            label = (
                f'{race["venue"]} '
                f'{race["race_no"]}R'
            )

            result["races_checked"] += 1

            print(
                f"  [{index}/{len(unique_races)}] {label}"
            )

            try:
                parsed = extract_race_data(
                    session,
                    race,
                )

                # 個別ページの日付が取得できなければ、
                # 今日のレースと決めつけない。
                actual_date = parse_date(
                    parsed.get("race_date_display")
                )

                if actual_date is None:
                    result["races_date_unknown"] += 1

                    result["warnings"].append({
                        "stage": "race_date",
                        "venue": race["venue"],
                        "race_no": race["race_no"],
                        "message": (
                            "個別レースページの日付を"
                            "確認できないため対象外"
                        ),
                    })

                    print("    日付不明: 対象外")
                    time.sleep(PAUSE)
                    continue

                if actual_date != today:
                    result["races_excluded_other_date"] += 1

                    print(
                        "    対象外:",
                        actual_date.isoformat(),
                    )

                    time.sleep(PAUSE)
                    continue

                if not parsed["riders"]:
                    raise ValueError(
                        "出走表の選手データが0人です"
                    )

                # 車番重複は正常な出走表ではないため記録する。
                if parsed["validation"]["duplicate_car_numbers"]:
                    result["warnings"].append({
                        "stage": "validation",
                        "venue": race["venue"],
                        "race_no": race["race_no"],
                        "message": "車番が重複しています",
                    })

                result["races"].append(parsed)

                result["races_fetched"] += 1
                result["rider_count"] += parsed["rider_count"]
                result["commented_rider_count"] += parsed[
                    "comment_count"
                ]

                print(
                    "    成功:",
                    parsed["rider_count"],
                    "人",
                    "/ コメント:",
                    parsed["comment_status"],
                )

            except Exception as exc:
                result["races_failed"] += 1

                result["errors"].append({
                    "stage": "race_detail",
                    "venue": race.get("venue", ""),
                    "race_no": race.get("race_no", ""),
                    "error": (
                        f"{type(exc).__name__}: {exc}"
                    ),
                })

                print("    取得失敗:", exc)

            time.sleep(PAUSE)

        # ----------------------------------------------------
        # 4. 結果検証
        # ----------------------------------------------------

        print("[4/4] データを検証...")

        if result["races_fetched"] == 0:
            result["status"] = "error"

        elif result["races_failed"] > 0:
            result["status"] = "partial"

        elif result["races_date_unknown"] > 0:
            result["status"] = "partial"

        elif result["errors"]:
            result["status"] = "partial"

        else:
            result["status"] = "ok"

        # 会場ごとの取得数を記録する。
        fetched_by_venue = {}

        for race in result["races"]:
            venue = race["venue"]

            fetched_by_venue[venue] = (
                fetched_by_venue.get(venue, 0) + 1
            )

        result["summary"] = {
            "target_date": today.isoformat(),
            "fetched_by_venue": fetched_by_venue,
            "race_count": len(result["races"]),
            "rider_count": result["rider_count"],
            "commented_rider_count": (
                result["commented_rider_count"]
            ),
            "comment_missing_rider_count": max(
                0,
                result["rider_count"]
                - result["commented_rider_count"],
            ),
        }

        if not result["races"]:
            result["warnings"].append({
                "stage": "final_validation",
                "message": (
                    "対象日の出走表が0件です。"
                    "開催日程と日付抽出条件を確認してください。"
                ),
            })

    except Exception as exc:
        result["status"] = "error"

        result["errors"].append({
            "stage": "main",
            "error": f"{type(exc).__name__}: {exc}",
        })

    # --------------------------------------------------------
    # JSON保存
    # --------------------------------------------------------

    result["finished_at"] = now_jst().isoformat()

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("=" * 60)
    print("取得結果")
    print("状態:", result["status"])
    print("開催候補:", result["events_found"])
    print("レース候補:", result["races_found"])
    print("確認したレース:", result["races_checked"])
    print("当日分の取得成功:", result["races_fetched"])
    print("別日として除外:", result["races_excluded_other_date"])
    print("日付不明:", result["races_date_unknown"])
    print("取得失敗:", result["races_failed"])
    print("選手データ件数:", result["rider_count"])
    print("コメント取得件数:", result["commented_rider_count"])
    print("エラー件数:", len(result["errors"]))
    print("警告件数:", len(result["warnings"]))
    print("保存先:", OUTPUT)
    print("=" * 60)

    if result["errors"]:
        print(
            json.dumps(
                result["errors"],
                ensure_ascii=False,
                indent=2,
            )
        )

    if result["warnings"]:
        print(
            json.dumps(
                result["warnings"],
                ensure_ascii=False,
                indent=2,
            )
        )

    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()

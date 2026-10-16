#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

import requests

BASE = "https://keirin.jp"
API = f"{BASE}/sp/json"
SCHEDULE = f"{BASE}/sp/raceschedule"
JST = timezone(timedelta(hours=9))
TIMEOUT = 25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ja-JP,ja;q=0.9",
}


def get_json(session, params, referer):
    try:
        r = session.get(
            API,
            params=params,
            headers={"Referer": referer},
            timeout=TIMEOUT,
        )
        print("HTTP:", r.status_code)
        print("URL:", r.url)
        print("応答サイズ:", len(r.content))

        data = r.json()
        print("resultCd:", data.get("resultCd"))
        print("messageCd:", data.get("messageCd"))
        return data

    except Exception as e:
        print("取得失敗:", repr(e))
        return None


def inspect_page(session, url, referer):
    try:
        r = session.get(
            url,
            headers={"Referer": referer},
            timeout=TIMEOUT,
            allow_redirects=True,
        )

        print("HTTP:", r.status_code)
        print("要求URL:", url)
        print("最終URL:", r.url)
        print("サイズ:", len(r.content))

        if "/sp/error" in r.url:
            print("エラーページに転送されました")
            return None

        return r

    except Exception as e:
        print("ページ取得失敗:", repr(e))
        return None


def inspect_race_list(data):
    if not isinstance(data, dict):
        return []

    days = data.get("kaisaiDayList", [])
    races = []

    if not isinstance(days, list):
        return races

    for day_index, day in enumerate(days):
        if not isinstance(day, dict):
            continue

        programs = day.get("raceProgramList", [])

        if not isinstance(programs, list):
            continue

        for race in programs:
            if not isinstance(race, dict):
                continue

            link_param = str(
                race.get("strRaceLnkPrm", "")
            ).strip()

            if not link_param:
                continue

            item = {
                "day_index": day_index,
                "day_name": day.get("strKaisaiNitijName"),
                "race_no": race.get("strRaceNo"),
                "race_name": race.get("strSyumokuName"),
                "race_url": race.get("strRaceLnk"),
                "race_encp": link_param,
            }

            races.append(item)

    return races


def inspect_javascript(session, page_url):
    print()
    print("--- 出走表ページのJavaScript調査 ---")

    page = inspect_page(
        session,
        page_url,
        SCHEDULE,
    )

    if page is None:
        return

    scripts = re.findall(
        r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""",
        page.text,
        flags=re.IGNORECASE,
    )

    script_urls = []

    for src in scripts:
        url = urljoin(page.url, src)

        if url not in script_urls:
            script_urls.append(url)

    print("JavaScriptファイル数:", len(script_urls))

    keywords = (
        "JSJ049",
        "JSJ058",
        "JSJ041",
        "strRaceLnkPrm",
        "kaisaiDayList",
        "commonSubmit",
        "formPost",
    )

    for js_url in script_urls:
        try:
            r = session.get(
                js_url,
                headers={"Referer": page.url},
                timeout=TIMEOUT,
            )

            if not r.ok:
                continue

            source = r.text
            matches = [
                word
                for word in keywords
                if word.lower() in source.lower()
            ]

            if not matches:
                continue

            print()
            print("JS:", js_url)
            print("検出語:", ", ".join(matches))

            for word in matches:
                found = 0

                for match in re.finditer(
                    re.escape(word),
                    source,
                    flags=re.IGNORECASE,
                ):
                    start = max(0, match.start() - 250)
                    end = min(
                        len(source),
                        match.end() + 500,
                    )

                    print(
                        f"[{word}]",
                        source[start:end]
                        .replace("\r", " ")
                        .replace("\n", " ")[:800],
                    )

                    found += 1

                    if found >= 2:
                        break

        except Exception as e:
            print("JS取得失敗:", js_url, repr(e))


def inspect_schedule_html(session, html, events):
    print()
    print("=" * 64)
    print("開催一覧HTML内のパラメータ調査")
    print("=" * 64)

    for event in events:
        venue = event.get("jyoName", "")
        encp = str(event.get("encPrm", "")).strip()

        if not encp:
            continue

        print()
        print("開催場:", venue)
        print("encPrm:", encp)

        position = html.find(encp)

        if position < 0:
            print("開催用encPrmはHTML本文内に見つかりません")
            continue

        start = max(0, position - 500)
        end = min(len(html), position + len(encp) + 800)

        print("HTML内の周辺:")
        print(
            html[start:end]
            .replace("\r", " ")
            .replace("\n", " ")[:1300]
        )


def main():
    print("=" * 64)
    print("KEIRIN.JP 開催・出走表リンク調査")
    print("対象日:", datetime.now(JST).date())
    print("=" * 64)

    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        page = session.get(
            SCHEDULE,
            timeout=TIMEOUT,
        )
        page.raise_for_status()
    except Exception as e:
        print("日程ページ取得失敗:", repr(e))
        return

    print()
    print("[1] JSJ058 開催情報")

    schedule = get_json(
        session,
        {"type": "JSJ058"},
        page.url,
    )

    if not isinstance(schedule, dict):
        print("JSJ058を解析できません")
        return

    events = schedule.get("kInfo", [])

    if not isinstance(events, list):
        print("kInfoがリストではありません")
        return

    targets = {"奈良", "豊橋", "四日市"}

    selected = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("jyoName") in targets
    ]

    print("比較対象:", len(selected))

    inspect_schedule_html(
        session,
        page.text,
        selected,
    )

    for event in selected:
        venue = event.get("jyoName", "")
        encp = str(event.get("encPrm", "")).strip()

        print()
        print("=" * 64)
        print("開催場:", venue)
        print("=" * 64)

        print("開催情報:")
        print(json.dumps(
            event,
            ensure_ascii=False,
            indent=2,
        )[:4000])

        if not encp:
            print("encPrmなし")
            continue

        print()
        print("[2] JSJ049 開催レース一覧")

        data = get_json(
            session,
            {
                "type": "JSJ049",
                "encp": encp,
            },
            page.url,
        )

        races = inspect_race_list(data)

        print("取得できたレース数:", len(races))

        for race in races[:3]:
            print(json.dumps(
                race,
                ensure_ascii=False,
            ))

        if not races:
            print(
                "レースリンクを取得できません。"
                "この開催の調査はここで継続します。"
            )
            continue

        # 開催用encPrmではなく、レース一覧から得た
        # strRaceLnkPrmを出走表ページに使用する。
        first_race = races[0]
        race_encp = first_race["race_encp"]

        print()
        print("[3] 実際のレース用パラメータ")
        print("開催場:", venue)
        print("レース:", first_race["race_no"])
        print("race_encp:", race_encp)

        race_url = f"{BASE}/sp/race"

        from urllib.parse import urlencode

        actual_url = (
            race_url
            + "?"
            + urlencode({"encp": race_encp})
        )

        inspect_javascript(
            session,
            actual_url,
        )

        print()
        print("[4] 出走表HTMLの主要リンク")

        race_page = inspect_page(
            session,
            actual_url,
            page.url,
        )

        if race_page is not None:
            links = re.findall(
                r"""(?:href|action)\s*=\s*["']([^"']+)["']""",
                race_page.text,
                flags=re.IGNORECASE,
            )

            for link in links[:40]:
                print(urljoin(race_page.url, link))

    print()
    print("=" * 64)
    print("調査終了")
    print("=" * 64)


if __name__ == "__main__":
    main()

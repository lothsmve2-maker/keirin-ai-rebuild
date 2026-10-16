#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

import requests

BASE = "https://keirin.jp"
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


def show_json(data, limit=2500):
    text = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
    )
    print(text[:limit])


def request_json(session, method, url, **kwargs):
    try:
        response = session.request(
            method,
            url,
            timeout=TIMEOUT,
            **kwargs,
        )

        print("HTTP:", response.status_code)
        print("URL:", response.url)
        print(
            "Content-Type:",
            response.headers.get("Content-Type"),
        )
        print("Bytes:", len(response.content))

        try:
            payload = response.json()
        except ValueError:
            print("JSONではない応答:")
            print(response.text[:1000])
            return None

        if isinstance(payload, dict):
            print("resultCd:", payload.get("resultCd"))
            print("messageCd:", payload.get("messageCd"))
            print(
                "kaisaiDayList件数:",
                len(payload.get("kaisaiDayList", []))
                if isinstance(payload.get("kaisaiDayList"), list)
                else "対象外",
            )
        else:
            print("応答型:", type(payload).__name__)

        return payload

    except Exception as exc:
        print("通信エラー:", repr(exc))
        return None


def main():
    today = datetime.now(JST).date()

    print("=" * 64)
    print("KEIRIN.JP 開催情報リクエスト比較")
    print("対象日:", today.isoformat())
    print("=" * 64)

    session = requests.Session()
    session.headers.update(HEADERS)

    schedule_url = f"{BASE}/sp/raceschedule"
    api_url = f"{BASE}/sp/json"

    print()
    print("[1] 開催日程ページを取得")

    try:
        page = session.get(
            schedule_url,
            timeout=TIMEOUT,
        )
        print("HTTP:", page.status_code)
        print("URL:", page.url)
        page.raise_for_status()
    except Exception as exc:
        print("開催日程取得失敗:", repr(exc))
        return

    print()
    print("[2] JSJ058を取得")

    schedule = request_json(
        session,
        "GET",
        api_url,
        params={"type": "JSJ058"},
        headers={"Referer": page.url},
    )

    if not isinstance(schedule, dict):
        print("JSJ058の形式が不正です")
        return

    events = schedule.get("kInfo", [])

    if not isinstance(events, list):
        print("kInfoがリストではありません")
        show_json(schedule)
        return

    targets = {"奈良", "豊橋", "四日市"}
    selected = []

    for event in events:
        if not isinstance(event, dict):
            continue

        venue = str(event.get("jyoName", "")).strip()

        if venue not in targets:
            continue

        selected.append(event)

    print()
    print("比較対象開催:", len(selected))

    for event in selected:
        venue = str(event.get("jyoName", "")).strip()
        encp = str(event.get("encPrm", "")).strip()

        print()
        print("=" * 64)
        print("開催場:", venue)
        print("開催初日:", event.get("kStartDate"))
        print("表示レース番号:", event.get("raceNum"))
        print("開催場コード:", event.get("KeirinCd"))
        print("encPrm:", encp)
        print("イベント全キー:", list(event.keys()))
        print("=" * 64)

        if not encp:
            print("encPrmがないためスキップ")
            continue

        referer = schedule_url

        # --------------------------------------------------
        # 試験A：通常のGET
        # --------------------------------------------------
        print()
        print("[A] GET type=JSJ049, encp")

        request_json(
            session,
            "GET",
            api_url,
            params={
                "type": "JSJ049",
                "encp": encp,
            },
            headers={"Referer": referer},
        )

        # --------------------------------------------------
        # 試験B：POSTフォーム
        # --------------------------------------------------
        print()
        print("[B] POST type=JSJ049, encp")

        request_json(
            session,
            "POST",
            api_url,
            data={
                "type": "JSJ049",
                "encp": encp,
            },
            headers={
                "Referer": referer,
                "Origin": BASE,
                "Content-Type": (
                    "application/x-www-form-urlencoded"
                ),
            },
        )

        # --------------------------------------------------
        # 試験C：POSTでtypeをクエリに指定
        # --------------------------------------------------
        print()
        print("[C] POST query type + form encp")

        request_json(
            session,
            "POST",
            api_url,
            params={"type": "JSJ049"},
            data={"encp": encp},
            headers={
                "Referer": referer,
                "Origin": BASE,
            },
        )

        # --------------------------------------------------
        # 試験D：レースページへの遷移を確認
        # --------------------------------------------------
        print()
        print("[D] /sp/race に encp を指定")

        race_response = None

        try:
            race_response = session.get(
                f"{BASE}/sp/race",
                params={"encp": encp},
                headers={"Referer": referer},
                timeout=TIMEOUT,
                allow_redirects=True,
            )

            print("HTTP:", race_response.status_code)
            print("最終URL:", race_response.url)
            print("Bytes:", len(race_response.content))
            print(
                "Content-Type:",
                race_response.headers.get("Content-Type"),
            )

            if "/sp/error" in race_response.url:
                print("結果: エラーページへ転送")
                print("本文:")
                print(race_response.text[:800])
            else:
                print("結果: エラーURLへの転送なし")

        except Exception as exc:
            print("レースページ取得失敗:", repr(exc))

        # --------------------------------------------------
        # 試験E：JSJ058の開催場コードも比較
        # --------------------------------------------------
        print()
        print("[E] JSJ058の開催情報")

        fields = (
            "jyoName",
            "bKeirinCd",
            "KeirinCd",
            "kStartDate",
            "raceNum",
            "denTime",
            "denTitle",
            "btnFlgLive",
            "btnFlgOdds",
            "btnFlgResult",
            "hyoujiFlg",
            "zenjituFlg",
        )

        for field in fields:
            print(f"{field}: {event.get(field)}")

    print()
    print("=" * 64)
    print("全比較終了")
    print("=" * 64)


if __name__ == "__main__":
    main()

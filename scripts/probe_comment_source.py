import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://keirin.jp"
SCHEDULE_URL = BASE + "/sp/raceschedule"
SCHEDULE_API = BASE + "/sp/json?type=JSJ058"

OUT = Path("data/comment_source_diagnostic.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Mobile) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.5",
}


def save_report(report):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get(session, url, referer=None, timeout=25):
    headers = {}
    if referer:
        headers["Referer"] = referer

    response = session.get(
        url,
        headers=headers,
        timeout=timeout,
    )
    response.encoding = response.apparent_encoding
    return response


def find_first_event(data):
    """JSJ058から開催情報を探す。"""
    candidates = []

    def walk(obj):
        if isinstance(obj, dict):
            if (
                obj.get("encPrm")
                and obj.get("jyoName")
                and obj.get("kStartDate")
            ):
                candidates.append(obj)

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(data)

    today = time.strftime("%Y%m%d", time.gmtime(
        time.time() + 9 * 3600
    ))

    for event in candidates:
        if str(event.get("kStartDate", "")) == today:
            return event

    return candidates[0] if candidates else None


def find_today_race(session, event):
    """開催のレース一覧から当日レースのパラメータを探す。"""
    url = BASE + "/sp/json"
    params = {
        "type": "JSJ049",
        "encp": event["encPrm"],
    }

    response = session.get(
        url,
        params=params,
        headers={"Referer": SCHEDULE_URL},
        timeout=25,
    )
    response.raise_for_status()

    data = response.json()
    today = time.strftime("%Y%m%d", time.gmtime(
        time.time() + 9 * 3600
    ))

    races = []

    def walk(obj):
        if isinstance(obj, dict):
            race_param = (
                obj.get("strRaceLnkPrm")
                or obj.get("raceParam")
                or obj.get("encPrm")
            )

            if race_param and obj.get("strRaceNo"):
                races.append({
                    "race_no": str(obj.get("strRaceNo")),
                    "race_name": str(
                        obj.get("strSyumokuName", "")
                    ),
                    "day": str(obj.get("strKaisaiNitiji", "")),
                    "param": str(race_param),
                    "raw_keys": list(obj.keys()),
                })

            for value in obj.values():
                walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(data)

    # 取得できた開催データの構造も報告に残す。
    return response.url, data, races, today


def main():
    report = {
        "started_at": time.strftime(
            "%Y-%m-%dT%H:%M:%S%z"
        ),
        "status": "running",
        "schedule": {},
        "event": {},
        "race_list": {},
        "race_page": {},
        "javascript": [],
        "comment_matches": [],
        "forms": [],
        "errors": [],
    }

    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        landing = get(session, SCHEDULE_URL)
        report["schedule"] = {
            "status_code": landing.status_code,
            "url": landing.url,
            "bytes": len(landing.content),
        }
        landing.raise_for_status()

        schedule = get(
            session,
            SCHEDULE_API,
            referer=SCHEDULE_URL,
        )
        report["schedule"]["api_status"] = (
            schedule.status_code
        )
        schedule.raise_for_status()

        event_data = schedule.json()
        event = find_first_event(event_data)

        if not event:
            raise RuntimeError(
                "JSJ058から開催情報を検出できません"
            )

        report["event"] = {
            "venue": event.get("jyoName"),
            "start_date": event.get("kStartDate"),
            "encPrm": event.get("encPrm"),
        }

        list_url, list_data, races, today = (
            find_today_race(session, event)
        )

        report["race_list"] = {
            "url": list_url,
            "today": today,
            "race_count_detected": len(races),
            "sample_races": races[:10],
            "top_level_keys": (
                list(list_data.keys())
                if isinstance(list_data, dict)
                else []
            ),
        }

        # 既存の公式レース一覧形式からパラメータを探す。
        def collect_race_params(obj, found):
            if isinstance(obj, dict):
                param = obj.get("strRaceLnkPrm")
                race_no = obj.get("strRaceNo")

                if param and race_no:
                    found.append({
                        "param": str(param),
                        "race_no": str(race_no),
                        "race_name": str(
                            obj.get("strSyumokuName", "")
                        ),
                    })

                for value in obj.values():
                    collect_race_params(value, found)

            elif isinstance(obj, list):
                for value in obj:
                    collect_race_params(value, found)

        race_params = []
        collect_race_params(list_data, race_params)

        if not race_params:
            raise RuntimeError(
                "レースパラメータが見つかりません。"
                "race_listのraw構造を確認してください"
            )

        race = race_params[0]
        race_url = BASE + "/sp/race"

        page = session.get(
            race_url,
            params={"encp": race["param"]},
            headers={"Referer": SCHEDULE_URL},
            timeout=25,
        )
        page.encoding = page.apparent_encoding
        page.raise_for_status()

        html = page.text
        soup = BeautifulSoup(html, "html.parser")

        report["race_page"] = {
            "status_code": page.status_code,
            "url": page.url,
            "race_no": race["race_no"],
            "race_name": race["race_name"],
            "bytes": len(page.content),
            "has_comment_label": "選手コメント" in html,
            "has_SJ0315": "SJ0315" in html,
            "has_SJ0314": "SJ0314" in html,
        }

        # フォームのaction、method、hidden項目を記録。
        for form in soup.find_all("form"):
            report["forms"].append({
                "action": form.get("action", ""),
                "method": form.get("method", ""),
                "inputs": [
                    {
                        "name": item.get("name", ""),
                        "value": item.get("value", ""),
                        "type": item.get("type", ""),
                    }
                    for item in form.find_all("input")
                    if item.get("name")
                ][:60],
            })

        # ページ内のJSON代入からコメント関連の項目名を探す。
        patterns = [
            r"""jsonData\s*\[\s*["']([^"']+)["']\s*\]\s*=""",
            r"""["']([^"']*(?:comment|ikioi|jikou)[^"']*)["']\s*:""",
            r"""(?:comment|jikou|sensyuComment|選手コメント)""",
        ]

        for pattern in patterns:
            for match in re.finditer(
                pattern,
                html,
                flags=re.IGNORECASE,
            ):
                start = max(0, match.start() - 180)
                end = min(len(html), match.end() + 350)

                report["comment_matches"].append({
                    "pattern": pattern,
                    "match": match.group(0)[:180],
                    "context": html[start:end],
                })

                if len(report["comment_matches"]) >= 80:
                    break

        # 公式ページが参照するJavaScriptを実際に取得して調べる。
        script_urls = []
        for script in soup.find_all("script", src=True):
            script_urls.append(urljoin(
                page.url,
                script.get("src"),
            ))

        targets = [
            url for url in script_urls
            if re.search(
                r"(SJ0314|SJ0315|FSJ0315|SC0201)",
                url,
                flags=re.IGNORECASE,
            )
        ]

        # 同じURLは一度だけ取得。
        targets = list(dict.fromkeys(targets))

        keywords = re.compile(
            r"comment|jikou|sensyu|SJ0315|FSJ0315|"
            r"getRequest|post|ajax|jsonData|disp",
            re.IGNORECASE,
        )

        for js_url in targets:
            item = {
                "url": js_url,
                "status_code": None,
                "bytes": 0,
                "matches": [],
                "error": None,
            }

            try:
                js_response = session.get(
                    js_url,
                    headers={"Referer": page.url},
                    timeout=20,
                )
                item["status_code"] = (
                    js_response.status_code
                )
                item["bytes"] = len(js_response.content)

                if js_response.ok:
                    js_text = js_response.text
                    lines = js_text.splitlines()

                    for index, line in enumerate(lines):
                        if keywords.search(line):
                            item["matches"].append({
                                "line": index + 1,
                                "text": line.strip()[:500],
                            })

                        if len(item["matches"]) >= 40:
                            break

            except Exception as exc:
                item["error"] = str(exc)

            report["javascript"].append(item)

        report["status"] = "ok"

    except Exception as exc:
        report["status"] = "error"
        report["errors"].append(
            f"{type(exc).__name__}: {exc}"
        )

    finally:
        report["finished_at"] = time.strftime(
            "%Y-%m-%dT%H:%M:%S%z"
        )
        save_report(report)

        print("=" * 60)
        print(" KEIRIN.JP コメント取得元調査")
        print("=" * 60)
        print("状態:", report["status"])
        print("開催:", report.get("event", {}).get("venue"))
        print(
            "レースページ:",
            report.get("race_page", {}).get("status_code"),
        )
        print(
            "コメント欄:",
            report.get("race_page", {}).get(
                "has_comment_label"
            ),
        )
        print(
            "JavaScript調査数:",
            len(report["javascript"]),
        )

        for item in report["javascript"]:
            print(
                item["status_code"],
                item["bytes"],
                item["url"],
                "候補:", len(item["matches"]),
            )

        print("エラー:", len(report["errors"]))
        for error in report["errors"]:
            print(" ", error)

        print("保存先:", OUT)


if __name__ == "__main__":
    main()

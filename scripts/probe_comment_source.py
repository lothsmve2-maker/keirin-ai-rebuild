import json
import re
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://keirin.jp"
SCHEDULE_URL = BASE + "/sp/raceschedule"
SCHEDULE_API = BASE + "/sp/json?type=JSJ058"
OUTPUT = Path("data/comment_source_diagnostic.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; Mobile) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9",
}

# コメント欄の描画処理、通信先、データ項目を探す
KEYWORDS = re.compile(
    r"ryTbodyRaceInfo|選手コメント|comment|"
    r"jikou|sensyuComment|JSON_REQ_ID|"
    r"getRequestGet|getRequestPost|"
    r"formPost|ajax|XMLHttpRequest|"
    r"racerprofile|syusso|sensyuTypeInfo|"
    r"ryController|ryView|ryDrawJSONData|"
    r"disp.*SJ0315|SJ0315.*disp",
    re.IGNORECASE,
)


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def get(session, url, referer=None, timeout=25):
    headers = {}
    if referer:
        headers["Referer"] = referer
    response = session.get(
        url,
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    return response


def find_events(obj, result):
    if isinstance(obj, dict):
        if (
            obj.get("encPrm")
            and obj.get("jyoName")
            and obj.get("kStartDate")
        ):
            result.append(obj)

        for value in obj.values():
            find_events(value, result)

    elif isinstance(obj, list):
        for value in obj:
            find_events(value, result)


def find_races(obj, result):
    if isinstance(obj, dict):
        param = obj.get("strRaceLnkPrm")
        race_no = obj.get("strRaceNo")

        if param and race_no:
            result.append({
                "race_no": str(race_no),
                "race_name": str(
                    obj.get("strSyumokuName", "")
                ),
                "param": str(param),
            })

        for value in obj.values():
            find_races(value, result)

    elif isinstance(obj, list):
        for value in obj:
            find_races(value, result)


def fetch_js(item, referer):
    url = item["url"]
    result = {
        "url": url,
        "status_code": None,
        "bytes": 0,
        "matches": [],
        "error": None,
    }

    try:
        response = requests.get(
            url,
            headers={
                **HEADERS,
                "Referer": referer,
            },
            timeout=20,
        )
        result["status_code"] = response.status_code
        result["bytes"] = len(response.content)

        if response.ok:
            response.encoding = (
                response.apparent_encoding or "utf-8"
            )
            lines = response.text.splitlines()

            for i, line in enumerate(lines):
                if KEYWORDS.search(line):
                    result["matches"].append({
                        "line": i + 1,
                        "text": line.strip()[:1000],
                    })

            # コメント関連の処理が見つからないファイルも
            # ファイル名と取得結果は記録する。

    except Exception as exc:
        result["error"] = str(exc)

    return result


def main():
    report = {
        "started_at": now(),
        "status": "running",
        "event": {},
        "race": {},
        "page": {},
        "hidden_inputs": [],
        "script_urls": [],
        "inline_matches": [],
        "javascript": [],
        "errors": [],
    }

    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        # 公式開催ページを開いてセッションを初期化
        landing = get(session, SCHEDULE_URL)

        # 開催一覧を取得
        schedule_response = get(
            session,
            SCHEDULE_API,
            SCHEDULE_URL,
        )
        schedule = schedule_response.json()

        events = []
        find_events(schedule, events)

        today = time.strftime(
            "%Y%m%d",
            time.gmtime(time.time() + 9 * 3600),
        )

        event = next(
            (
                e for e in events
                if str(e.get("kStartDate", "")) == today
            ),
            None,
        )

        if not event:
            raise RuntimeError(
                f"本日 {today} 開始の開催を検出できません"
            )

        report["event"] = {
            "venue": event.get("jyoName"),
            "start_date": event.get("kStartDate"),
            "encPrm": event.get("encPrm"),
        }

        # 開催内のレース一覧を取得
        race_list_url = BASE + "/sp/json"
        race_response = session.get(
            race_list_url,
            params={
                "type": "JSJ049",
                "encp": event["encPrm"],
            },
            headers={"Referer": SCHEDULE_URL},
            timeout=25,
        )
        race_response.raise_for_status()
        race_data = race_response.json()

        races = []
        find_races(race_data, races)

        if not races:
            raise RuntimeError(
                "開催データからレースパラメータを検出できません"
            )

        # 最初のレースページを調査
        race = races[0]
        race_url = BASE + "/sp/race"

        page_response = session.get(
            race_url,
            params={"encp": race["param"]},
            headers={"Referer": SCHEDULE_URL},
            timeout=25,
        )
        page_response.raise_for_status()
        page_response.encoding = (
            page_response.apparent_encoding or "utf-8"
        )

        html = page_response.text
        soup = BeautifulSoup(html, "html.parser")

        report["race"] = {
            "race_no": race["race_no"],
            "race_name": race["race_name"],
            "param": race["param"],
        }

        report["page"] = {
            "status_code": page_response.status_code,
            "url": page_response.url,
            "bytes": len(page_response.content),
            "comment_label": "選手コメント" in html,
            "comment_table_empty": bool(
                re.search(
                    r'<tbody[^>]*id=["\']ryTbodyRaceInfo'
                    r'["\'][^>]*>\s*</tbody>',
                    html,
                    re.IGNORECASE,
                )
            ),
        }

        # フォームの外にあるhidden項目も含めて記録
        for node in soup.find_all("input"):
            name = node.get("name") or node.get("id") or ""
            value = node.get("value", "")

            if name and (
                node.get("type") == "hidden"
                or re.search(
                    r"url|post|disp|param|syori|race|ry",
                    name,
                    re.IGNORECASE,
                )
            ):
                report["hidden_inputs"].append({
                    "name": name,
                    "value": value[:500],
                    "type": node.get("type", ""),
                })

        # インラインJavaScript内のコメント関連処理を探す
        for script in soup.find_all("script"):
            if script.get("src"):
                continue

            source = script.string or script.get_text() or ""
            lines = source.splitlines()

            for i, line in enumerate(lines):
                if KEYWORDS.search(line):
                    report["inline_matches"].append({
                        "line": i + 1,
                        "text": line.strip()[:1000],
                    })

        # 外部JavaScriptを全件列挙
        js_items = []
        seen = set()

        for script in soup.find_all("script", src=True):
            js_url = urljoin(
                page_response.url,
                script.get("src"),
            )

            if js_url in seen:
                continue

            seen.add(js_url)
            js_items.append({"url": js_url})

        report["script_urls"] = [
            item["url"] for item in js_items
        ]

        # JavaScriptを並列取得して関連行を探す
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(
                    fetch_js,
                    item,
                    page_response.url,
                ): item
                for item in js_items
            }

            for future in as_completed(futures):
                report["javascript"].append(
                    future.result()
                )

        report["javascript"].sort(
            key=lambda item: item["url"]
        )

        report["status"] = "ok"

    except Exception as exc:
        report["status"] = "error"
        report["errors"].append(
            f"{type(exc).__name__}: {exc}"
        )

    finally:
        report["finished_at"] = now()
        OUTPUT.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        OUTPUT.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print("=" * 60)
        print(" KEIRIN.JP コメント通信調査")
        print("=" * 60)
        print("状態:", report["status"])
        print("開催:", report.get("event", {}).get("venue"))
        print("調査したJS:", len(report["script_urls"]))
        print("HTMLコメント欄:", report.get("page", {}).get(
            "comment_label"
        ))
        print("コメントテーブル空:", report.get("page", {}).get(
            "comment_table_empty"
        ))

        for item in report["javascript"]:
            if item["matches"] or item["error"]:
                print(
                    "\nJS:",
                    item["url"],
                    "HTTP:",
                    item["status_code"],
                    "関連行:",
                    len(item["matches"]),
                )

                for match in item["matches"][:8]:
                    print(
                        f"  L{match['line']}: "
                        f"{match['text'][:250]}"
                    )

                if item["error"]:
                    print("  エラー:", item["error"])

        print("\nエラー:", report["errors"])
        print("保存先:", OUTPUT)


if __name__ == "__main__":
    main()

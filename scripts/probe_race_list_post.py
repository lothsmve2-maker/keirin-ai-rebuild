import json
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = "https://keirin.jp"
ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "race_transition_diagnostic.json"

JST = ZoneInfo("Asia/Tokyo")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
}


def normalize(value):
    return re.sub(r"\s+", " ", value or "").strip()


def is_active_event(text, target):
    match = re.search(
        r"(\d{1,2})/(\d{1,2}).*?[～〜~\-－]\s*"
        r"(\d{1,2})/(\d{1,2})",
        text,
    )

    if not match:
        return False

    sm, sd, em, ed = map(int, match.groups())

    try:
        start_year = target.year
        end_year = target.year

        if sm > target.month + 1:
            start_year -= 1

        if em < sm:
            end_year += 1

        start = date(start_year, sm, sd)
        end = date(end_year, em, ed)

        return start <= target <= end

    except ValueError:
        return False


def main():
    now = datetime.now(JST)
    target = now.date()

    report = {
        "generated_at": now.isoformat(),
        "target_date": target.isoformat(),
        "status": "error",
        "events_found": 0,
        "race_list_pages_ok": 0,
        "total_race_numbers": 0,
        "total_rider_names": 0,
        "events": [],
    }

    session = requests.Session()
    session.headers.update(HEADERS)

    print(f"対象日: {target}", flush=True)

    # 1. 開催日程を取得
    response = session.get(
        f"{BASE}/sp/raceschedule",
        timeout=30,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding

    soup = BeautifulSoup(response.text, "html.parser")
    events = []
    seen = set()

    # 2. 公式ページの画面遷移パラメータを取得
    for element in soup.find_all(onclick=True):
        onclick = element.get("onclick", "")

        match = re.search(
            r"""formlnk\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)""",
            onclick,
        )

        if not match:
            continue

        endpoint, encp = match.groups()

        if endpoint != "/sp/racelist" or encp in seen:
            continue

        seen.add(encp)

        text = normalize(element.get_text(" ", strip=True))

        if not is_active_event(text, target):
            continue

        events.append({
            "event_text": text,
            "endpoint": endpoint,
            "encp": encp,
        })

    report["events_found"] = len(events)
    print(f"開催情報: {len(events)}件", flush=True)

    # 3. 実際のレース一覧を取得
    for event in events:
        item = {
            "event_text": event["event_text"],
            "http_status": None,
            "final_url": None,
            "title": "",
            "race_numbers": [],
            "rider_names": [],
            "body_sample": "",
            "error": None,
        }

        try:
            result = session.post(
                BASE + event["endpoint"],
                data={
                    "encp": event["encp"],
                    "disp": "SJ0301",
                },
                headers={
                    "Referer": f"{BASE}/sp/raceschedule",
                    "Origin": BASE,
                },
                timeout=30,
                allow_redirects=True,
            )
            result.encoding = result.apparent_encoding

            item["http_status"] = result.status_code
            item["final_url"] = result.url

            page = BeautifulSoup(
                result.text,
                "html.parser",
            )

            item["title"] = (
                page.title.get_text(" ", strip=True)
                if page.title else ""
            )

            body = normalize(page.get_text(" ", strip=True))
            item["body_sample"] = body[:3000]

            # レース番号の候補を抽出
            numbers = set()

            for element in page.find_all(
                ["a", "button", "td", "th", "span", "div"]
            ):
                text = normalize(element.get_text(" ", strip=True))

                if re.fullmatch(r"\d{1,2}R", text):
                    number = int(text[:-1])

                    if 1 <= number <= 12:
                        numbers.add(number)

            item["race_numbers"] = sorted(numbers)

            # 選手名候補を抽出
            names = set()

            for element in page.select(
                "[class*='racerName'], "
                "[class*='playerName'], "
                "[class*='sensyuName']"
            ):
                name = normalize(element.get_text(" ", strip=True))

                if 2 <= len(name) <= 20:
                    names.add(name)

            item["rider_names"] = sorted(names)

            if item["http_status"] == 200 and numbers:
                report["race_list_pages_ok"] += 1

            report["total_race_numbers"] += len(numbers)
            report["total_rider_names"] += len(names)

            print(
                f"{item['event_text'][:50]} | "
                f"HTTP {item['http_status']} | "
                f"レース番号 {len(numbers)}件 | "
                f"選手名候補 {len(names)}件",
                flush=True,
            )

        except Exception as exc:
            item["error"] = str(exc)
            print(f"取得エラー: {exc}", flush=True)

        report["events"].append(item)

    # 4. 結果を保存
    report["status"] = (
        "ok"
        if report["race_list_pages_ok"] > 0
        else "race_list_not_confirmed"
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"結果: {report['status']}", flush=True)
    print(f"保存先: {OUTPUT}", flush=True)


if __name__ == "__main__":
    main()

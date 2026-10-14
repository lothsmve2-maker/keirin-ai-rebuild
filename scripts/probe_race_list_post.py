import json
import re
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = "https://keirin.jp"
ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "race_transition_diagnostic.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
}

JST = ZoneInfo("Asia/Tokyo")


def log(message):
    print(message, flush=True)


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip()


def parse_event_date(text, target):
    """
    開催日程の表示例:
    10/14(水)～10/16(金)
    """
    match = re.search(
        r"(\d{1,2})/(\d{1,2}).*?[～〜\-－]\s*"
        r"(\d{1,2})/(\d{1,2})",
        text,
    )

    if not match:
        return False

    sm, sd, em, ed = map(int, match.groups())

    try:
        start = date(target.year, sm, sd)
        end_year = target.year

        if em < sm:
            end_year += 1

        end = date(end_year, em, ed)

        # 年をまたぐ開催に対応
        if start > target and start.year == target.year:
            start = date(target.year - 1, sm, sd)

        return start <= target <= end

    except ValueError:
        return False


def main():
    now = datetime.now(JST)
    target = now.date()

    report = {
        "generated_at": now.isoformat(),
        "target_date": target.isoformat(),
        "source": f"{BASE}/sp/raceschedule",
        "schedule_status": None,
        "active_event_count": 0,
        "results": [],
        "notes": [
            "開催日程ページのencpを使ってレース一覧を取得する。",
            "既存の予想・買い目・収支データは変更しない。",
        ],
    }

    session = requests.Session()
    session.headers.update(HEADERS)

    log("=" * 50)
    log("KEIRIN.JP 実レース一覧取得テスト")
    log(f"対象日: {target}")
    log("=" * 50)

    # 1. 公式開催日程を取得
    response = session.get(
        f"{BASE}/sp/raceschedule",
        timeout=30,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding

    report["schedule_status"] = response.status_code

    soup = BeautifulSoup(response.text, "html.parser")

    # 2. 開催日程に埋め込まれた公式の画面遷移情報を抽出
    events = []
    seen_tokens = set()

    for element in soup.find_all(onclick=True):
        onclick = element.get("onclick", "")

        match = re.search(
            r"formlnk\(\s*['\"]([^'\"]+)['\"]\s*,\s*"
            r"['\"]([^'\"]+)['\"]\s*\)",
            onclick,
        )

        if not match:
            continue

        endpoint, encp = match.groups()

        if encp in seen_tokens:
            continue

        seen_tokens.add(encp)

        # 表示要素の文字と親要素の文字から開催日を探す
        texts = []
        node = element

        for _ in range(5):
            if node is None:
                break

            text = normalize(node.get_text(" ", strip=True))

            if text and text not in texts:
                texts.append(text)

            node = node.parent

        event_text = next(
            (
                text for text in texts
                if parse_event_date(text, target)
            ),
            "",
        )

        if not event_text:
            continue

        events.append({
            "endpoint": endpoint,
            "encp": encp,
            "event_text": event_text[:500],
        })

    report["active_event_count"] = len(events)

    log(f"本日該当の開催情報: {len(events)}件")

    # 3. 各開催のレース一覧を取得
    for index, event in enumerate(events, 1):
        endpoint = event["endpoint"]

        if not endpoint.startswith("/"):
            log(f"対象外URL: {endpoint}")
            continue

        url = BASE + endpoint

        item = {
            "event_text": event["event_text"],
            "request_url": url,
            "http_status": None,
            "final_url": None,
            "title": "",
            "race_numbers": [],
            "rider_names": [],
            "body_sample": "",
            "error": None,
        }

        log(f"\n開催 {index}/{len(events)}")
        log(event["event_text"][:100])

        try:
            result = session.post(
                url,
                data={
                    "encp": event["encp"],
                    "disp": "SJ0301",
                },
                headers={
                    "Referer": f"{BASE}/sp/raceschedule",
                    "Origin": BASE,
                    "Content-Type": (
                        "application/x-www-form-urlencoded"
                    ),
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
            item["body_sample"] = body[:2500]

            # ページに実際に表示されたレース番号を検出
            race_numbers = set()

            for element in page.find_all(
                ["a", "button", "td", "th", "span", "div"]
            ):
                text = normalize(element.get_text(" ", strip=True))

                if re.fullmatch(r"\d{1,2}R", text):
                    number = int(text[:-1])

                    if 1 <= number <= 12:
                        race_numbers.add(number)

            item["race_numbers"] = sorted(race_numbers)

            # 選手名候補。実際の出走表かどうかの確認に使用
            names = set()

            for element in page.select(
                ".racerName, .playerName, .sensyuName, "
                "[class*='racer'], [class*='sensyu']"
            ):
                name = normalize(element.get_text(" ", strip=True))

                if 2 <= len(name) <= 20:
                    names.add(name)

            item["rider_names"] = sorted(names)

            log(f"HTTP: {item['http_status']}")
            log(f"ページ: {item['title']}")
            log(f"レース番号: {item['race_numbers']}")
            log(f"選手名候補: {len(item['rider_names'])}件")

        except Exception as exc:
            item["error"] = str(exc)
            log(f"取得エラー: {exc}")

        report["results"].append(item)

    # 4. 結果を保存
    successful = [
        item for item in report["results"]
        if item["http_status"] == 200
        and item["race_numbers"]
    ]

    report["successful_race_list_pages"] = len(successful)
    report["status"] = (
        "ok" if successful else "race_list_not_confirmed"
    )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    OUTPUT.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    log("\n" + "=" * 50)
    log(f"開催情報: {len(events)}件")
    log(f"レース一覧を確認できたページ: {len(successful)}件")
    log(f"保存先: {OUTPUT}")
    log("=" * 50)


if __name__ == "__main__":
    main()

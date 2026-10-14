#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""KEIRIN.JPのレース一覧取得先を検証する。"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from html.parser import HTMLParser


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

INPUT_FILE = DATA_DIR / "today_schedule.json"
OUTPUT_FILE = DATA_DIR / "race_endpoint_probe.json"

BASE_URL = "https://keirin.jp"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "ja,en-US;q=0.8",
}


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.title_parts = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        text = " ".join(data.split())

        if text:
            self.parts.append(text)

            if self.in_title:
                self.title_parts.append(text)


def fetch(url):
    request = urllib.request.Request(url, headers=HEADERS)

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(1_500_001)
            truncated = len(raw) > 1_500_000
            raw = raw[:1_500_000]

            charset = response.headers.get_content_charset() or "utf-8"

            try:
                body = raw.decode(charset, errors="replace")
            except LookupError:
                body = raw.decode("utf-8", errors="replace")

            return {
                "requested_url": url,
                "final_url": response.geturl(),
                "status_code": response.status,
                "content_type": response.headers.get(
                    "Content-Type", ""
                ),
                "bytes": len(raw),
                "truncated": truncated,
                "body": body,
                "error": None,
            }

    except urllib.error.HTTPError as exc:
        return {
            "requested_url": url,
            "final_url": exc.geturl(),
            "status_code": exc.code,
            "content_type": exc.headers.get("Content-Type", ""),
            "bytes": 0,
            "truncated": False,
            "body": "",
            "error": f"HTTP {exc.code}: {exc.reason}",
        }

    except Exception as exc:
        return {
            "requested_url": url,
            "final_url": None,
            "status_code": None,
            "content_type": "",
            "bytes": 0,
            "truncated": False,
            "body": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def summarize(result):
    body = result.pop("body", "")

    parser = TextParser()

    try:
        parser.feed(body)
    except Exception:
        pass

    text = " ".join(parser.parts)

    race_numbers = sorted(
        set(
            re.findall(
                r"(?<!\d)(?:[1-9]|1[0-2])\s*R(?![A-Za-z])",
                text,
                re.IGNORECASE,
            )
        )
    )

    rider_number_mentions = sorted(
        set(
            re.findall(
                r"(?:車番|車)\s*[1-9]",
                text,
            )
        )
    )

    result["page_title"] = " ".join(parser.title_parts)[:200]
    result["text_length"] = len(text)
    result["text_preview"] = text[:1800]
    result["race_number_mentions"] = race_numbers
    result["rider_number_mentions"] = rider_number_mentions

    # 取得結果がHTMLなのかJSONなのか判断するための先頭部分。
    result["body_preview"] = body[:500]

    return result


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"開催日程ファイルがありません: {INPUT_FILE}"
        )

    with INPUT_FILE.open("r", encoding="utf-8") as file:
        schedule = json.load(file)

    target_date = schedule.get("target_date")

    if not target_date:
        raise ValueError("today_schedule.json に target_date がありません")

    date_value = target_date.replace("-", "")

    venues = schedule.get("active_today", [])

    if not venues:
        raise ValueError(
            "today_schedule.json に active_today がありません"
        )

    print("=" * 48)
    print("KEIRIN.JP RACE ENDPOINT PROBE")
    print("=" * 48)
    print(f"対象日: {target_date}")
    print(f"開催場: {len(venues)}場")

    results = []

    for venue in venues:
        venue_code = str(venue.get("venue_code", "")).strip()
        venue_name = venue.get("venue_name", "")

        if not venue_code:
            continue

        params = urllib.parse.urlencode({
            "jocd": venue_code,
            "kday": date_value,
        })

        candidates = [
            ("racelist", f"{BASE_URL}/sp/racelist?{params}"),
            ("race", f"{BASE_URL}/sp/race?{params}"),
            ("jyosellinfo", f"{BASE_URL}/sp/jyosellinfo?{params}"),
        ]

        for endpoint_name, url in candidates:
            print(f"GET {venue_name} {endpoint_name}: {url}")

            response = fetch(url)
            response["venue_code"] = venue_code
            response["venue_name"] = venue_name
            response["endpoint_candidate"] = endpoint_name

            results.append(summarize(response))

            time.sleep(0.2)

    successful = [
        item for item in results
        if item.get("status_code") == 200
        and item.get("text_length", 0) > 0
    ]

    output = {
        "generated_at": datetime.now(
            ZoneInfo("Asia/Tokyo")
        ).isoformat(),
        "target_date": target_date,
        "status": "ok",
        "purpose": (
            "公式サイトのレース一覧・基本情報の取得先候補を検証する"
        ),
        "candidate_parameters": {
            "venue_code": "jocd",
            "date": "kday",
            "date_format": "YYYYMMDD",
        },
        "total_requests": len(results),
        "successful_http_responses": len(successful),
        "results": results,
        "note": (
            "HTTP 200だけでは正しいレースデータを取得できたとは限らない。"
            "ページ本文、最終URL、レース番号の有無を確認して判定する。"
        ),
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    temporary = OUTPUT_FILE.with_suffix(".json.tmp")

    with temporary.open("w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)
        file.write("\n")

    temporary.replace(OUTPUT_FILE)

    print("-" * 48)
    print(f"検証リクエスト: {len(results)}")
    print(f"HTTP 200かつ本文あり: {len(successful)}")
    print(f"結果: {OUTPUT_FILE.relative_to(ROOT)}")
    print("完了")


if __name__ == "__main__":
    main()

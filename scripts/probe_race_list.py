import json
import re
import sys
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

SCHEDULE_FILE = DATA_DIR / "today_schedule.json"
OUTPUT_FILE = DATA_DIR / "race_list_probe.json"

JST = ZoneInfo("Asia/Tokyo")

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Mobile Safari/537.36"
)

# 現時点で確認対象とする公式ページ
SOURCES = [
    "https://keirin.jp/sp/top",
    "https://keirin.jp/sp/raceschedule",
]


def now_jst():
    return datetime.now(JST)


def normalize(value):
    value = str(value or "")
    value = value.replace("\u3000", " ")
    value = value.replace("\xa0", " ")
    return " ".join(value.split()).strip()


def fetch_html(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:
        status = response.status
        raw = response.read()
        charset = (
            response.headers.get_content_charset()
            or "utf-8"
        )

    if status != 200:
        raise RuntimeError(
            f"HTTPステータス異常: {status}"
        )

    html = raw.decode(
        charset,
        errors="replace",
    )

    if not html.strip():
        raise RuntimeError("HTMLが空です")

    return html, status, len(raw)


class LinkParser(HTMLParser):
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)

        self.base_url = base_url
        self.links = []
        self.current_link = None

        self.scripts = []
        self.stylesheets = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()

        if tag == "a":
            href = attrs.get("href")

            if href:
                self.current_link = {
                    "url": urljoin(
                        self.base_url,
                        href,
                    ),
                    "text": "",
                    "title": attrs.get("title"),
                }

        elif tag == "script":
            src = attrs.get("src")

            if src:
                self.scripts.append(
                    urljoin(self.base_url, src)
                )

        elif tag == "link":
            rel = attrs.get("rel", "").lower()
            href = attrs.get("href")

            if (
                href
                and "stylesheet" in rel
            ):
                self.stylesheets.append(
                    urljoin(self.base_url, href)
                )

    def handle_data(self, data):
        if self.current_link is not None:
            self.current_link["text"] += (
                " " + normalize(data)
            )

    def handle_endtag(self, tag):
        if (
            tag.lower() == "a"
            and self.current_link is not None
        ):
            self.current_link["text"] = normalize(
                self.current_link["text"]
            )

            self.links.append(self.current_link)
            self.current_link = None


def load_schedule():
    if not SCHEDULE_FILE.exists():
        raise FileNotFoundError(
            "data/today_schedule.json がありません。"
            "先に fetch_schedule.py を実行してください。"
        )

    data = json.loads(
        SCHEDULE_FILE.read_text(
            encoding="utf-8"
        )
    )

    active = data.get("active_today")

    if not isinstance(active, list) or not active:
        raise ValueError(
            "today_schedule.json の active_today が空です"
        )

    return data, active


def classify_link(link):
    url = link["url"]
    text = link["text"]

    parsed = urlparse(url)

    path = parsed.path.lower()
    query = parsed.query.lower()

    combined = f"{path} {query} {text}".lower()

    # レース・出走表・開催情報らしいURLを抽出する。
    # これは候補抽出であり、実際の用途は後で検証する。
    patterns = [
        r"race",
        r"entry",
        r"card",
        r"kaisai",
        r"jocd",
        r"race_no",
        r"raceno",
        r"競走",
        r"出走",
    ]

    matched = [
        pattern
        for pattern in patterns
        if re.search(pattern, combined, re.IGNORECASE)
    ]

    if not matched:
        return None

    return {
        "url": url,
        "text": text,
        "title": link.get("title"),
        "matched_patterns": matched,
        "host": parsed.netloc,
        "path": parsed.path,
        "query": parsed.query,
    }


def inspect_source(url, active_venues):
    print()
    print("-" * 55)
    print("調査URL:", url)

    result = {
        "source_url": url,
        "http_status": None,
        "body_bytes": 0,
        "error": None,
        "links": [],
        "race_candidates": [],
        "scripts": [],
        "stylesheets": [],
        "venue_name_matches": [],
    }

    try:
        html, status, body_bytes = fetch_html(url)

        result["http_status"] = status
        result["body_bytes"] = body_bytes

        parser = LinkParser(url)
        parser.feed(html)
        parser.close()

        result["links"] = parser.links
        result["scripts"] = parser.scripts
        result["stylesheets"] = parser.stylesheets

        candidates = []

        for link in parser.links:
            candidate = classify_link(link)

            if candidate:
                candidates.append(candidate)

        result["race_candidates"] = candidates

        for venue in active_venues:
            venue_name = normalize(
                venue.get("venue_name", "")
            )

            if not venue_name:
                continue

            found = []

            for link in parser.links:
                if venue_name in link["text"]:
                    found.append({
                        "url": link["url"],
                        "text": link["text"],
                    })

            if found:
                result["venue_name_matches"].append({
                    "venue_name": venue_name,
                    "links": found,
                })

        print("HTTP:", status)
        print("HTMLサイズ:", body_bytes, "bytes")
        print("リンク数:", len(parser.links))
        print("レース関連候補:", len(candidates))
        print("JavaScript:", len(parser.scripts))
        print("CSS:", len(parser.stylesheets))

        for candidate in candidates[:20]:
            print(
                "  候補:",
                candidate["text"][:50],
                candidate["url"],
            )

        if not candidates:
            print(
                "  レース関連リンクなし。"
                "JavaScript等による生成の可能性を調査します。"
            )

    except Exception as exc:
        result["error"] = str(exc)

        print("取得エラー:", exc)

    return result


def main():
    print("=" * 55)
    print("KEIRIN AI RACE LIST SOURCE PROBE")
    print("=" * 55)

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        schedule, active_venues = load_schedule()

        print("対象日:", schedule.get("target_date"))
        print("本日開催:", len(active_venues), "場")

        for venue in active_venues:
            print(
                " ",
                venue.get("venue_name"),
                venue.get("start_date"),
                "～",
                venue.get("end_date"),
            )

    except Exception as exc:
        print("開催日程の読込失敗:", exc)
        sys.exit(1)

    source_results = []

    for url in SOURCES:
        source_results.append(
            inspect_source(
                url,
                active_venues,
            )
        )

    output = {
        "generated_at": now_jst().isoformat(
            timespec="seconds"
        ),
        "target_date": schedule.get("target_date"),
        "status": (
            "ok"
            if any(
                item["http_status"] == 200
                for item in source_results
            )
            else "error"
        ),
        "active_venues": active_venues,
        "sources": source_results,
        "notes": [
            "このプログラムはURLとHTML構造の調査用です。",
            "レース情報の取得成功を保証するものではありません。",
            "実際のURLとページ構造を確認してから本番処理を実装します。",
        ],
    }

    temporary_file = OUTPUT_FILE.with_suffix(".tmp")

    temporary_file.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    json.loads(
        temporary_file.read_text(
            encoding="utf-8"
        )
    )

    temporary_file.replace(OUTPUT_FILE)

    print()
    print("=" * 55)
    print("調査完了")
    print("保存先:", OUTPUT_FILE)
    print("結果:", output["status"])
    print("=" * 55)


if __name__ == "__main__":
    main()

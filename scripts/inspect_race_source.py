#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KEIRIN.JP レース情報取得先の調査ツール

目的:
- 公式サイトのページとJavaScriptを取得する
- レース一覧・出走表・開催情報の取得先候補を調査する
- 調査結果を data/race_source_inspection.json に保存する

既存の fetch_schedule.py と today_schedule.json は変更しない。
"""

import json
import re
import sys
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
SCHEDULE_FILE = DATA_DIR / "today_schedule.json"
OUTPUT_FILE = DATA_DIR / "race_source_inspection.json"

BASE_URL = "https://keirin.jp"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml,"
        "application/json,text/javascript,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
}

PAGES = [
    "https://keirin.jp/sp/raceschedule",
    "https://keirin.jp/sp/top",
    "https://keirin.jp/pc/raceschedule",
    "https://keirin.jp/pc/top",
]

FIXED_SCRIPTS = [
    "https://keirin.jp/sp/static/js/SJ0201.js",
    "https://keirin.jp/sp/static/js/SJ0201_v.js",
    "https://keirin.jp/sp/static/js/SJ0201_c.js",
    "https://keirin.jp/sp/static/js/bopen.js",
    "https://keirin.jp/sp/static/js/SJ0101.js",
    "https://keirin.jp/sp/static/js/SJ0101_v.js",
    "https://keirin.jp/sp/static/js/SJ0101_c.js",
    "https://keirin.jp/sp/static/js/commonJSON.js",
    "https://keirin.jp/sp/static/js/commonLoad.js",
    "https://keirin.jp/sp/static/js/commonLink.js",
    "https://keirin.jp/pc/static/js/SJ0201.js",
    "https://keirin.jp/pc/static/js/SJ0101.js",
]

MAX_BODY_BYTES = 2_500_000
MAX_SNIPPETS_PER_FILE = 35
MAX_SNIPPET_LENGTH = 650

KEYWORDS = [
    "jocd",
    "kaisai",
    "kaisaiBi",
    "kaisaiDate",
    "race",
    "race_no",
    "raceno",
    "raceNo",
    "raceNum",
    "raceNumber",
    "entry",
    "shutsuba",
    "card",
    "SJ0201",
    "SJ0101",
    "XMLHttpRequest",
    "ajax",
    "fetch(",
    "location.href",
    "location.replace",
    "window.open",
    "action",
    ".do",
    ".json",
    ".xml",
    "application/json",
    "api/",
    "json/",
]

URL_RE = re.compile(
    r"""(?:
        https?://[^\s"'<>`]+
        |
        /[A-Za-z0-9_./?&=%+#:@~-]*(?:\.do|\.json|\.xml|\.html)?
          [A-Za-z0-9_./?&=%+#:@~-]*
    )""",
    re.IGNORECASE | re.VERBOSE,
)

JS_SRC_RE = re.compile(
    r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""",
    re.IGNORECASE,
)


def now_jst():
    return datetime.now(ZoneInfo("Asia/Tokyo")).isoformat()


def fetch_url(url, referer=None):
    """URLを取得。失敗しても処理全体は停止しない。"""
    headers = dict(HEADERS)

    if referer:
        headers["Referer"] = referer

    request = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(MAX_BODY_BYTES + 1)
            truncated = len(raw) > MAX_BODY_BYTES
            raw = raw[:MAX_BODY_BYTES]

            charset = response.headers.get_content_charset() or "utf-8"

            try:
                body = raw.decode(charset, errors="replace")
            except LookupError:
                body = raw.decode("utf-8", errors="replace")

            return {
                "url": url,
                "final_url": response.geturl(),
                "status_code": response.status,
                "content_type": response.headers.get(
                    "Content-Type", ""
                ),
                "bytes_read": len(raw),
                "truncated": truncated,
                "body": body,
                "error": None,
            }

    except urllib.error.HTTPError as exc:
        return {
            "url": url,
            "final_url": exc.geturl(),
            "status_code": exc.code,
            "content_type": exc.headers.get("Content-Type", ""),
            "bytes_read": 0,
            "truncated": False,
            "body": "",
            "error": f"HTTPError: {exc.code} {exc.reason}",
        }

    except Exception as exc:
        return {
            "url": url,
            "final_url": None,
            "status_code": None,
            "content_type": "",
            "bytes_read": 0,
            "truncated": False,
            "body": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


class PageParser(HTMLParser):
    """HTMLからリンク、フォーム、入力項目、onclickを抽出する。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.forms = []
        self.inputs = []
        self.buttons = []
        self.scripts = []
        self._current_form = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag.lower() == "script":
            src = attrs.get("src")
            if src:
                self.scripts.append(src)

        if tag.lower() == "a":
            self.links.append({
                "href": attrs.get("href"),
                "text": "",
                "onclick": attrs.get("onclick"),
                "class": attrs.get("class"),
            })
            self._current_link = self.links[-1]
        else:
            self._current_link = None

        if tag.lower() == "form":
            form = {
                "action": attrs.get("action"),
                "method": attrs.get("method", "get"),
                "name": attrs.get("name"),
                "id": attrs.get("id"),
            }
            self.forms.append(form)
            self._current_form = form

        if tag.lower() in ("input", "select", "textarea"):
            self.inputs.append({
                "tag": tag.lower(),
                "name": attrs.get("name"),
                "id": attrs.get("id"),
                "type": attrs.get("type"),
                "value": attrs.get("value"),
            })

        if tag.lower() in ("button", "area"):
            self.buttons.append({
                "tag": tag.lower(),
                "text": "",
                "onclick": attrs.get("onclick"),
                "href": attrs.get("href"),
                "name": attrs.get("name"),
                "value": attrs.get("value"),
                "class": attrs.get("class"),
            })
            self._current_button = self.buttons[-1]
        else:
            self._current_button = None

    def handle_endtag(self, tag):
        if tag.lower() == "form":
            self._current_form = None

        if tag.lower() == "a":
            self._current_link = None

        if tag.lower() in ("button", "area"):
            self._current_button = None

    def handle_data(self, data):
        text = " ".join(data.split())

        if not text:
            return

        if getattr(self, "_current_link", None) is not None:
            link = self._current_link
            link["text"] = (link.get("text", "") + " " + text).strip()[:250]

        if getattr(self, "_current_button", None) is not None:
            button = self._current_button
            button["text"] = (
                button.get("text", "") + " " + text
            ).strip()[:250]


def resolve_url(base, value):
    if not value:
        return None

    return urllib.parse.urljoin(base, value.strip())


def inspect_html(url, result):
    body = result.get("body", "")

    parser = PageParser()

    try:
        parser.feed(body)
    except Exception:
        pass

    relevant_links = []

    for link in parser.links:
        combined = " ".join([
            str(link.get("href") or ""),
            str(link.get("text") or ""),
            str(link.get("onclick") or ""),
        ]).lower()

        if any(word.lower() in combined for word in [
            "race", "kaisai", "jocd", "entry", "card",
            "shutsuba", "result", "schedule", "racedetail",
        ]):
            item = dict(link)
            item["absolute_url"] = resolve_url(
                result.get("final_url") or url,
                link.get("href"),
            )
            relevant_links.append(item)

    onclick_candidates = []

    for button in parser.buttons:
        combined = " ".join([
            str(button.get("text") or ""),
            str(button.get("onclick") or ""),
            str(button.get("href") or ""),
            str(button.get("class") or ""),
        ]).lower()

        if any(word.lower() in combined for word in [
            "race", "kaisai", "jocd", "entry", "card",
            "shutsuba", "result", "schedule",
        ]):
            onclick_candidates.append(button)

    return {
        "url": url,
        "final_url": result.get("final_url"),
        "status_code": result.get("status_code"),
        "content_type": result.get("content_type"),
        "bytes_read": result.get("bytes_read"),
        "error": result.get("error"),
        "script_urls": [
            resolve_url(result.get("final_url") or url, item)
            for item in parser.scripts
        ],
        "links_total": len(parser.links),
        "relevant_links": relevant_links[:100],
        "forms": parser.forms[:50],
        "inputs": parser.inputs[:100],
        "button_candidates": onclick_candidates[:100],
    }


def inspect_javascript(url, result):
    body = result.get("body", "")

    snippets = []
    seen = set()

    for match in re.finditer(
        r"(?i)(jocd|kaisai|raceno|race_no|racenum|"
        r"shutsuba|entry|ajax|xmlhttprequest|"
        r"location\.href|location\.replace|window\.open|"
        r"fetch\s*\(|\.do\b|\.json\b|api/|SJ0201|SJ0101)",
        body,
    ):
        start = max(0, match.start() - 180)
        end = min(len(body), match.end() + 300)
        snippet = body[start:end]
        snippet = " ".join(snippet.split())

        if snippet not in seen:
            seen.add(snippet)
            snippets.append({
                "keyword": match.group(0),
                "context": snippet[:MAX_SNIPPET_LENGTH],
            })

        if len(snippets) >= MAX_SNIPPETS_PER_FILE:
            break

    urls = []

    for match in URL_RE.finditer(body):
        value = match.group(0).rstrip(");,]}")

        if value not in urls:
            urls.append(value)

        if len(urls) >= 100:
            break

    return {
        "url": url,
        "final_url": result.get("final_url"),
        "status_code": result.get("status_code"),
        "content_type": result.get("content_type"),
        "bytes_read": result.get("bytes_read"),
        "truncated": result.get("truncated"),
        "error": result.get("error"),
        "url_literals": urls,
        "keyword_snippets": snippets,
    }


def load_schedule():
    if not SCHEDULE_FILE.exists():
        raise FileNotFoundError(
            f"開催日程ファイルがありません: {SCHEDULE_FILE}"
        )

    with SCHEDULE_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    venues = data.get("active_today", [])

    return {
        "target_date": data.get("target_date"),
        "active_venue_count": len(venues),
        "active_venues": [
            {
                "venue_code": item.get("venue_code"),
                "venue_name": item.get("venue_name"),
                "event_name": item.get("event_name"),
                "start_date": item.get("start_date"),
                "end_date": item.get("end_date"),
            }
            for item in venues
        ],
    }


def safe_save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")

    with temporary.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")

    temporary.replace(path)


def main():
    print("=" * 48)
    print("KEIRIN.JP RACE SOURCE INSPECTION")
    print("=" * 48)

    schedule = load_schedule()

    print(f"対象日: {schedule['target_date']}")
    print(f"開催場: {schedule['active_venue_count']}場")
    print("公式ページを調査します...")

    page_results = []
    discovered_scripts = []

    for url in PAGES:
        print(f"PAGE GET: {url}")
        result = fetch_url(url)

        page_info = inspect_html(url, result)
        page_results.append(page_info)

        for script_url in page_info["script_urls"]:
            if script_url and script_url.startswith("https://keirin.jp/"):
                if script_url not in discovered_scripts:
                    discovered_scripts.append(script_url)

        time.sleep(0.2)

    script_targets = list(FIXED_SCRIPTS)

    # HTML内で実際に参照されていたスクリプトも追加する。
    for url in discovered_scripts:
        if url not in script_targets:
            script_targets.append(url)

    js_results = []

    for index, url in enumerate(script_targets, start=1):
        print(f"JS GET {index}/{len(script_targets)}: {url}")

        result = fetch_url(url, referer="https://keirin.jp/sp/raceschedule")
        js_results.append(inspect_javascript(url, result))

        time.sleep(0.15)

    # レース情報に関連しそうなスクリプトを優先して抽出。
    relevant_scripts = []

    for item in js_results:
        joined = json.dumps(item, ensure_ascii=False).lower()

        score = 0
        for term in [
            "jocd", "kaisai", "raceno", "race_no", "shutsuba",
            "entry", "xmlhttprequest", "ajax", ".do", ".json",
            "sj0201", "sj0101",
        ]:
            if term in joined:
                score += 1

        relevant_scripts.append({
            "url": item.get("url"),
            "status_code": item.get("status_code"),
            "bytes_read": item.get("bytes_read"),
            "error": item.get("error"),
            "relevance_score": score,
            "url_literals": item.get("url_literals", []),
            "keyword_snippets": item.get("keyword_snippets", []),
        })

    relevant_scripts.sort(
        key=lambda item: item.get("relevance_score", 0),
        reverse=True,
    )

    output = {
        "generated_at": now_jst(),
        "target_date": schedule["target_date"],
        "status": "ok",
        "purpose": (
            "公式サイトのレース一覧・出走表データ取得先を特定するための調査"
        ),
        "schedule": schedule,
        "pages": page_results,
        "javascript_files_checked": len(js_results),
        "javascript": js_results,
        "relevant_scripts_ranked": relevant_scripts,
        "next_step": (
            "取得先候補を確認し、実際にレース一覧を返すURLを検証してから"
            "fetch_race_list.pyを作成する"
        ),
    }

    safe_save_json(OUTPUT_FILE, output)

    print("-" * 48)
    print(f"調査したJS: {len(js_results)}件")
    print(f"結果保存: {OUTPUT_FILE.relative_to(ROOT)}")
    print("完了")
    print("=" * 48)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise

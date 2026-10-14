import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
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
        "Chrome/125.0.0.0 Mobile Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
}

# JSON通信・Ajax・画面遷移・データ取得の手掛かりを広く探す
PATTERN = re.compile(
    r"ajax|fetch\s*\(|XMLHttpRequest|"
    r"\.post\s*\(|\.get\s*\(|"
    r"commonJSON|commonSubmit|"
    r"JSON\.parse|JSON\.stringify|"
    r"contentType|dataType|"
    r"encp|encPrm|"
    r"/sp/json|/json\?|"
    r"JSJ\d+|SJ\d{4}|"
    r"racelist|raceInfo|raceList|"
    r"syaban|sensyu|player|entry",
    re.IGNORECASE,
)


def log(message):
    print(message, flush=True)


def inspect_page(session, name, url):
    result = {
        "name": name,
        "requested_url": url,
        "status": None,
        "final_url": None,
        "title": "",
        "scripts": [],
        "forms": [],
        "links": [],
        "error": None,
    }

    try:
        response = session.get(url, timeout=30)
        response.encoding = response.apparent_encoding

        result["status"] = response.status_code
        result["final_url"] = response.url

        soup = BeautifulSoup(response.text, "html.parser")
        result["title"] = (
            soup.title.get_text(" ", strip=True)
            if soup.title else ""
        )

        for tag in soup.find_all("script"):
            src = tag.get("src")

            if src:
                result["scripts"].append(urljoin(response.url, src))

        for form in soup.find_all("form"):
            result["forms"].append({
                "action": urljoin(
                    response.url,
                    form.get("action", "")
                ),
                "method": form.get("method", "get").upper(),
                "fields": [
                    {
                        "name": field.get("name", ""),
                        "value": field.get("value", ""),
                        "type": field.get("type", ""),
                    }
                    for field in form.find_all(
                        ["input", "select", "button"]
                    )
                ],
            })

        for tag in soup.find_all(True):
            onclick = str(tag.get("onclick", ""))
            href = str(tag.get("href", ""))

            if not PATTERN.search(onclick + " " + href):
                continue

            result["links"].append({
                "tag": tag.name,
                "text": tag.get_text(" ", strip=True)[:150],
                "onclick": onclick,
                "href": urljoin(response.url, href) if href else "",
            })

        result["_html"] = response.text

    except Exception as exc:
        result["error"] = str(exc)

    return result


def inspect_script(session, url):
    result = {
        "url": url,
        "status": None,
        "content_type": "",
        "size": 0,
        "matches": [],
        "error": None,
    }

    try:
        response = session.get(url, timeout=25)
        result["status"] = response.status_code
        result["content_type"] = response.headers.get(
            "Content-Type", ""
        )
        result["size"] = len(response.content)

        if response.status_code != 200:
            return result

        source = response.text
        lines = source.splitlines()
        matches = []

        for index, line in enumerate(lines):
            if not PATTERN.search(line):
                continue

            start = max(0, index - 2)
            end = min(len(lines), index + 4)

            snippet = "\n".join(
                f"{i + 1}: {lines[i][:1000]}"
                for i in range(start, end)
            )

            if snippet not in matches:
                matches.append(snippet)

            if len(matches) >= 80:
                break

        result["matches"] = matches

    except Exception as exc:
        result["error"] = str(exc)

    return result


def main():
    now = datetime.now(ZoneInfo("Asia/Tokyo"))

    report = {
        "generated_at": now.isoformat(),
        "pages": [],
        "javascript": [],
        "candidate_api_urls": [],
        "notes": [
            "診断専用。既存の予想データや買い目は変更しない。",
            "JavaScript内のJSON通信・Ajax・画面遷移を調査する。",
            "Cookieの値や個人情報は保存しない。",
        ],
    }

    session = requests.Session()
    session.headers.update(HEADERS)

    log("=" * 55)
    log("KEIRIN.JP データ取得先調査")
    log(f"実行日時: {now.isoformat()}")
    log("=" * 55)

    page_urls = [
        ("top", f"{BASE}/sp/top"),
        ("schedule", f"{BASE}/sp/raceschedule"),
    ]

    script_urls = set()
    api_urls = set()

    for name, url in page_urls:
        log(f"\nページ取得: {name}")

        page = inspect_page(session, name, url)
        html = page.pop("_html", "")

        log(
            f"HTTP={page['status']} "
            f"title={page['title']}"
        )

        script_urls.update(page["scripts"])

        # HTML内のAPIらしいURLを拾う
        for match in re.findall(
            r"""["']([^"'<> ]*(?:/json|ajax|racelist|raceInfo)[^"'<> ]*)["']""",
            html,
            re.IGNORECASE,
        ):
            api_urls.add(urljoin(BASE, match))

        report["pages"].append(page)

    log(f"\n調査対象JavaScript: {len(script_urls)}")

    for index, url in enumerate(sorted(script_urls), 1):
        log(f"JS {index}/{len(script_urls)}: {url}")

        result = inspect_script(session, url)

        if result["matches"]:
            log(f"  関連箇所: {len(result['matches'])}")

        # JavaScript内に現れる通信先候補を収集
        for snippet in result["matches"]:
            for match in re.findall(
                r"""["']([^"' ]*(?:/json|ajax|racelist|raceInfo)[^"' ]*)["']""",
                snippet,
                re.IGNORECASE,
            ):
                api_urls.add(urljoin(BASE, match))

        report["javascript"].append(result)

    report["candidate_api_urls"] = sorted(api_urls)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log("\n調査完了")
    log(f"JavaScript数: {len(script_urls)}")
    log(f"通信先候補: {len(api_urls)}")
    log(f"保存先: {OUTPUT}")


if __name__ == "__main__":
    main()

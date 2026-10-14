import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "race_transition_diagnostic.json"

BASE = "https://keirin.jp"
TOP_URL = BASE + "/sp/top"
RACELIST_URL = BASE + "/sp/racelist"
JSON_URL = BASE + "/sp/json"

TODAY = datetime.now(JST).strftime("%Y%m%d")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def inspect_html(html, page_url):
    soup = BeautifulSoup(html, "html.parser")

    result = {
        "title": (
            soup.title.get_text(" ", strip=True)
            if soup.title else ""
        ),
        "forms": [],
        "interesting_inputs": [],
        "scripts": [],
        "interesting_links": [],
    }

    # フォームの送信先・送信方式を確認
    for form in soup.find_all("form"):
        form_data = {
            "action": form.get("action"),
            "method": form.get("method"),
            "id": form.get("id"),
            "name": form.get("name"),
            "inputs": [],
        }

        for tag in form.find_all(
            ["input", "button", "select"]
        ):
            form_data["inputs"].append({
                "tag": tag.name,
                "type": tag.get("type"),
                "name": tag.get("name"),
                "id": tag.get("id"),
                "value_length": len(
                    str(tag.get("value") or "")
                ),
            })

        result["forms"].append(form_data)

    # 画面遷移に関係しそうな項目だけ抽出
    for tag in soup.find_all(["input", "script", "a"]):
        element_id = str(tag.get("id") or "")
        name = str(tag.get("name") or "")
        src = str(tag.get("src") or "")
        href = str(tag.get("href") or "")

        combined = (
            element_id + " " + name + " " + src + " " + href
        ).lower()

        if any(
            word in combined
            for word in [
                "syusolisturl",
                "encp",
                "disp",
                "submit",
                "common",
                "sj0101",
                "sj0305",
                "sj0301",
                "sj0304",
                "sj0306",
                "racelist",
                "race",
            ]
        ):
            if tag.name == "input":
                result["interesting_inputs"].append({
                    "name": name,
                    "id": element_id,
                    "type": tag.get("type"),
                    "value_length": len(
                        str(tag.get("value") or "")
                    ),
                })

            elif tag.name == "script" and src:
                result["scripts"].append(
                    urljoin(page_url, src)
                )

            elif tag.name == "a":
                result["interesting_links"].append({
                    "text": tag.get_text(" ", strip=True),
                    "href": href,
                    "onclick": tag.get("onclick"),
                })

    # JavaScriptファイルをすべて記録
    for tag in soup.find_all("script", src=True):
        url = urljoin(page_url, tag["src"])
        if url not in result["scripts"]:
            result["scripts"].append(url)

    return result


def get_js_snippets(session, url):
    try:
        response = session.get(url, timeout=25)

        if response.status_code != 200:
            return {
                "url": url,
                "status_code": response.status_code,
                "snippets": [],
            }

        text = response.text
        lines = text.splitlines()
        snippets = []
        keywords = [
            "formPost",
            "syusoListURL",
            "keirinjyoInfoClick",
            "btnPickupClick",
            "encp",
            "SJ0305",
            "SJ0315",
        ]

        matched_lines = set()

        for index, line in enumerate(lines):
            if any(word.lower() in line.lower()
                   for word in keywords):
                start = max(0, index - 4)
                end = min(len(lines), index + 7)

                for line_number in range(start, end):
                    matched_lines.add(line_number)

        if matched_lines:
            current = []
            previous = -2

            for line_number in sorted(matched_lines):
                if line_number > previous + 1 and current:
                    snippets.append("\n".join(current))
                    current = []

                current.append(
                    f"{line_number + 1}: {lines[line_number]}"
                )
                previous = line_number

            if current:
                snippets.append("\n".join(current))

        return {
            "url": url,
            "status_code": response.status_code,
            "bytes": len(response.content),
            "snippets": snippets[:30],
        }

    except Exception as exc:
        return {
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
            "snippets": [],
        }


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    result = {
        "generated_at": datetime.now(JST).isoformat(),
        "target_date": TODAY,
        "pages": {},
        "javascript": [],
        "schedule_summary": {},
        "error": None,
    }

    try:
        # 1. トップページを取得
        print("1. 公式トップページを調査中")

        top = session.get(TOP_URL, timeout=30)
        top.raise_for_status()

        result["pages"]["top"] = inspect_html(
            top.text, top.url
        )

        # 2. 開催情報ページを直接取得
        print("2. 開催情報ページを調査中")

        race_page = session.get(
            RACELIST_URL, timeout=30
        )
        race_page.raise_for_status()

        result["pages"]["racelist_get"] = inspect_html(
            race_page.text, race_page.url
        )

        # 3. 開催情報JSONを取得
        print("3. 開催情報JSONを調査中")

        schedule_response = session.get(
            BASE + "/sp/json",
            params={
                "type": "JSJ058",
                "kday": TODAY,
            },
            headers={
                "Referer": TOP_URL,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=30,
        )
        schedule_response.raise_for_status()

        schedule = schedule_response.json()
        venues = schedule.get("kInfo", [])

        result["schedule_summary"] = {
            "venue_count": len(venues),
            "venues": [
                {
                    "name": v.get("jyoName"),
                    "btnFlgLive": v.get("btnFlgLive"),
                    "encPrm_present": bool(v.get("encPrm")),
                    "encPrm_length": len(
                        str(v.get("encPrm") or "")
                    ),
                    "keys": list(v.keys()),
                }
                for v in venues
            ],
        }

        # 4. 関係するJavaScriptを集める
        script_urls = set()

        for page in result["pages"].values():
            script_urls.update(page["scripts"])

        targets = [
            url for url in script_urls
            if any(
                word in url.lower()
                for word in [
                    "commonsubmit",
                    "commonjson",
                    "commonload",
                    "sj0101",
                    "sc0201",
                    "sj0301",
                    "sj0304",
                    "sj0305",
                    "sj0306",
                ]
            )
        ]

        print("4. JavaScriptを調査中")
        print("対象ファイル数:", len(targets))

        for url in sorted(targets):
            print("調査:", url)

            js_result = get_js_snippets(session, url)

            if js_result.get("snippets"):
                result["javascript"].append(js_result)

        # 5. 要点を表示
        print("\n=== 送信先・フォームの診断 ===")

        for page_name, page in result["pages"].items():
            print("\nページ:", page_name)
            print("タイトル:", page["title"])

            for item in page["interesting_inputs"]:
                if (
                    "syusolisturl" in str(item).lower()
                    or "encp" in str(item).lower()
                ):
                    print("関連項目:", item)

            for form in page["forms"]:
                print("フォーム:", {
                    "action": form["action"],
                    "method": form["method"],
                    "id": form["id"],
                    "name": form["name"],
                })

        print("\nJavaScript調査ファイル数:",
              len(result["javascript"]))

        print("================================")

    except Exception as exc:
        result["error"] = (
            f"{type(exc).__name__}: {exc}"
        )
        print("調査エラー:", result["error"])

    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n診断結果を保存:", OUTPUT)


if __name__ == "__main__":
    main()

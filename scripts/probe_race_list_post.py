import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://keirin.jp"
TOP_URL = f"{BASE}/sp/top"
SCHEDULE_URL = f"{BASE}/sp/raceschedule"

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

INTERESTING = re.compile(
    r"formlnk|commonSubmit|formPost|formGet|encp|"
    r"SJ0\d+|racelist|/sp/race|/sp/result",
    re.I,
)


def log(message):
    print(message, flush=True)


def title_of(html):
    soup = BeautifulSoup(html, "html.parser")
    return soup.title.get_text(" ", strip=True) if soup.title else ""


def extract_scripts(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    found = []

    for tag in soup.find_all("script"):
        src = tag.get("src")

        if src:
            found.append({
                "type": "external",
                "url": urljoin(page_url, src),
            })
        elif tag.string or tag.get_text(strip=True):
            source = tag.string or tag.get_text()
            if INTERESTING.search(source):
                found.append({
                    "type": "inline",
                    "url": page_url + "#inline-script",
                    "source": source[:20000],
                })

    return found


def extract_links(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    found = []

    for tag in soup.find_all(True):
        onclick = str(tag.get("onclick", ""))
        href = str(tag.get("href", ""))

        if not INTERESTING.search(onclick + " " + href):
            continue

        found.append({
            "tag": tag.name,
            "text": tag.get_text(" ", strip=True)[:200],
            "onclick": onclick,
            "href": href,
            "resolved_href": urljoin(page_url, href) if href else "",
            "attributes": {
                key: str(value)
                for key, value in tag.attrs.items()
                if key in (
                    "id", "class", "name", "value",
                    "data-href", "data-url", "action", "method"
                )
            },
        })

    return found


def extract_forms(html):
    soup = BeautifulSoup(html, "html.parser")
    forms = []

    for form in soup.find_all("form"):
        fields = []

        for field in form.find_all(["input", "select", "button"]):
            fields.append({
                "tag": field.name,
                "type": field.get("type", ""),
                "name": field.get("name", ""),
                "value": field.get("value", ""),
                "id": field.get("id", ""),
            })

        forms.append({
            "action": form.get("action", ""),
            "method": form.get("method", ""),
            "id": form.get("id", ""),
            "fields": fields,
        })

    return forms


def extract_tokens(html, page_url, today):
    soup = BeautifulSoup(html, "html.parser")
    found = []

    pattern = re.compile(
        r"""formlnk\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]"""
    )

    for tag in soup.find_all(True):
        onclick = str(tag.get("onclick", ""))
        match = pattern.search(onclick)

        if not match:
            continue

        found.append({
            "text": tag.get_text(" ", strip=True)[:200],
            "target": urljoin(page_url, match.group(1)),
            "token": match.group(2),
            "onclick": onclick,
            "is_today_text": today.strftime("%Y/%m/%d") in tag.get_text(),
        })

    return found


def inspect_javascript(session, script_url, referer):
    result = {
        "url": script_url,
        "status": None,
        "content_type": "",
        "size": 0,
        "interesting": False,
        "matches": [],
        "error": None,
    }

    try:
        response = session.get(
            script_url,
            headers={"Referer": referer},
            timeout=20,
        )

        result["status"] = response.status_code
        result["content_type"] = response.headers.get("Content-Type", "")
        result["size"] = len(response.content)

        if response.status_code != 200:
            return result

        source = response.text
        matches = []

        for match in INTERESTING.finditer(source):
            start = max(0, match.start() - 250)
            end = min(len(source), match.end() + 700)
            snippet = source[start:end]

            if snippet not in matches:
                matches.append(snippet)

            if len(matches) >= 15:
                break

        result["interesting"] = bool(matches)
        result["matches"] = matches

    except Exception as exc:
        result["error"] = str(exc)

    return result


def main():
    today = date.today()

    report = {
        "date": today.isoformat(),
        "pages": [],
        "cookies": [],
        "links": [],
        "forms": [],
        "javascript_urls": [],
        "javascript_details": [],
        "token_candidates": [],
        "notes": [
            "診断専用。レース一覧取得処理は変更しない。",
            "このスクリプトではPOST送信を実行しない。",
            "Cookieの値は保存しない。",
        ],
    }

    session = requests.Session()
    session.headers.update(HEADERS)

    log("=" * 60)
    log("KEIRIN.JP 遷移診断")
    log(f"実行日: {today.isoformat()}")
    log("=" * 60)

    pages = []

    for name, url, referer in (
        ("top", TOP_URL, None),
        ("schedule", SCHEDULE_URL, TOP_URL),
    ):
        try:
            headers = {"Referer": referer} if referer else {}

            response = session.get(
                url,
                headers=headers,
                timeout=30,
            )
            response.encoding = response.apparent_encoding

            page = {
                "name": name,
                "url": response.url,
                "status": response.status_code,
                "title": title_of(response.text),
                "html": response.text,
            }

            pages.append(page)

            report["pages"].append({
                "name": name,
                "url": response.url,
                "status": response.status_code,
                "title": page["title"],
                "size": len(response.content),
            })

            log(
                f"{name}: HTTP {response.status_code} "
                f"/ {page['title']}"
            )

        except Exception as exc:
            log(f"{name}取得失敗: {exc}")
            report["pages"].append({
                "name": name,
                "url": url,
                "error": str(exc),
            })

    for cookie in session.cookies:
        report["cookies"].append({
            "name": cookie.name,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.secure,
        })

    script_map = {}

    for page in pages:
        if "html" not in page:
            continue

        name = page["name"]
        html = page["html"]
        page_url = page["url"]

        links = extract_links(html, page_url)
        forms = extract_forms(html)
        scripts = extract_scripts(html, page_url)

        report["links"].append({
            "page": name,
            "items": links,
        })

        report["forms"].append({
            "page": name,
            "items": forms,
        })

        log(f"\n[{name}] 関連リンク: {len(links)}")
        log(f"[{name}] フォーム: {len(forms)}")

        for item in links:
            log("LINK " + json.dumps(item, ensure_ascii=False)[:900])

        for form in forms:
            log("FORM " + json.dumps(form, ensure_ascii=False)[:900])

        for script in scripts:
            if script["type"] == "inline":
                report["javascript_details"].append({
                    "type": "inline",
                    "page": name,
                    "url": script["url"],
                    "interesting": True,
                    "source": script["source"],
                })
            else:
                script_map.setdefault(script["url"], page_url)

    schedule_page = next(
        (p for p in pages if p["name"] == "schedule" and "html" in p),
        None,
    )

    if schedule_page:
        report["token_candidates"] = extract_tokens(
            schedule_page["html"],
            schedule_page["url"],
            today,
        )

    log(f"\nJavaScript URL数: {len(script_map)}")

    for index, script_url in enumerate(script_map, 1):
        log(f"JS URL {index}: {script_url}")

        detail = inspect_javascript(
            session,
            script_url,
            script_map[script_url],
        )

        report["javascript_details"].append(detail)

        if detail["interesting"]:
            log(f"  関連コードあり: {len(detail['matches'])}箇所")

            for snippet in detail["matches"][:5]:
                log("  " + snippet[:500].replace("\n", " "))
        elif detail["status"] != 200:
            log(f"  HTTP: {detail['status']}")

    report["javascript_urls"] = list(script_map.keys())

    log(f"\nCookie数: {len(report['cookies'])}")
    log(f"開催トークン候補: {len(report['token_candidates'])}")

    for item in report["token_candidates"]:
        log(
            "TOKEN "
            + json.dumps(
                {
                    "text": item["text"],
                    "target": item["target"],
                    "is_today_text": item["is_today_text"],
                },
                ensure_ascii=False,
            )
        )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log("\n診断完了")
    log(f"出力ファイル: {OUTPUT}")
    log(f"外部JavaScript: {len(report['javascript_urls'])}")
    log(f"JavaScript調査結果: {len(report['javascript_details'])}")


if __name__ == "__main__":
    main()

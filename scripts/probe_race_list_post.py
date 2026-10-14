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


def log(message):
    print(message, flush=True)


def page_title(html):
    soup = BeautifulSoup(html, "html.parser")
    return soup.title.get_text(" ", strip=True) if soup.title else ""


def extract_date_range(text, year):
    pattern = (
        r"(\d{1,2})/(\d{1,2})[（(][^）)]*[）)]"
        r"\s*[～〜~\-－]\s*(\d{1,2})/(\d{1,2})"
    )

    match = re.search(pattern, text)

    if not match:
        pattern = (
            r"(\d{1,2})/(\d{1,2})\s*[～〜~\-－]\s*"
            r"(\d{1,2})/(\d{1,2})"
        )
        match = re.search(pattern, text)

    if not match:
        return None

    sm, sd, em, ed = map(int, match.groups())

    try:
        start = date(year, sm, sd)
        end = date(year + (1 if em < sm else 0), em, ed)
        return start, end
    except ValueError:
        return None


def extract_links(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    found = []

    for tag in soup.find_all(True):
        onclick = tag.get("onclick", "")
        href = tag.get("href", "")

        if not onclick and not href:
            continue

        text = tag.get_text(" ", strip=True)
        combined = f"{onclick} {href}"

        if not re.search(
            r"formlnk|encp|racelist|/sp/race|/sp/result|SJ0\d+",
            combined,
            re.I,
        ):
            continue

        item = {
            "tag": tag.name,
            "text": text[:200],
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
        }

        found.append(item)

    return found


def extract_scripts(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    urls = []

    for tag in soup.find_all("script", src=True):
        urls.append(urljoin(page_url, tag["src"]))

    return list(dict.fromkeys(urls))


def extract_form_info(html):
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


def main():
    today = date.today()

    report = {
        "date": today.isoformat(),
        "pages": [],
        "cookies": [],
        "links": [],
        "forms": [],
        "scripts": [],
        "token_candidates": [],
        "transition_tests": [],
    }

    session = requests.Session()
    session.headers.update(HEADERS)

    log("=" * 65)
    log("KEIRIN.JP 画面遷移・フォーム診断")
    log(f"実行日: {today.isoformat()}")
    log("=" * 65)

    # 1. トップページ
    top = session.get(TOP_URL, timeout=30)
    top.encoding = top.apparent_encoding

    log(f"\nトップ: HTTP {top.status_code} / {page_title(top.text)}")

    report["pages"].append({
        "url": top.url,
        "status": top.status_code,
        "title": page_title(top.text),
    })

    # 2. 開催日程ページ
    schedule = session.get(
        SCHEDULE_URL,
        headers={"Referer": top.url},
        timeout=30,
    )
    schedule.encoding = schedule.apparent_encoding

    log(
        f"開催日程: HTTP {schedule.status_code} / "
        f"{page_title(schedule.text)}"
    )

    report["pages"].append({
        "url": schedule.url,
        "status": schedule.status_code,
        "title": page_title(schedule.text),
    })

    # 3. セッション情報
    for cookie in session.cookies:
        report["cookies"].append({
            "name": cookie.name,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.secure,
        })

    log(f"Cookie数: {len(report['cookies'])}")

    # 4. ページのリンクとフォームを調査
    for page_name, html, page_url in (
        ("top", top.text, top.url),
        ("schedule", schedule.text, schedule.url),
    ):
        links = extract_links(html, page_url)
        forms = extract_form_info(html)

        report["links"].append({
            "page": page_name,
            "items": links,
        })

        report["forms"].append({
            "page": page_name,
            "items": forms,
        })

        log(f"\n[{page_name}] 関連リンク: {len(links)}")
        log(f"[{page_name}] フォーム: {len(forms)}")

        for item in links:
            log(
                "LINK "
                + json.dumps(item, ensure_ascii=False)[:700]
            )

        for form in forms:
            log(
                "FORM "
                + json.dumps(form, ensure_ascii=False)[:1200]
            )

    # 5. JavaScriptを取得し、遷移処理を探す
    script_urls = list(dict.fromkeys(
        extract_scripts(top.text, top.url)
        + extract_scripts(schedule.text, schedule.url)
    ))

    log(f"\nJavaScript候補: {len(script_urls)}")

    for script_url in script_urls:
        try:
            response = session.get(
                script_url,
                headers={"Referer": schedule.url},
                timeout=20,
            )

            if response.status_code != 200:
                continue

            source = response.text

            if not re.search(
                r"formlnk|commonSubmit|formPost|encp|SJ0301|SJ0305",
                source,
                re.I,
            ):
                continue

            matches = []

            for match in re.finditer(
                r"formlnk|commonSubmit|formPost|encp|SJ0301|SJ0305",
                source,
                re.I,
            ):
                start = max(0, match.start() - 350)
                end = min(len(source), match.end() + 900)
                snippet = source[start:end]

                if snippet not in matches:
                    matches.append(snippet)

                if len(matches) >= 12:
                    break

            entry = {
                "url": script_url,
                "status": response.status_code,
                "snippets": matches,
            }

            report["scripts"].append(entry)
            log(f"\nJS: {script_url}")

            for snippet in matches:
                log(snippet[:1000])
                log("-" * 30)

        except Exception as exc:
            log(f"JS取得失敗: {script_url}: {exc}")

    # 6. 開催トークン候補を記録する
    soup = BeautifulSoup(schedule.text, "html.parser")

    token_pattern = re.compile(
        r"""formlnk\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]"""
    )

    for tag in soup.find_all(True):
        onclick = tag.get("onclick", "")
        match = token_pattern.search(onclick)

        if not match:
            continue

        text = tag.get_text(" ", strip=True)
        interval = extract_date_range(text, today.year)

        entry = {
            "text": text,
            "target": urljoin(BASE, match.group(1)),
            "token": match.group(2),
            "onclick": onclick,
            "date_range": (
                [interval[0].isoformat(), interval[1].isoformat()]
                if interval else None
            ),
            "is_today": (
                interval[0] <= today <= interval[1]
                if interval else False
            ),
        }

        report["token_candidates"].append(entry)

    log(
        f"\nトークン候補: "
        f"{len(report['token_candidates'])}"
    )

    for item in report["token_candidates"]:
        if item["is_today"]:
            log(
                "当日候補: "
                + json.dumps(item, ensure_ascii=False)
            )

    # 7. 重要：ここではPOSTを再実行しない
    # 公式ページが必要とするパラメータを特定する前に、
    # 同じPOSTを繰り返しても原因の切り分けにならないため。

    report["notes"] = [
        "この診断ではPOSTを再実行していない。",
        "開催リンク、フォーム、JavaScript、Cookie情報を記録した。",
        "Cookieの値など認証情報は保存していない。",
    ]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log("\n" + "=" * 65)
    log("診断完了")
    log(f"リンク記録: {len(report['links'])}ページ")
    log(f"フォーム記録: {len(report['forms'])}ページ")
    log(f"関連JavaScript: {len(report['scripts'])}")
    log(f"トークン候補: {len(report['token_candidates'])}")
    log(f"出力: {OUTPUT}")
    log("=" * 65)


if __name__ == "__main__":
    main()

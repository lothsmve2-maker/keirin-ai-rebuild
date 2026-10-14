import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://keirin.jp"
TOP_URL = f"{BASE_URL}/sp/top"
SCHEDULE_URL = f"{BASE_URL}/sp/raceschedule"

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT = DATA_DIR / "race_transition_diagnostic.json"

JST = timezone(timedelta(hours=9))

KEYWORDS = [
    "commonSubmit",
    "formPost",
    "syusoListURL",
    "keirinjyoInfoClick",
    "btnPickupClick",
    "encp",
    "SJ0305",
    "SJ0315",
    "racelist",
    "raceList",
    "raceInfo",
    "disp",
]


def log(message):
    print(message, flush=True)


def fetch(session, url):
    try:
        response = session.get(url, timeout=25)
        response.encoding = response.apparent_encoding
        return {
            "url": url,
            "status": response.status_code,
            "final_url": response.url,
            "html": response.text,
            "error": None,
        }
    except Exception as exc:
        return {
            "url": url,
            "status": None,
            "final_url": None,
            "html": "",
            "error": f"{type(exc).__name__}: {exc}",
        }


def inspect_page(page):
    html = page["html"]
    page_url = page["final_url"] or page["url"]

    result = {
        "requested_url": page["url"],
        "status": page["status"],
        "final_url": page["final_url"],
        "error": page["error"],
        "title": "",
        "forms": [],
        "links": [],
        "scripts": [],
        "keyword_lines": [],
    }

    if not html:
        return result

    soup = BeautifulSoup(html, "html.parser")

    if soup.title:
        result["title"] = soup.title.get_text(" ", strip=True)

    for form in soup.find_all("form"):
        inputs = []

        for item in form.find_all(["input", "button", "select"]):
            inputs.append({
                "tag": item.name,
                "type": item.get("type"),
                "name": item.get("name"),
                "value": item.get("value"),
                "id": item.get("id"),
                "onclick": item.get("onclick"),
            })

        result["forms"].append({
            "action": urljoin(page_url, form.get("action") or page_url),
            "raw_action": form.get("action"),
            "method": form.get("method", "get").upper(),
            "id": form.get("id"),
            "name": form.get("name"),
            "inputs": inputs,
        })

    for tag in soup.find_all(True):
        onclick = tag.get("onclick")
        href = tag.get("href")

        if onclick:
            text = tag.get_text(" ", strip=True)
            combined = f"{onclick} {href or ''} {text}"

            if any(k.lower() in combined.lower() for k in KEYWORDS):
                result["links"].append({
                    "tag": tag.name,
                    "text": text[:160],
                    "href": urljoin(page_url, href) if href else None,
                    "onclick": onclick[:500],
                    "id": tag.get("id"),
                    "name": tag.get("name"),
                    "class": tag.get("class"),
                })

        elif href:
            text = tag.get_text(" ", strip=True)
            combined = f"{href} {text}"

            if any(k.lower() in combined.lower() for k in KEYWORDS):
                result["links"].append({
                    "tag": tag.name,
                    "text": text[:160],
                    "href": urljoin(page_url, href),
                    "onclick": None,
                    "id": tag.get("id"),
                    "name": tag.get("name"),
                    "class": tag.get("class"),
                })

    for tag in soup.find_all("script", src=True):
        src = urljoin(page_url, tag.get("src"))
        result["scripts"].append(src)

    lines = html.splitlines()

    for i, line in enumerate(lines):
        if any(k.lower() in line.lower() for k in KEYWORDS):
            result["keyword_lines"].append({
                "line": i + 1,
                "text": line.strip()[:500],
            })

    return result


def inspect_javascript(session, script_urls):
    output = []
    seen = set()

    for url in script_urls:
        if url in seen:
            continue
        seen.add(url)

        # 大量の無関係なJSを取得しない
        if len(seen) > 35:
            break

        try:
            response = session.get(url, timeout=20)

            if response.status_code != 200:
                continue

            content_type = response.headers.get("Content-Type", "")

            if "javascript" not in content_type.lower() and not url.lower().endswith(".js"):
                continue

            source = response.text
            lines = source.splitlines()
            matches = []

            for i, line in enumerate(lines):
                if any(k.lower() in line.lower() for k in KEYWORDS):
                    start = max(0, i - 2)
                    end = min(len(lines), i + 4)

                    matches.append({
                        "line": i + 1,
                        "snippet": "\n".join(lines[start:end])[:1200],
                    })

                    if len(matches) >= 35:
                        break

            if matches:
                output.append({
                    "url": url,
                    "status": response.status_code,
                    "matches": matches,
                })

                log(f"  JS候補: {url} / 一致 {len(matches)} 件")

        except Exception as exc:
            log(f"  JS取得失敗: {url} / {type(exc).__name__}: {exc}")

    return output


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 13) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Mobile Safari/537.36"
        ),
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Referer": TOP_URL,
    })

    log("=" * 55)
    log("KEIRIN.JP 遷移先・フォーム診断")
    log("=" * 55)

    pages = []

    for label, url in [
        ("公式トップページ", TOP_URL),
        ("公式開催日程", SCHEDULE_URL),
    ]:
        log(f"\n[{label}] GET {url}")
        page = fetch(session, url)
        pages.append(page)

        log(f"HTTP: {page['status']}")
        log(f"最終URL: {page['final_url']}")

        if page["error"]:
            log(f"取得エラー: {page['error']}")

    inspected = []

    for page in pages:
        result = inspect_page(page)
        inspected.append(result)

        log(f"\n--- ページ解析: {result['requested_url']} ---")
        log(f"タイトル: {result['title']}")
        log(f"フォーム数: {len(result['forms'])}")
        log(f"関連リンク・onclick数: {len(result['links'])}")
        log(f"JavaScript数: {len(result['scripts'])}")

        for form in result["forms"]:
            log(
                "FORM "
                f"method={form['method']} "
                f"action={form['action']} "
                f"id={form['id']} "
                f"name={form['name']}"
            )

            for item in form["inputs"]:
                if item["name"] or item["onclick"]:
                    log(f"  INPUT/BUTTON: {item}")

        for item in result["links"][:30]:
            log(f"LINK: {json.dumps(item, ensure_ascii=False)}")

        for item in result["keyword_lines"][:20]:
            log(f"HTML: {item['line']}: {item['text']}")

    script_urls = []

    for page in inspected:
        script_urls.extend(page["scripts"])

    log("\n--- 関連JavaScriptを調査 ---")
    js_results = inspect_javascript(session, script_urls)

    report = {
        "checked_at": datetime.now(JST).isoformat(),
        "method": "Inspect official top and schedule pages; do not assume /sp/racelist exists",
        "pages": inspected,
        "javascript": js_results,
    }

    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log("\n" + "=" * 55)
    log("診断完了")
    log(f"調査ページ数: {len(inspected)}")
    log(f"JavaScript調査結果: {len(js_results)}")
    log(f"保存先: {OUTPUT}")
    log("=" * 55)

    # GitHub Actionsのログから結果を確認できるようにする
    log("\n=== 送信先・フォームの診断 ===")

    for page in inspected:
        log(f"\nページ: {page['requested_url']}")
        log(f"HTTP: {page['status']} / タイトル: {page['title']}")

        for form in page["forms"]:
            log(
                f"FORM {form['method']} {form['action']} "
                f"id={form['id']} name={form['name']}"
            )

        for item in page["links"]:
            log(json.dumps(item, ensure_ascii=False))

    log("\n=== JavaScriptの重要箇所 ===")

    for item in js_results:
        log(f"\nFILE: {item['url']}")

        for match in item["matches"][:15]:
            log(f"LINE {match['line']}\n{match['snippet']}")


if __name__ == "__main__":
    main()

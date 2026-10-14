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


def get_title(html):
    soup = BeautifulSoup(html, "html.parser")
    return soup.title.get_text(" ", strip=True) if soup.title else ""


def extract_tokens(html):
    soup = BeautifulSoup(html, "html.parser")
    tokens = []

    pattern = re.compile(
        r"""formlnk\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]"""
    )

    for tag in soup.find_all(True):
        onclick = tag.get("onclick", "")
        match = pattern.search(onclick)

        if not match:
            continue

        tokens.append({
            "target": urljoin(BASE, match.group(1)),
            "token": match.group(2),
            "text": tag.get_text(" ", strip=True),
            "onclick": onclick,
        })

    return tokens


def date_range(text, year):
    match = re.search(
        r"(\d{1,2})/(\d{1,2})[（(][^）)]*[）)]"
        r"\s*[～〜~\-－]\s*"
        r"(\d{1,2})/(\d{1,2})",
        text,
    )

    if not match:
        match = re.search(
            r"(\d{1,2})/(\d{1,2})\s*[～〜~\-－]\s*"
            r"(\d{1,2})/(\d{1,2})",
            text,
        )

    if not match:
        return None

    sm, sd, em, ed = map(int, match.groups())

    try:
        start = date(year, sm, sd)
        end_year = year + (1 if em < sm else 0)
        end = date(end_year, em, ed)
        return start, end
    except ValueError:
        return None


def choose_today_token(tokens, target_date):
    exact = []

    for item in tokens:
        interval = date_range(item["text"], target_date.year)

        if interval:
            start, end = interval

            if start <= target_date <= end:
                exact.append(item)

    return exact


def main():
    today = date.today()

    log("=" * 60)
    log("KEIRIN.JP 当日開催トークン・画面遷移診断")
    log(f"実行日: {today.isoformat()}")
    log("=" * 60)

    session = requests.Session()
    session.headers.update(HEADERS)

    report = {
        "date": today.isoformat(),
        "pages": [],
        "today_tokens": [],
        "javascript": [],
        "tests": [],
    }

    # 1. セッションを開始
    top = session.get(TOP_URL, timeout=30)
    top.encoding = top.apparent_encoding

    log(f"\nトップページ: HTTP {top.status_code}")
    log(f"タイトル: {get_title(top.text)}")

    report["pages"].append({
        "url": top.url,
        "status": top.status_code,
        "title": get_title(top.text),
    })

    # 2. 開催日程から当日開催のトークンを抽出
    schedule = session.get(
        SCHEDULE_URL,
        headers={"Referer": TOP_URL},
        timeout=30,
    )
    schedule.encoding = schedule.apparent_encoding

    log(f"\n開催日程: HTTP {schedule.status_code}")
    log(f"タイトル: {get_title(schedule.text)}")

    report["pages"].append({
        "url": schedule.url,
        "status": schedule.status_code,
        "title": get_title(schedule.text),
    })

    tokens = extract_tokens(schedule.text)
    today_tokens = choose_today_token(tokens, today)

    log(f"全トークン数: {len(tokens)}")
    log(f"当日開催トークン数: {len(today_tokens)}")

    for item in today_tokens:
        log(f"当日候補: {item['text']}")
        log(f"  送信先: {item['target']}")
        log(f"  トークン: {item['token']}")

    report["today_tokens"] = today_tokens

    # 3. commonSubmit.formPost の実装を探す
    log("\n[JavaScript] commonSubmit.formPost を検索")

    soup = BeautifulSoup(schedule.text, "html.parser")
    top_soup = BeautifulSoup(top.text, "html.parser")

    scripts = []

    for page_soup, page_url in [
        (top_soup, top.url),
        (soup, schedule.url),
    ]:
        for tag in page_soup.find_all("script", src=True):
            scripts.append(urljoin(page_url, tag["src"]))

    scripts = list(dict.fromkeys(scripts))

    for script_url in scripts:
        try:
            response = session.get(script_url, timeout=20)

            if response.status_code != 200:
                continue

            source = response.text

            if not re.search(r"commonSubmit|formPost", source, re.I):
                continue

            # 関連箇所をログに出す
            matches = list(
                re.finditer(r"commonSubmit|formPost", source, re.I)
            )

            snippets = []

            for match in matches[:8]:
                start = max(0, match.start() - 500)
                end = min(len(source), match.end() + 1800)
                snippet = source[start:end]
                snippets.append(snippet)

                log(f"\nJS: {script_url}")
                log(snippet[:2200])

            report["javascript"].append({
                "url": script_url,
                "snippets": snippets,
            })

        except Exception as exc:
            log(f"JS取得失敗: {script_url}: {exc}")

    # 4. 当日開催のトークンだけを使って POST を検証
    log("\n[遷移テスト]")

    if not today_tokens:
        log("当日開催のトークンを抽出できませんでした。")
        log("日程ページの表記または実行日の確認が必要です。")

    for item in today_tokens:
        target = item["target"]
        token = item["token"]

        # 公式 lnk.formlnk が指定している disp を使用
        payload = {
            "encp": token,
            "disp": "SJ0301",
        }

        try:
            response = session.post(
                target,
                data=payload,
                headers={
                    "Referer": SCHEDULE_URL,
                    "Origin": BASE,
                },
                timeout=30,
                allow_redirects=True,
            )
            response.encoding = response.apparent_encoding

            result_soup = BeautifulSoup(response.text, "html.parser")
            title = get_title(response.text)
            text = result_soup.get_text(" ", strip=True)

            race_markers = bool(
                re.search(
                    r"第\s*\d+\s*レース|1R|2R|3R|レース一覧|出走表",
                    text,
                )
            )

            result = {
                "venue_text": item["text"],
                "request_url": target,
                "payload": payload,
                "status": response.status_code,
                "final_url": response.url,
                "title": title,
                "body_excerpt": text[:2000],
                "race_markers_found": race_markers,
            }

            report["tests"].append(result)

            log("\n--- POST結果 ---")
            log(f"開催: {item['text']}")
            log(f"HTTP: {response.status_code}")
            log(f"最終URL: {response.url}")
            log(f"タイトル: {title}")
            log(f"レース関連文字列検出: {race_markers}")
            log(f"本文: {text[:700]}")

        except Exception as exc:
            log(f"POST失敗: {type(exc).__name__}: {exc}")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log("\n" + "=" * 60)
    log("診断終了")
    log(f"当日開催候補: {len(today_tokens)}")
    log(f"POSTテスト: {len(report['tests'])}")
    log(f"JavaScript候補: {len(report['javascript'])}")
    log(f"出力: {OUTPUT}")
    log("=" * 60)


if __name__ == "__main__":
    main()

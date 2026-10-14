import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE = "https://keirin.jp"
TOP_URL = f"{BASE}/sp/top"
SCHEDULE_URL = f"{BASE}/sp/raceschedule"

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "race_transition_diagnostic.json"

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
    return (
        soup.title.get_text(" ", strip=True)
        if soup.title else ""
    )


def find_tokens(html):
    soup = BeautifulSoup(html, "html.parser")
    found = []

    for tag in soup.find_all(True):
        onclick = tag.get("onclick", "")

        match = re.search(
            r"""formlnk\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]""",
            onclick,
        )

        if not match:
            continue

        found.append({
            "target": urljoin(BASE, match.group(1)),
            "token": match.group(2),
            "text": tag.get_text(" ", strip=True)[:200],
            "onclick": onclick,
        })

    return found


def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    report = {
        "pages": [],
        "tokens": [],
        "javascript": [],
        "tests": [],
    }

    log("=" * 60)
    log("KEIRIN.JP レース一覧遷移の調査")
    log("=" * 60)

    # 1. 公式トップページにアクセスしてセッションを作る
    log("\n[1] 公式トップページ")

    top = session.get(TOP_URL, timeout=30)
    top.encoding = top.apparent_encoding

    log(f"HTTP: {top.status_code}")
    log(f"URL: {top.url}")
    log(f"タイトル: {page_title(top.text)}")

    report["pages"].append({
        "url": top.url,
        "status": top.status_code,
        "title": page_title(top.text),
    })

    top_soup = BeautifulSoup(top.text, "html.parser")

    # 2. 開催日程ページから実際のトークンを取得
    log("\n[2] 開催日程ページ")

    schedule = session.get(
        SCHEDULE_URL,
        timeout=30,
        headers={"Referer": TOP_URL},
    )
    schedule.encoding = schedule.apparent_encoding

    log(f"HTTP: {schedule.status_code}")
    log(f"URL: {schedule.url}")
    log(f"タイトル: {page_title(schedule.text)}")

    report["pages"].append({
        "url": schedule.url,
        "status": schedule.status_code,
        "title": page_title(schedule.text),
    })

    tokens = find_tokens(schedule.text)
    report["tokens"] = tokens

    log(f"取得した開催トークン数: {len(tokens)}")

    for i, item in enumerate(tokens, 1):
        log(
            f"{i}. {item['text']} "
            f"target={item['target']} "
            f"token={item['token']}"
        )

    # 3. lnk.formlnk の実装をJavaScriptから探す
    log("\n[3] formlnk の実装を検索")

    schedule_soup = BeautifulSoup(schedule.text, "html.parser")
    script_urls = []

    for tag in schedule_soup.find_all("script", src=True):
        script_urls.append(urljoin(schedule.url, tag["src"]))

    for tag in top_soup.find_all("script", src=True):
        script_urls.append(urljoin(top.url, tag["src"]))

    script_urls = list(dict.fromkeys(script_urls))

    for script_url in script_urls:
        try:
            response = session.get(script_url, timeout=20)

            if response.status_code != 200:
                continue

            source = response.text

            # formlnk の定義を含むファイルを優先
            if not re.search(r"formlnk", source, re.IGNORECASE):
                continue

            log(f"\nJavaScript発見: {script_url}")

            matches = list(
                re.finditer(r"formlnk", source, re.IGNORECASE)
            )

            snippets = []

            for match in matches[:10]:
                start = max(0, match.start() - 500)
                end = min(len(source), match.end() + 1800)
                snippet = source[start:end]

                snippets.append(snippet)
                log("--- formlnk 周辺 ---")
                log(snippet[:2300])

            report["javascript"].append({
                "url": script_url,
                "snippets": snippets,
            })

        except Exception as exc:
            log(f"JS取得失敗: {script_url}: {exc}")

    # 4. 最初の当日開催会場を選び、遷移方法を検証
    log("\n[4] レース一覧への遷移を検証")

    # ページに掲載されたトークンをそのまま使用する。
    # GETとPOSTの両方を試すが、成功したとはHTTP 200だけで判断しない。
    candidates = [
        item for item in tokens
        if "/sp/racelist" in item["target"]
    ]

    if not candidates:
        log("開催日程ページから racelist のトークンを取得できませんでした。")
    else:
        candidate = candidates[0]
        target = candidate["target"]
        token = candidate["token"]

        log(f"検証対象: {candidate['text']}")
        log(f"送信先: {target}")

        # GET: トークンをクエリに付ける方式
        try:
            response = session.get(
                target,
                params={"encp": token},
                headers={"Referer": SCHEDULE_URL},
                timeout=30,
                allow_redirects=True,
            )
            response.encoding = response.apparent_encoding

            title = page_title(response.text)

            result = {
                "method": "GET",
                "url": response.url,
                "status": response.status_code,
                "title": title,
                "body_excerpt": BeautifulSoup(
                    response.text, "html.parser"
                ).get_text(" ", strip=True)[:1500],
                "has_race_list_markers": bool(
                    re.search(
                        r"第\s*\d+\s*レース|レース一覧|出走表|RaceList",
                        response.text,
                        re.IGNORECASE,
                    )
                ),
            }

            report["tests"].append(result)

            log(
                f"GET結果: HTTP {response.status_code} "
                f"title={title} URL={response.url}"
            )
            log(f"本文: {result['body_excerpt'][:500]}")

        except Exception as exc:
            log(f"GET検証エラー: {type(exc).__name__}: {exc}")

        # POST: トークンをフォーム項目として送る方式
        post_variants = [
            {"encp": token},
            {"encp": token, "disp": "SJ0305"},
        ]

        for data in post_variants:
            try:
                response = session.post(
                    target,
                    data=data,
                    headers={
                        "Referer": SCHEDULE_URL,
                        "Origin": BASE,
                    },
                    timeout=30,
                    allow_redirects=True,
                )
                response.encoding = response.apparent_encoding

                title = page_title(response.text)
                text = BeautifulSoup(
                    response.text, "html.parser"
                ).get_text(" ", strip=True)

                result = {
                    "method": "POST",
                    "payload_keys": list(data.keys()),
                    "status": response.status_code,
                    "url": response.url,
                    "title": title,
                    "body_excerpt": text[:1500],
                    "has_race_list_markers": bool(
                        re.search(
                            r"第\s*\d+\s*レース|レース一覧|出走表|RaceList",
                            response.text,
                            re.IGNORECASE,
                        )
                    ),
                }

                report["tests"].append(result)

                log(
                    f"POST keys={list(data.keys())} "
                    f"HTTP {response.status_code} "
                    f"title={title} URL={response.url}"
                )
                log(f"本文: {text[:500]}")

            except Exception as exc:
                log(f"POST検証エラー: {type(exc).__name__}: {exc}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log("\n" + "=" * 60)
    log("診断終了")
    log(f"JavaScript定義の発見数: {len(report['javascript'])}")
    log(f"遷移テスト数: {len(report['tests'])}")
    log(f"診断ファイル: {OUT}")
    log("=" * 60)


if __name__ == "__main__":
    main()

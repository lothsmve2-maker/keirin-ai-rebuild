#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

import requests

BASE = "https://keirin.jp"
SCHEDULE = f"{BASE}/sp/raceschedule"
TIMEOUT = 20

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "text/html,application/javascript,*/*",
    "Accept-Language": "ja-JP,ja;q=0.9",
}


def main():
    session = requests.Session()
    session.headers.update(HEADERS)

    print("=" * 60)
    print("KEIRIN.JP formリンク処理調査")
    print("対象日:", datetime.now(
        timezone(timedelta(hours=9))
    ).date())
    print("=" * 60)

    try:
        response = session.get(
            SCHEDULE,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
    except Exception as exc:
        print("日程ページ取得失敗:", repr(exc))
        return

    html = response.text

    print()
    print("[1] 開催日程ページ")
    print("HTTP:", response.status_code)
    print("URL:", response.url)
    print("サイズ:", len(response.content))

    # 開催一覧へのリンクを抽出
    pattern = re.compile(
        r"""lnk\.formlnk\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]""",
        re.IGNORECASE,
    )

    links = pattern.findall(html)

    print()
    print("[2] lnk.formlnk の呼び出し")
    print("検出件数:", len(links))

    for path, parameter in links:
        print("遷移先:", path)
        print("パラメータ:", parameter)

    # ページ内JavaScriptの参照先を抽出
    script_paths = re.findall(
        r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""",
        html,
        flags=re.IGNORECASE,
    )

    script_urls = []

    for path in script_paths:
        url = urljoin(response.url, path)

        if url not in script_urls:
            script_urls.append(url)

    print()
    print("[3] JavaScript内の formlnk 定義を検索")
    print("JavaScriptファイル数:", len(script_urls))

    keywords = (
        "formlnk",
        "racelist",
        "formPost",
        "formGet",
        "method",
    )

    found_any = False

    for js_url in script_urls:
        try:
            js_response = session.get(
                js_url,
                headers={"Referer": response.url},
                timeout=TIMEOUT,
            )

            if not js_response.ok:
                continue

            source = js_response.text

            # formlnkの定義があるファイルを優先
            if not re.search(
                r"formlnk",
                source,
                flags=re.IGNORECASE,
            ):
                continue

            found_any = True

            print()
            print("--- JavaScript ---")
            print("URL:", js_url)

            matches = list(re.finditer(
                r"formlnk",
                source,
                flags=re.IGNORECASE,
            ))

            for match in matches[:5]:
                start = max(0, match.start() - 500)
                end = min(len(source), match.end() + 1000)

                print(
                    source[start:end]
                    .replace("\r", " ")
                    .replace("\n", " ")[:1500]
                )

        except Exception as exc:
            print("JavaScript取得失敗:", js_url, repr(exc))

    if not found_any:
        print("外部JavaScript内にformlnkが見つかりません")

    # 開催一覧リンクのHTML上の位置も出力
    print()
    print("[4] 開催一覧リンクのHTML")

    for path, parameter in links[:5]:
        pos = html.find(parameter)

        if pos < 0:
            continue

        start = max(0, pos - 350)
        end = min(len(html), pos + len(parameter) + 350)

        print()
        print("遷移先:", path)
        print(
            html[start:end]
            .replace("\r", " ")
            .replace("\n", " ")[:1000]
        )

    print()
    print("=" * 60)
    print()
    print("=" * 60)
    print("開催一覧 POST方式の確認")
    print("=" * 60)

    # 日程ページのHTMLから、実際の開催一覧パラメータを取得
    matches = re.findall(
        r"""lnk\.formlnk\(\s*['"]/sp/racelist['"]\s*,\s*['"]([^'"]+)['"]\s*\)""",
        html,
        flags=re.IGNORECASE,
    )

    print("開催一覧リンク数:", len(matches))

    if matches:
        encp = matches[-1]

        print("テスト用encp:", encp)

        try:
            response = session.post(
                f"{BASE}/sp/racelist",
                data={
                    "encp": encp,
                    "disp": "SJ0301",
                },
                headers={
                    "Referer": SCHEDULE,
                    "Origin": BASE,
                    "Content-Type": (
                        "application/x-www-form-urlencoded"
                    ),
                },
                timeout=TIMEOUT,
                allow_redirects=True,
            )

            print("HTTP:", response.status_code)
            print("最終URL:", response.url)
            print("応答サイズ:", len(response.content))
            print(
                "エラーページ:",
                "/sp/error" in response.url,
            )

            title = re.findall(
                r"<title[^>]*>(.*?)</title>",
                response.text,
                flags=re.IGNORECASE | re.DOTALL,
            )

            print("ページタイトル:", title[:1])

            for keyword in (
                "race_tbl",
                "strRaceNo",
                "出走表",
                "レース一覧",
            ):
                print(
                    f"{keyword}:",
                    keyword in response.text,
                )

        except Exception as exc:
            print("POST失敗:", repr(exc))

    print("=" * 60)
    print("調査終了")
    print("=" * 60)


if __name__ == "__main__":
    main()

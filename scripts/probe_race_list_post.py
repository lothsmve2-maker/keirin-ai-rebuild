#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KEIRIN.JP レース一覧の通信仕様調査
・開催日程HTMLの実際の遷移先とencpを同時に取得
・SJ0201.js のフォーム送信処理を記録
・JSJ058の正しいフィールド名で開催情報を解析
・実際のリンク先に対し SJ0301 でPOST
・成功をHTTP 200だけで判定しない
・診断結果を data/race_transition_diagnostic.json に保存
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE = "https://keirin.jp"
SCHEDULE_URL = f"{BASE}/sp/raceschedule"
SCHEDULE_JSON_URL = f"{BASE}/sp/json?type=JSJ058"

OUT_FILE = Path("data/race_transition_diagnostic.json")
TIMEOUT = 25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8",
}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def excerpt(value, limit=1200):
    return re.sub(r"\s+", " ", value or "").strip()[:limit]


def safe_url(url):
    """クエリに機密らしい値があれば記録を抑制する。"""
    parsed = urlparse(url)
    return parsed.scheme + "://" + parsed.netloc + parsed.path


def save(data):
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def request_info(response):
    return {
        "status_code": response.status_code,
        "url": response.url,
        "content_type": response.headers.get("Content-Type", ""),
        "bytes": len(response.content),
        "redirect_history": [
            {
                "status_code": r.status_code,
                "url": r.url,
                "location": r.headers.get("Location", ""),
            }
            for r in response.history
        ],
    }


def extract_encp_from_call(value):
    """
    form lnk の呼び出しから引数を抽出する。
    引数の対応関係を崩さないため、遷移先とencpをセットで返す。
    """
    if not value:
        return None

    patterns = [
        r"""formlnk\s*\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)""",
        r"""formlnk\s*\(\s*['"]([^'"]+)['"]\s*,\s*([A-Za-z_$][\w$]*)\s*\)""",
    ]

    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return {
                "href": match.group(1),
                "encp_literal": (
                    match.group(2)
                    if pattern == patterns[0]
                    else None
                ),
                "encp_variable": (
                    match.group(2)
                    if pattern == patterns[1]
                    else None
                ),
                "source": value[:500],
            }

    return None


def extract_schedule_links(html):
    soup = BeautifulSoup(html, "html.parser")
    links = []

    for element in soup.find_all(True):
        attrs = element.attrs

        href = (
            attrs.get("data-pprm-href")
            or attrs.get("href")
        )
        encp = (
            attrs.get("data-pprm-encp")
            or attrs.get("data-pprm-encprm")
        )
        onclick = attrs.get("onclick", "")

        call = extract_encp_from_call(onclick)

        # onclick内の変数名とencp属性を対応させる
        if call and not encp:
            variable = call.get("encp_variable")
            if variable:
                for candidate in element.find_all(
                    attrs={"data-pprm-encp": True}
                ):
                    encp = candidate.get("data-pprm-encp")
                    if encp:
                        break

        if call:
            href = call["href"]

        if not href:
            continue

        if not any(
            token in str(href).lower()
            for token in ("racelist", "race", "result", "odds")
        ):
            continue

        record = {
            "tag": element.name,
            "href": str(href),
            "absolute_href": urljoin(SCHEDULE_URL, str(href)),
            "encp": str(encp) if encp else None,
            "encp_length": len(str(encp)) if encp else 0,
            "onclick": str(onclick)[:500] if onclick else "",
            "formlnk_call": call,
            "element_text": excerpt(
                element.get_text(" ", strip=True), 250
            ),
        }

        # 重複要素を排除するが、hrefとencpの組み合わせは維持する
        key = (
            record["absolute_href"],
            record["encp"],
            record["onclick"],
        )
        if not any(
            (x["absolute_href"], x["encp"], x["onclick"]) == key
            for x in links
        ):
            links.append(record)

    return links


def find_form_post_function(js_text):
    """
    commonSubmit.js の formPost 実装周辺を取得する。
    実装を確認する前に、追加パラメータを推測しない。
    """
    results = []

    patterns = [
        r"formPost\s*:\s*function\s*\(",
        r"formPost\s*=\s*function\s*\(",
        r"function\s+formPost\s*\(",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, js_text):
            start = match.start()
            results.append(js_text[start:start + 5000])

    return results[:5]


def inspect_html(response):
    soup = BeautifulSoup(response.text or "", "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = soup.get_text(" ", strip=True)

    race_numbers = sorted(set(
        int(m.group(1))
        for m in re.finditer(
            r"(?<!\d)(1[0-2]|[1-9])\s*R",
            text,
        )
    ))

    # KEIRIN.JP出走表で使われる選手番号や選手名を広く確認
    rider_numbers = re.findall(
        r"""(?:選手番号|選手名|車番|選手)\s*[:：]?\s*\d{1,6}""",
        text,
    )

    error_markers = [
        "エラーページ",
        "予期せぬエラーが発生しました",
        "指定されたページ",
    ]

    generic_markers = [
        "開催情報｜KEIRIN.JP",
        "開催情報|KEIRIN.JP",
    ]

    is_error = any(marker in text or marker in title for marker in error_markers)
    is_generic = any(marker in title for marker in generic_markers)

    return {
        "title": title,
        "is_error_page": is_error,
        "is_generic_schedule_page": is_generic,
        "race_numbers": race_numbers,
        "race_number_count": len(race_numbers),
        "rider_marker_count": len(rider_numbers),
        "body_excerpt": excerpt(text, 1000),
        "confirmed_race_list": (
            not is_error
            and not is_generic
            and len(race_numbers) >= 2
            and len(rider_numbers) >= 3
        ),
    }


def main():
    result = {
        "created_at": timestamp(),
        "project": "keirin-ai-rebuild",
        "schedule_url": SCHEDULE_URL,
        "checks": {},
        "official_form_post": {},
        "schedule_links": [],
        "schedule_json": {},
        "post_tests": [],
        "confirmed_race_list_count": 0,
        "final_status": "not_confirmed",
        "warnings": [],
    }

    session = requests.Session()
    session.headers.update(HEADERS)

    # 1. 公式開催日程ページを取得
    print("[1/5] 開催日程ページを取得中...")
    try:
        page = session.get(SCHEDULE_URL, timeout=TIMEOUT)
        page.raise_for_status()
    except Exception as exc:
        result["checks"]["schedule_page"] = {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        save(result)
        print("開催日程ページの取得に失敗しました。")
        return 1

    result["checks"]["schedule_page"] = {
        **request_info(page),
        "success": True,
    }

    # 2. 実際の遷移リンクを解析
    print("[2/5] HTML内の実際のリンクとencpを解析中...")
    links = extract_schedule_links(page.text)
    result["schedule_links"] = links

    paired_links = [
        link for link in links
        if link["encp"]
        and link["absolute_href"].startswith(BASE + "/")
    ]

    result["checks"]["links_found"] = len(links)
    result["checks"]["links_with_encp"] = len(paired_links)

    # 3. 公式JSONの正しいフィールドを確認
    print("[3/5] JSJ058の開催情報を解析中...")
    try:
        json_response = session.get(
            SCHEDULE_JSON_URL,
            headers={"Referer": SCHEDULE_URL},
            timeout=TIMEOUT,
        )
        json_response.raise_for_status()
        payload = json_response.json()

        venues = payload.get("kInfo", [])
        venue_summary = []

        for venue in venues if isinstance(venues, list) else []:
            if not isinstance(venue, dict):
                continue

            riders = venue.get("sensyuList", [])
            venue_summary.append({
                "venue_name": venue.get("jyoName"),
                "venue_code": venue.get("bKeirinCd"),
                "keirin_code": venue.get("KeirinCd"),
                "start_date": venue.get("kStartDate"),
                "race_num": venue.get("raceNum"),
                "encp_present": bool(venue.get("encPrm")),
                "encp_length": len(venue.get("encPrm", "")),
                "rider_count": len(riders) if isinstance(riders, list) else 0,
            })

        result["schedule_json"] = {
            **request_info(json_response),
            "success": bool(venue_summary),
            "venue_count": len(venue_summary),
            "venues": venue_summary,
            "result_code": payload.get("resultCd"),
            "request_parameters": payload.get("reqprm"),
        }

    except Exception as exc:
        payload = {}
        result["schedule_json"] = {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    # 4. 実際の遷移先・encpのペアだけを使って試験
    print("[4/5] 実際のリンク先へSJ0301でPOST...")
    for link in paired_links[:8]:
        test = {
            "href": link["href"],
            "absolute_href": link["absolute_href"],
            "encp_length": link["encp_length"],
            "element_text": link["element_text"],
            "success": False,
        }

        try:
            response = session.post(
                link["absolute_href"],
                data={
                    "encp": link["encp"],
                    "disp": "SJ0301",
                },
                headers={
                    "Referer": SCHEDULE_URL,
                    "Origin": BASE,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=TIMEOUT,
                allow_redirects=True,
            )

            html_check = inspect_html(response)

            test.update({
                **request_info(response),
                "response_page": html_check,
                "success": (
                    response.status_code == 200
                    and html_check["confirmed_race_list"]
                ),
            })

        except Exception as exc:
            test["error"] = f"{type(exc).__name__}: {exc}"

        result["post_tests"].append(test)

        if test["success"]:
            result["confirmed_race_list_count"] += 1

        print(
            f"  {link['href']}: "
            + (
                "出走表確認"
                if test["success"]
                else "未確認"
            )
        )
        time.sleep(0.15)

    # 5. commonSubmit.js のフォーム送信実装を記録
    print("[5/5] commonSubmit.jsのformPost実装を調査中...")
    js_url = urljoin(BASE, "/sp/static/js/commonSubmit.js")

    try:
        js_response = session.get(
            js_url,
            headers={"Referer": SCHEDULE_URL},
            timeout=TIMEOUT,
        )
        js_response.raise_for_status()

        functions = find_form_post_function(js_response.text)

        result["official_form_post"] = {
            **request_info(js_response),
            "function_found": bool(functions),
            "formPost_source": functions,
        }

        if not functions:
            result["warnings"].append(
                "commonSubmit.jsからformPost関数を抽出できませんでした。"
            )

    except Exception as exc:
        result["official_form_post"] = {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    result["final_status"] = (
        "race_list_confirmed"
        if result["confirmed_race_list_count"] > 0
        else "not_confirmed"
    )

    if not paired_links:
        result["warnings"].append(
            "開催日程HTMLから遷移先とencpの組を取得できませんでした。"
        )

    if result["confirmed_race_list_count"] == 0:
        result["warnings"].append(
            "出走表取得は未確認です。HTTP 200だけでは成功扱いしていません。"
        )

    save(result)

    print("------------------------------------")
    print("診断完了")
    print(f"リンク候補: {len(links)}")
    print(f"encp付きリンク: {len(paired_links)}")
    print(f"出走表確認数: {result['confirmed_race_list_count']}")
    print(f"最終判定: {result['final_status']}")
    print(f"保存先: {OUT_FILE}")
    print("------------------------------------")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

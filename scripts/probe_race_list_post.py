#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KEIRIN.JP レース一覧遷移・通信診断
---------------------------------
目的:
1. 公式開催日程ページから JavaScript を調査する
2. /sp/json の通信形式とレース一覧関連の処理を探す
3. JSJ058 の開催情報を取得し、encPrm を検証する
4. レース一覧への遷移を、HTTP 200 だけで成功扱いしない
5. 診断結果を data/race_transition_diagnostic.json に保存する

注意:
このスクリプトは通信仕様を調査する診断用。
レース一覧の取得に成功したと確認できない限り、
成功件数には加算しない。
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE = "https://keirin.jp"
SCHEDULE_URL = f"{BASE}/sp/raceschedule"
JSON_URL = f"{BASE}/sp/json"

OUT_FILE = Path("data/race_transition_diagnostic.json")

TIMEOUT = 20
MAX_JS_FILES = 80
MAX_JS_BYTES = 2_000_000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.7",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def text_excerpt(value: str, limit: int = 500) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    return value[:limit]


def response_info(response: requests.Response) -> dict:
    return {
        "status_code": response.status_code,
        "url": response.url,
        "content_type": response.headers.get("Content-Type", ""),
        "bytes": len(response.content),
    }


def fetch(
    session: requests.Session,
    url: str,
    *,
    method: str = "GET",
    params: dict | None = None,
    data: dict | None = None,
    referer: str | None = None,
) -> requests.Response:
    headers = {}
    if referer:
        headers["Referer"] = referer

    if method.upper() == "POST":
        response = session.post(
            url,
            params=params,
            data=data,
            headers=headers,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
    else:
        response = session.get(
            url,
            params=params,
            headers=headers,
            timeout=TIMEOUT,
            allow_redirects=True,
        )

    response.raise_for_status()
    return response


def looks_like_json(response: requests.Response) -> bool:
    content_type = response.headers.get("Content-Type", "").lower()
    body = response.text.lstrip()

    if "json" in content_type:
        return True

    return body.startswith("{") or body.startswith("[")


def parse_json_response(response: requests.Response):
    try:
        return response.json(), None
    except (ValueError, json.JSONDecodeError) as exc:
        return None, str(exc)


def find_js_urls(html: str, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls = []

    for script in soup.find_all("script", src=True):
        src = script.get("src", "").strip()
        if not src:
            continue

        absolute = urljoin(page_url, src)
        parsed = urlparse(absolute)

        if parsed.scheme not in ("http", "https"):
            continue

        if parsed.netloc.lower() != urlparse(BASE).netloc.lower():
            continue

        if absolute not in urls:
            urls.append(absolute)

    return urls


def scan_js_for_relevant_patterns(js_text: str) -> dict:
    """
    収集した JavaScript から通信先・処理識別子を探す。
    見つかった文字列を実際の仕様と断定せず、周辺コードも保存する。
    """
    patterns = {
        "json_endpoint": r"""['"`][^'"`]{0,100}/sp/json[^'"`]{0,100}['"`]""",
        "racelist_endpoint": r"""['"`][^'"`]{0,100}/(?:sp|pc)/racelist[^'"`]{0,100}['"`]""",
        "race_endpoint": r"""['"`][^'"`]{0,100}/(?:sp|pc)/race[^'"`]{0,100}['"`]""",
        "schedule_type": r"""JSJ\d{3}""",
        "enc_parameter": r"""enc(?:Prm|p)""",
        "ajax_terms": r"""ajax|XMLHttpRequest|fetch\(""",
    }

    found = {}

    for name, pattern in patterns.items():
        matches = list(re.finditer(pattern, js_text, flags=re.IGNORECASE))
        snippets = []

        for match in matches[:30]:
            start = max(0, match.start() - 180)
            end = min(len(js_text), match.end() + 260)
            snippets.append({
                "match": match.group(0)[:300],
                "context": js_text[start:end],
            })

        found[name] = {
            "match_count": len(matches),
            "snippets": snippets,
        }

    return found


def find_schedule_types(scan_results: dict) -> list[str]:
    values = set()

    for result in scan_results.values():
        for snippet in result.get("schedule_type", {}).get("snippets", []):
            for value in re.findall(r"JSJ\d{3}", snippet.get("context", "")):
                values.add(value)

    return sorted(values)


def classify_html(response: requests.Response) -> dict:
    """
    HTTP 200 を成功とみなさず、ページ内容を確認する。
    特に「開催情報」などの汎用ページは遷移成功にしない。
    """
    body = response.text or ""
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    normalized_title = re.sub(r"\s+", "", title)
    normalized_body = re.sub(r"\s+", "", soup.get_text(" ", strip=True))

    generic_markers = [
        "開催情報｜KEIRIN.JP",
        "開催情報|KEIRIN.JP",
        "エラー｜KEIRIN.JP",
        "エラー|KEIRIN.JP",
        "ページが見つかりません",
        "指定されたページ",
    ]

    is_generic = any(
        re.sub(r"\s+", "", marker) in normalized_title
        or re.sub(r"\s+", "", marker) in normalized_body[:3000]
        for marker in generic_markers
    )

    race_numbers = sorted({
        int(match.group(1))
        for match in re.finditer(
            r"(?<!\d)(1[0-2]|[1-9])\s*R",
            soup.get_text(" ", strip=True),
        )
    })

    # レース番号だけでは成功と断定しない。
    # ページタイトルと出走表らしい情報を併せて確認する。
    rider_name_candidates = []

    for element in soup.select(
        ".racerName, .racer-name, .playerName, .player-name, "
        ".name, [class*='racerName'], [class*='playerName']"
    ):
        value = element.get_text(" ", strip=True)
        if value and len(value) <= 40:
            rider_name_candidates.append(value)

    rider_name_candidates = list(dict.fromkeys(rider_name_candidates))

    race_list_evidence = (
        len(race_numbers) >= 2
        and len(rider_name_candidates) >= 3
        and not is_generic
    )

    return {
        "title": title,
        "generic_or_error_page": is_generic,
        "race_numbers_found": race_numbers,
        "race_number_count": len(race_numbers),
        "rider_name_candidates": rider_name_candidates[:30],
        "rider_name_candidate_count": len(rider_name_candidates),
        "race_list_evidence": race_list_evidence,
        "body_excerpt": text_excerpt(soup.get_text(" ", strip=True), 700),
    }


def main() -> int:
    result = {
        "created_at": now_iso(),
        "project": "keirin-ai-rebuild",
        "script": "scripts/probe_race_list_post.py",
        "official_site": BASE,
        "checks": {},
        "javascript_analysis": {},
        "schedule": {},
        "race_list_tests": [],
        "confirmed_race_list_success_count": 0,
        "warnings": [],
    }

    session = requests.Session()
    session.headers.update(HEADERS)

    # ---------------------------------------------------------
    # Check 1: 公式日程ページを取得
    # ---------------------------------------------------------
    print("[1/5] 公式開催日程ページを確認中...")

    try:
        schedule_response = fetch(session, SCHEDULE_URL)
        result["checks"]["schedule_page"] = {
            **response_info(schedule_response),
            "success": schedule_response.status_code == 200,
        }

        schedule_html = schedule_response.text
        js_urls = find_js_urls(schedule_html, schedule_response.url)

        result["checks"]["javascript_files_found"] = len(js_urls)
        result["checks"]["javascript_urls"] = js_urls[:MAX_JS_FILES]

    except Exception as exc:
        result["checks"]["schedule_page"] = {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        result["warnings"].append(
            "公式日程ページの取得に失敗したため、後続の調査を制限しました。"
        )
        save_result(result)
        return 1

    # ---------------------------------------------------------
    # Check 2: JSJ058 の応答形式と kInfo を検証
    # ---------------------------------------------------------
    print("[2/5] JSJ058 の開催情報を確認中...")

    try:
        schedule_json_response = fetch(
            session,
            JSON_URL,
            params={"type": "JSJ058"},
            referer=SCHEDULE_URL,
        )

        schedule_json, parse_error = parse_json_response(
            schedule_json_response
        )

        schedule_check = {
            **response_info(schedule_json_response),
            "is_json": looks_like_json(schedule_json_response),
            "json_parse_error": parse_error,
            "success": False,
            "venue_count": 0,
            "venue_samples": [],
        }

        if isinstance(schedule_json, dict):
            venues = schedule_json.get("kInfo", [])

            if isinstance(venues, list):
                valid_venues = []

                for venue in venues:
                    if not isinstance(venue, dict):
                        continue

                    enc = venue.get("encPrm") or venue.get("encp")
                    valid_venues.append({
                        "venue_name": (
                            venue.get("kName")
                            or venue.get("KeirinName")
                            or venue.get("name")
                        ),
                        "venue_code": (
                            venue.get("bKeirinCd")
                            or venue.get("KeirinCd")
                        ),
                        "start_date": venue.get("kStartDate"),
                        "live_flag": venue.get("btnFlgLive"),
                        "encPrm_present": isinstance(enc, str) and bool(enc),
                        "encPrm_length": len(enc) if isinstance(enc, str) else 0,
                        "encPrm": enc if isinstance(enc, str) else None,
                    })

                schedule_check["venue_count"] = len(valid_venues)
                schedule_check["venue_samples"] = valid_venues[:12]

                schedule_check["success"] = (
                    schedule_json_response.status_code == 200
                    and bool(valid_venues)
                    and any(v["encPrm_present"] for v in valid_venues)
                )

        result["checks"]["schedule_json"] = schedule_check

        if not schedule_check["success"]:
            result["warnings"].append(
                "JSJ058 から有効な開催情報と encPrm を確認できませんでした。"
            )

    except Exception as exc:
        result["checks"]["schedule_json"] = {
            "success": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        result["warnings"].append("JSJ058 の取得に失敗しました。")
        schedule_json = None

    # ---------------------------------------------------------
    # Check 3: 公式ページの JavaScript を調査
    # ---------------------------------------------------------
    print("[3/5] JavaScript 内の通信仕様を調査中...")

    js_results = {}
    js_errors = []
    total_bytes = 0

    for index, js_url in enumerate(js_urls[:MAX_JS_FILES], start=1):
        try:
            js_response = fetch(
                session,
                js_url,
                referer=SCHEDULE_URL,
            )

            content = js_response.content

            if len(content) > MAX_JS_BYTES:
                js_errors.append({
                    "url": js_url,
                    "error": "ファイルが上限サイズを超えたためスキップ",
                    "bytes": len(content),
                })
                continue

            total_bytes += len(content)
            encoding = js_response.encoding or "utf-8"
            js_text = content.decode(encoding, errors="replace")

            relevant = scan_js_for_relevant_patterns(js_text)

            # 通信に関係するファイルだけ結果に残す
            relevant_count = sum(
                value["match_count"]
                for value in relevant.values()
            )

            if relevant_count:
                js_results[js_url] = {
                    "http": response_info(js_response),
                    "patterns": relevant,
                }

        except Exception as exc:
            js_errors.append({
                "url": js_url,
                "error": f"{type(exc).__name__}: {exc}",
            })

        if index % 10 == 0:
            print(f"  JavaScript {index}/{min(len(js_urls), MAX_JS_FILES)}")

        time.sleep(0.05)

    result["javascript_analysis"] = {
        "files_examined": min(len(js_urls), MAX_JS_FILES),
        "total_bytes_examined": total_bytes,
        "relevant_files": js_results,
        "errors": js_errors,
        "note": (
            "検出した文字列は候補です。周辺コードを確認するまで、"
            "通信パラメータや type の意味を確定扱いしません。"
        ),
    }

    # ---------------------------------------------------------
    # Check 4: encPrm を使ったレース一覧遷移を厳密に評価
    # ---------------------------------------------------------
    print("[4/5] レース一覧への遷移を検証中...")

    venues = []
    if isinstance(schedule_json, dict):
        raw_venues = schedule_json.get("kInfo", [])
        if isinstance(raw_venues, list):
            venues = [
                v for v in raw_venues
                if isinstance(v, dict)
                and isinstance(v.get("encPrm") or v.get("encp"), str)
                and (v.get("encPrm") or v.get("encp"))
            ]

    for venue in venues[:8]:
        encp = venue.get("encPrm") or venue.get("encp")
        venue_name = (
            venue.get("kName")
            or venue.get("KeirinName")
            or venue.get("name")
            or "名称不明"
        )

        # 既知の失敗パターンを再現し、成功と誤判定しない。
        # POST のレスポンスを出走表の証拠と照合する。
        test = {
            "venue_name": venue_name,
            "venue_code": (
                venue.get("bKeirinCd")
                or venue.get("KeirinCd")
            ),
            "encPrm_length": len(encp),
            "attempts": [],
            "confirmed_success": False,
        }

        for endpoint, disp in (
            ("/sp/racelist", "SJ0305"),
            ("/pc/racelist", "SJ0305"),
            ("/sp/racelist", "SJ0301"),
        ):
            attempt = {
                "endpoint": endpoint,
                "disp": disp,
                "success": False,
            }

            try:
                response = fetch(
                    session,
                    urljoin(BASE, endpoint),
                    method="POST",
                    data={
                        "encp": encp,
                        "disp": disp,
                    },
                    referer=SCHEDULE_URL,
                )

                page_check = classify_html(response)

                attempt.update({
                    **response_info(response),
                    "page_check": page_check,
                    "success": (
                        response.status_code == 200
                        and page_check["race_list_evidence"]
                    ),
                })

            except Exception as exc:
                attempt["error"] = f"{type(exc).__name__}: {exc}"

            test["attempts"].append(attempt)

            if attempt["success"]:
                test["confirmed_success"] = True
                result["confirmed_race_list_success_count"] += 1
                break

        result["race_list_tests"].append(test)

        print(
            f"  {venue_name}: "
            + (
                "レース一覧を確認"
                if test["confirmed_success"]
                else "レース一覧の取得を確認できず"
            )
        )

    # ---------------------------------------------------------
    # Check 5: 結果の保存と最終判定
    # ---------------------------------------------------------
    print("[5/5] 診断結果を保存中...")

    result["final_status"] = (
        "race_list_confirmed"
        if result["confirmed_race_list_success_count"] > 0
        else "race_list_not_confirmed"
    )

    if result["confirmed_race_list_success_count"] == 0:
        result["warnings"].append(
            "レース一覧の取得成功は確認できませんでした。"
            "HTTP 200 やリダイレクトだけでは成功扱いしていません。"
        )

    save_result(result)

    print("--------------------------------------------")
    print("診断完了")
    print(f"開催情報JSON: {result['checks'].get('schedule_json', {}).get('success', False)}")
    print(f"確認済みレース一覧取得数: {result['confirmed_race_list_success_count']}")
    print(f"最終判定: {result['final_status']}")
    print(f"保存先: {OUT_FILE}")
    print("--------------------------------------------")

    return 0 if result["checks"].get("schedule_json", {}).get("success") else 1


def save_result(result: dict) -> None:
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n中断されました。", file=sys.stderr)
        sys.exit(130)

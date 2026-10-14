#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KEIRIN.JP 出走表通信診断
- 公式開催情報 JSON JSJ058 から encPrm / reqprm を取得
- /sp/racelist と /pc/racelist を比較
- GET / POST、パラメータ名・送信形式を比較
- HTML内のレース番号・選手情報を検証
- 結果を data/race_transition_diagnostic.json に保存

このスクリプトは診断専用。
update.py、today.json、予想データは変更しない。
"""

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE = "https://keirin.jp"
SCHEDULE_URL = f"{BASE}/sp/raceschedule"
JSON_URL = f"{BASE}/sp/json?type=JSJ058"

OUTPUT = Path("data/race_transition_diagnostic.json")

TIMEOUT = 20
MAX_EVENTS = 3
MAX_TESTS = 36
PAUSE_SECONDS = 0.4

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/json,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.7",
}


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def save_json(data):
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_text(session, url, **kwargs):
    response = session.get(url, timeout=TIMEOUT, **kwargs)
    response.encoding = response.apparent_encoding or "utf-8"
    return response


def get_json(session, url):
    response = get_text(session, url)

    info = {
        "url": response.url,
        "status_code": response.status_code,
        "content_type": response.headers.get("Content-Type", ""),
        "bytes": len(response.content),
    }

    response.raise_for_status()

    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"JSON解析失敗: {type(exc).__name__}: {exc}; "
            f"先頭={response.text[:300]!r}"
        ) from exc

    return payload, info


def normalize_event(item):
    """公式JSONの開催データを、元の項目を残して正規化する。"""
    if not isinstance(item, dict):
        return None

    venue = (
        item.get("jyoName")
        or item.get("venueName")
        or item.get("jyo")
        or ""
    )

    enc_prm = (
        item.get("encPrm")
        or item.get("encp")
        or item.get("encprm")
        or ""
    )

    req_prm = item.get("reqprm") or ""

    if not venue and not enc_prm:
        return None

    return {
        "venue": str(venue),
        "venue_code": str(
            item.get("bKeirinCd")
            or item.get("KeirinCd")
            or item.get("jyoCd")
            or ""
        ),
        "race_num": str(item.get("raceNum") or ""),
        "start_date": str(item.get("kStartDate") or ""),
        "encPrm": str(enc_prm),
        "reqprm": str(req_prm),
        "sensyuList_type": type(item.get("sensyuList")).__name__,
        "sensyuList_count": (
            len(item["sensyuList"])
            if isinstance(item.get("sensyuList"), (list, dict))
            else None
        ),
        "raw": item,
    }


def find_events(payload):
    """JSONの階層が変わっても kInfo を探す。"""
    found = []

    def walk(obj):
        if isinstance(obj, dict):
            kinfo = obj.get("kInfo")

            if isinstance(kinfo, list):
                found.extend(kinfo)

            for key, value in obj.items():
                if key != "kInfo":
                    walk(value)

        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(payload)

    # kInfo が見つからない場合は、JSON全体の辞書を候補にする。
    if not found:
        def fallback(obj):
            if isinstance(obj, dict):
                if (
                    any(k in obj for k in ("encPrm", "encp"))
                    and any(k in obj for k in ("jyoName", "raceNum"))
                ):
                    found.append(obj)
                for value in obj.values():
                    fallback(value)
            elif isinstance(obj, list):
                for value in obj:
                    fallback(value)

        fallback(payload)

    events = []
    seen = set()

    for item in found:
        event = normalize_event(item)
        if not event:
            continue

        key = (
            event["venue"],
            event["venue_code"],
            event["race_num"],
            event["encPrm"],
        )

        if key in seen:
            continue

        seen.add(key)
        events.append(event)

    return events


def html_metrics(html):
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text(" ", strip=True)

    # 1R～12Rなどのレース番号
    race_matches = re.findall(
        r"(?<!\d)(?:1[0-2]|[1-9])\s*R(?![A-Za-z])",
        text,
        flags=re.IGNORECASE,
    )

    # 「1 佐藤」などの表記だけに依存しないよう、
    # ページ内の選手名らしい日本語文字列も記録する。
    japanese_names = re.findall(
        r"[一-龥々〆ヵヶ]{2,8}\s*[一-龥々〆ヵヶ]{0,8}",
        text,
    )

    rider_classes = []
    for tag in soup.find_all(True):
        classes = tag.get("class", [])
        if isinstance(classes, str):
            classes = [classes]

        for cls in classes:
            if re.search(
                r"rider|sensyu|player|entry|racelist|member",
                str(cls),
                re.IGNORECASE,
            ):
                rider_classes.append(str(cls))

    error_terms = [
        "ページが見つかりません",
        "エラーが発生しました",
        "不正なリクエスト",
        "該当する情報がありません",
        "システムエラー",
        "アクセスできません",
    ]

    error_hits = [term for term in error_terms if term in text]

    return {
        "title": soup.title.get_text(strip=True) if soup.title else "",
        "text_length": len(text),
        "race_number_count": len(set(race_matches)),
        "race_numbers": sorted(
            set(race_matches),
            key=lambda value: int(re.search(r"\d+", value).group()),
        ),
        "japanese_text_sample": japanese_names[:15],
        "rider_related_classes": sorted(set(rider_classes))[:30],
        "error_markers": error_hits,
        "text_sample": text[:1000],
    }


def looks_like_racecard(metrics):
    """
    レース番号だけでは成功と判定しない。
    返却内容を保存し、候補の比較に利用する。
    """
    return (
        metrics["text_length"] >= 500
        and metrics["race_number_count"] >= 2
        and not metrics["error_markers"]
    )


def candidate_payloads(event):
    """
    正しいパラメータ形式は未確定。
    既知のキーを使った候補を比較するだけで、
    どれかが正しいと事前に決めつけない。
    """
    enc = event.get("encPrm", "")
    req = event.get("reqprm", "")

    candidates = []

    if enc:
        candidates.extend([
            ("encp", {"encp": enc, "disp": "SJ0301"}),
            ("encPrm", {"encPrm": enc, "disp": "SJ0301"}),
            ("encprm", {"encprm": enc, "disp": "SJ0301"}),
            ("encp_only", {"encp": enc}),
            ("encPrm_only", {"encPrm": enc}),
        ])

    if req:
        candidates.extend([
            ("reqprm", {"reqprm": req, "disp": "SJ0301"}),
            ("reqprm_only", {"reqprm": req}),
        ])

    if enc and req:
        candidates.extend([
            (
                "encp_and_reqprm",
                {"encp": enc, "reqprm": req, "disp": "SJ0301"},
            ),
            (
                "encPrm_and_reqprm",
                {"encPrm": enc, "reqprm": req, "disp": "SJ0301"},
            ),
        ])

    # 同一のパラメータ候補は重複させない。
    result = []
    seen = set()

    for name, params in candidates:
        signature = json.dumps(
            params,
            sort_keys=True,
            ensure_ascii=False,
        )
        if signature in seen:
            continue
        seen.add(signature)
        result.append((name, params))

    return result


def inspect_response(response, method, endpoint, candidate_name, params):
    response.encoding = response.apparent_encoding or "utf-8"

    content_type = response.headers.get("Content-Type", "")
    body = response.text or ""

    record = {
        "method": method,
        "endpoint": endpoint,
        "candidate": candidate_name,
        "params_keys": list(params.keys()),
        "status_code": response.status_code,
        "final_url": response.url,
        "content_type": content_type,
        "bytes": len(response.content),
        "redirect_history": [
            {
                "status_code": item.status_code,
                "url": item.url,
                "location": item.headers.get("Location", ""),
            }
            for item in response.history
        ],
    }

    if "json" in content_type.lower():
        try:
            parsed = response.json()
            record["json_response"] = parsed
            record["json_top_level_type"] = type(parsed).__name__
            record["html_metrics"] = None
            record["racecard_candidate"] = False
            return record
        except Exception:
            record["json_parse_error"] = True

    metrics = html_metrics(body)
    record["html_metrics"] = metrics
    record["racecard_candidate"] = looks_like_racecard(metrics)

    return record


def main():
    started = datetime.now(timezone.utc).isoformat()

    report = {
        "diagnostic": "KEIRIN.JP racecard communication investigation",
        "started_at_utc": started,
        "schedule_url": SCHEDULE_URL,
        "json_url": JSON_URL,
        "events": [],
        "tests": [],
        "summary": {},
        "warnings": [
            "この診断では、正しい送信パラメータを事前に確定していません。",
            "HTTP 200だけでは出走表取得成功と判定しません。",
            "診断対象は公開ページへの読み取りリクエストのみです。",
        ],
    }

    session = make_session()

    print("[1/5] 開催日程ページを取得中...")

    try:
        schedule = get_text(session, SCHEDULE_URL)
        report["schedule"] = {
            "status_code": schedule.status_code,
            "final_url": schedule.url,
            "bytes": len(schedule.content),
        }
        schedule.raise_for_status()
    except Exception as exc:
        report["schedule_error"] = (
            f"{type(exc).__name__}: {exc}"
        )
        print("開催日程ページの取得に失敗:", report["schedule_error"])

    print("[2/5] 公式JSON JSJ058を取得中...")

    try:
        payload, json_info = get_json(session, JSON_URL)
        report["json_source"] = json_info
        events = find_events(payload)
        report["events_found"] = len(events)

    except Exception as exc:
        report["json_error"] = f"{type(exc).__name__}: {exc}"
        save_json(report)
        print("公式JSONの取得に失敗:", report["json_error"])
        print(f"診断結果を保存しました: {OUTPUT}")
        raise SystemExit(1)

    print(f"開催候補: {len(events)}")

    # encPrmがあり、開催名または開催コードを持つ候補を優先する。
    events.sort(
        key=lambda e: (
            bool(e.get("encPrm")),
            bool(e.get("venue")),
            bool(e.get("venue_code")),
        ),
        reverse=True,
    )

    selected = events[:MAX_EVENTS]

    for event in selected:
        event_copy = {
            key: value
            for key, value in event.items()
            if key != "raw"
        }
        report["events"].append(event_copy)

    if not selected:
        report["summary"] = {
            "status": "no_event_candidates",
            "message": "JSJ058内に開催候補を見つけられませんでした。",
        }
        save_json(report)
        print("開催候補がありません。JSON構造を診断ファイルに保存しました。")
        print(f"保存先: {OUTPUT}")
        return

    print("[3/5] encPrm / reqprmの有無を確認中...")

    for event in selected:
        print(
            f"  {event['venue'] or '(開催名不明)'}"
            f" / encPrm={'あり' if event['encPrm'] else 'なし'}"
            f" / reqprm={'あり' if event['reqprm'] else 'なし'}"
        )

    print("[4/5] GET / POSTとパラメータ形式を比較中...")

    endpoints = [
        "/sp/racelist",
        "/pc/racelist",
    ]

    tests_done = 0

    for event in selected:
        payload_candidates = candidate_payloads(event)

        if not payload_candidates:
            report["tests"].append({
                "venue": event["venue"],
                "status": "skipped",
                "reason": "encPrm / reqprm がありません。",
            })
            continue

        referer = SCHEDULE_URL

        for endpoint in endpoints:
            target_url = urljoin(BASE, endpoint)

            for candidate_name, params in payload_candidates:
                if tests_done >= MAX_TESTS:
                    break

                for method in ("POST", "GET"):
                    if tests_done >= MAX_TESTS:
                        break

                    test_headers = {
                        "Referer": referer,
                        "Origin": BASE,
                    }

                    try:
                        if method == "POST":
                            response = session.post(
                                target_url,
                                data=params,
                                headers=test_headers,
                                timeout=TIMEOUT,
                                allow_redirects=True,
                            )
                        else:
                            response = session.get(
                                target_url,
                                params=params,
                                headers=test_headers,
                                timeout=TIMEOUT,
                                allow_redirects=True,
                            )

                        record = inspect_response(
                            response,
                            method,
                            endpoint,
                            candidate_name,
                            params,
                        )
                        record["venue"] = event["venue"]
                        record["venue_code"] = event["venue_code"]
                        record["race_num_hint"] = event["race_num"]

                        report["tests"].append(record)

                        metric = record.get("html_metrics") or {}

                        print(
                            f"  {tests_done + 1:02d} "
                            f"{method:4s} {endpoint:14s} "
                            f"{candidate_name:22s} "
                            f"HTTP {record['status_code']} "
                            f"文字数={metric.get('text_length', 0)} "
                            f"レース番号={metric.get('race_number_count', 0)} "
                            f"候補={'YES' if record.get('racecard_candidate') else 'no'}"
                        )

                    except Exception as exc:
                        report["tests"].append({
                            "venue": event["venue"],
                            "venue_code": event["venue_code"],
                            "method": method,
                            "endpoint": endpoint,
                            "candidate": candidate_name,
                            "params_keys": list(params.keys()),
                            "error": f"{type(exc).__name__}: {exc}",
                            "racecard_candidate": False,
                        })

                        print(
                            f"  {tests_done + 1:02d} "
                            f"{method} {endpoint} "
                            f"{candidate_name}: "
                            f"{type(exc).__name__}: {exc}"
                        )

                    tests_done += 1
                    time.sleep(PAUSE_SECONDS)

            if tests_done >= MAX_TESTS:
                break

        if tests_done >= MAX_TESTS:
            break

    print("[5/5] 診断結果を集計中...")

    successes = [
        item
        for item in report["tests"]
        if item.get("racecard_candidate")
    ]

    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["summary"] = {
        "status": (
            "candidate_found"
            if successes
            else "not_confirmed"
        ),
        "events_found": len(events),
        "events_tested": len(selected),
        "tests_executed": tests_done,
        "racecard_candidates": len(successes),
        "successful_candidates": [
            {
                "venue": item.get("venue"),
                "method": item.get("method"),
                "endpoint": item.get("endpoint"),
                "candidate": item.get("candidate"),
                "status_code": item.get("status_code"),
                "final_url": item.get("final_url"),
                "race_numbers": (
                    (item.get("html_metrics") or {}).get("race_numbers", [])
                ),
            }
            for item in successes
        ],
    }

    save_json(report)

    print("------------------------------------")
    print("診断完了")
    print(f"開催候補: {len(events)}")
    print(f"検証リクエスト: {tests_done}")
    print(f"出走表候補: {len(successes)}")
    print(f"最終判定: {report['summary']['status']}")
    print(f"保存先: {OUTPUT}")
    print("------------------------------------")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KEIRIN.JP 出走表データ取得先・通信経路 一括診断

調査対象:
1. 公式開催日程ページ
2. 公式JSON JSJ058の全階層
3. 開催日・競輪場・選手データの構造
4. HTML内のリンク・フォーム・JavaScript
5. JavaScript内の出走表URL・JSON取得処理
6. 開催情報に含まれる選手データ
7. 実際に発見したURLへの読み取りリクエスト

出力:
data/race_transition_diagnostic.json

update.py / today.json / AI予想データは変更しない。
"""

import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


BASE = "https://keirin.jp"
SCHEDULE_URL = f"{BASE}/sp/raceschedule"
JSON_URL = f"{BASE}/sp/json?type=JSJ058"

OUTPUT = Path("data/race_transition_diagnostic.json")

TIMEOUT = 20
MAX_EVENTS = 8
MAX_JS_FILES = 30
MAX_JS_BYTES = 1_500_000
PAUSE_SECONDS = 0.2

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/json,"
        "application/javascript,*/*;q=0.8"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.7",
}


def now_jst():
    return datetime.now(JST)


def save_json(data):
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def make_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def fetch(session, url, **kwargs):
    return session.get(
        url,
        timeout=TIMEOUT,
        allow_redirects=True,
        **kwargs,
    )


def fetch_json(session, url):
    response = fetch(session, url)
    response.raise_for_status()

    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(
            f"JSON解析失敗: {exc}; "
            f"本文先頭={response.text[:300]!r}"
        ) from exc

    return payload, {
        "requested_url": url,
        "final_url": response.url,
        "status_code": response.status_code,
        "content_type": response.headers.get("Content-Type", ""),
        "bytes": len(response.content),
    }


def shorten(value, limit=1200):
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)

    if len(text) > limit:
        return text[:limit] + "...(省略)"
    return text


def find_events(payload):
    """JSON全体から開催情報候補を抽出する。"""
    found = []
    visited = set()

    def walk(obj, path="$"):
        if isinstance(obj, dict):
            if (
                any(k in obj for k in ("encPrm", "encp", "encprm"))
                and any(
                    k in obj
                    for k in (
                        "jyoName",
                        "venueName",
                        "jyo",
                        "raceNum",
                        "sensyuList",
                    )
                )
            ):
                signature = (
                    str(obj.get("jyoName", "")),
                    str(obj.get("bKeirinCd", "")),
                    str(obj.get("raceNum", "")),
                    str(obj.get("encPrm", obj.get("encp", ""))),
                )

                if signature not in visited:
                    visited.add(signature)
                    found.append({
                        "path": path,
                        "raw": obj,
                    })

            for key, value in obj.items():
                walk(value, f"{path}.{key}")

        elif isinstance(obj, list):
            for index, value in enumerate(obj):
                walk(value, f"{path}[{index}]")

    walk(payload)
    return found


def get_value(obj, *keys):
    for key in keys:
        value = obj.get(key)
        if value not in (None, ""):
            return value
    return ""


def event_info(item):
    raw = item["raw"]

    start_date = str(get_value(
        raw,
        "kStartDate",
        "startDate",
        "kaisaiDate",
        "start_date",
    ))

    enc = str(get_value(raw, "encPrm", "encp", "encprm"))
    req = str(get_value(raw, "reqprm"))

    sensyu = raw.get("sensyuList")

    return {
        "json_path": item["path"],
        "venue": str(get_value(
            raw,
            "jyoName",
            "venueName",
            "jyo",
        )),
        "venue_code": str(get_value(
            raw,
            "bKeirinCd",
            "KeirinCd",
            "jyoCd",
        )),
        "race_num": str(get_value(raw, "raceNum")),
        "start_date": start_date,
        "encPrm": enc,
        "reqprm": req,
        "raw_keys": list(raw.keys()),
        "sensyuList_type": type(sensyu).__name__,
        "sensyuList_count": (
            len(sensyu)
            if isinstance(sensyu, (list, dict))
            else None
        ),
        "sensyuList_sample": shorten(sensyu, 5000),
        "raw_relevant_fields": {
            key: value
            for key, value in raw.items()
            if any(
                word in key.lower()
                for word in (
                    "sensyu",
                    "rider",
                    "race",
                    "entry",
                    "member",
                    "comment",
                    "line",
                    "enc",
                    "req",
                    "name",
                )
            )
        },
    }


def parse_date(value):
    value = str(value or "")

    match = re.search(r"(20\d{6})", value)
    if match:
        try:
            return datetime.strptime(
                match.group(1), "%Y%m%d"
            ).date()
        except ValueError:
            pass

    return None


def event_priority(event):
    today = now_jst().date()
    date = parse_date(event.get("start_date"))

    if date is None:
        date_rank = 0
    elif date >= today:
        date_rank = 3
    else:
        date_rank = 1

    return (
        date_rank,
        date.isoformat() if date else "",
        bool(event.get("encPrm")),
        bool(event.get("venue")),
    )


def inspect_html(html, page_url):
    soup = BeautifulSoup(html or "", "html.parser")

    links = []
    for tag in soup.find_all("a", href=True):
        links.append({
            "text": tag.get_text(" ", strip=True)[:150],
            "href": urljoin(page_url, tag["href"]),
        })

    forms = []
    for form in soup.find_all("form"):
        forms.append({
            "action": urljoin(
                page_url,
                form.get("action") or page_url,
            ),
            "method": (form.get("method") or "GET").upper(),
            "name": form.get("name", ""),
            "id": form.get("id", ""),
            "inputs": [
                {
                    "name": field.get("name", ""),
                    "type": field.get("type", ""),
                    "value": field.get("value", ""),
                }
                for field in form.find_all(
                    ["input", "select", "button"]
                )
            ][:80],
        })

    scripts = []
    for tag in soup.find_all("script"):
        src = tag.get("src")

        if src:
            scripts.append({
                "src": urljoin(page_url, src),
                "inline": False,
            })
        elif tag.string or tag.get_text(strip=True):
            scripts.append({
                "src": "",
                "inline": True,
                "text": (tag.string or tag.get_text())[:5000],
            })

    # onclick属性から画面遷移とPOST送信の手掛かりを収集
    click_handlers = []
    for tag in soup.find_all(attrs={"onclick": True}):
        handler = str(tag.get("onclick", "")).strip()
        if handler:
            click_handlers.append({
                "tag": tag.name,
                "text": tag.get_text(" ", strip=True)[:150],
                "onclick": handler[:2000],
            })

    # onclick以外の属性に埋め込まれた遷移情報も調査
    for tag in soup.find_all(True):
        for attr in ("data-url", "data-href", "action"):
            value = tag.get(attr)
            if value:
                click_handlers.append({
                    "tag": tag.name,
                    "attribute": attr,
                    "value": urljoin(page_url, str(value))[:2000],
                })

    title = soup.title.get_text(strip=True) if soup.title else ""
    page_text = soup.get_text(" ", strip=True)

    return {
        "title": title,
        "text_length": len(page_text),
        "text_sample": page_text[:1500],
        "links": links,
        "forms": forms,
        "scripts": scripts,
        "click_handlers": click_handlers[:500],
    }


def scan_javascript(text):
    """出走表・通信先の手掛かりをJavaScriptから抽出する。"""
    patterns = {
        "race_urls": (
            r"""["'`](?:[^"'`]{0,100})?"""
            r"""(?:racelist|racecard|raceprogram|raceinfo)"""
            r"""[^"'`]{0,200}["'`]"""
        ),
        "json_urls": (
            r"""["'`][^"'`]{0,250}"""
            r"""(?:/json|\.json|type=JSJ\d+)[^"'`]{0,250}["'`]"""
        ),
        "parameter_names": (
            r"""(?:encPrm|encp|reqprm|sensyuList|"""
            r"""disp|raceNum|jyoCd|bKeirinCd)"""
        ),
        "submit_calls": (
            r"""(?:form\.submit|submit\(|location\.href|"""
            r"""location\.assign|location\.replace)"""
        ),
        "api_calls": (
            r"""(?:fetch\(|XMLHttpRequest|\.ajax\(|"""
            r"""axios\.|getJSON\(|post\()"""
        ),
    }

    result = {}

    for name, pattern in patterns.items():
        matches = re.findall(
            pattern,
            text or "",
            flags=re.IGNORECASE,
        )

        result[name] = list(dict.fromkeys(
            str(value)[:500] for value in matches
        ))[:100]

    return result

def download_javascript(session, script_url):
    parsed = urlparse(script_url)

    if parsed.netloc and parsed.netloc != urlparse(BASE).netloc:
        return {
            "url": script_url,
            "status": "external_host_skipped",
        }

    try:
        response = fetch(session, script_url)

        result = {
            "url": script_url,
            "final_url": response.url,
            "status_code": response.status_code,
            "content_type": response.headers.get("Content-Type", ""),
            "bytes": len(response.content),
        }

        if not response.ok:
            result["status"] = "http_error"
            return result

        if len(response.content) > MAX_JS_BYTES:
            result["status"] = "too_large"
            return result

        text = response.content.decode(
            response.encoding or "utf-8",
            errors="replace",
        )

        result["scan"] = scan_javascript(text)

        patterns = [
            r"commonSubmit",
            r"formPost",
            r"formGet",
            r"fnHdn[A-Za-z0-9_]*",
            r"encPrm",
            r"encp",
            r"reqprm",
            r"location\.href",
            r"location\.replace",
            r"Post\s*\(",
            r"racelist",
            r"racecard",
            r"raceprogram",
            r"disp\s*[:=]",
        ]

        contexts = []
        seen = set()

        for pattern in patterns:
            for match in re.finditer(
                pattern,
                text,
                flags=re.IGNORECASE,
            ):
                start = max(0, match.start() - 250)
                end = min(len(text), match.end() + 400)
                snippet = text[start:end]

                if snippet in seen:
                    continue

                seen.add(snippet)

                contexts.append({
                    "matched_pattern": pattern,
                    "position": match.start(),
                    "context": snippet,
                })

                if len(contexts) >= 100:
                    break

            if len(contexts) >= 100:
                break

        result["source_contexts"] = contexts

        result["form_target_hints"] = list(
            dict.fromkeys(
                re.findall(
                    r"""(?i)(?:action|url|target)\s*[:=]\s*["']([^"']+)["']""",
                    text,
                )
            )
        )[:100]

        result["status"] = "ok"
        return result

    except Exception as exc:
        return {
            "url": script_url,
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
        }


def test_candidate(
    session,
    url,
    params=None,
    method="GET",
    referer=None,
):
    """GETまたはPOSTで候補ページを検証する。"""
    params = params or {}
    method = method.upper()

    try:
        headers = {}
        if referer:
            headers["Referer"] = referer

        if method == "POST":
            response = session.post(
                url,
                data=params,
                headers=headers,
                timeout=TIMEOUT,
                allow_redirects=True,
            )
        else:
            response = session.get(
                url,
                params=params or None,
                headers=headers,
                timeout=TIMEOUT,
                allow_redirects=True,
            )

        response.encoding = (
            response.apparent_encoding or "utf-8"
        )

        content_type = response.headers.get("Content-Type", "")
        body = response.text or ""

        result = {
            "method": method,
            "requested_url": url,
            "final_url": response.url,
            "params": params,
            "status_code": response.status_code,
            "content_type": content_type,
            "bytes": len(response.content),
            "redirects": [
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
                result["response_type"] = "json"
                result["json_top_level_type"] = type(parsed).__name__

                if isinstance(parsed, dict):
                    result["json_keys"] = list(parsed.keys())

                result["json_sample"] = shorten(parsed, 5000)
            except Exception:
                result["response_type"] = "json_parse_failed"
                result["body_sample"] = body[:1500]
        else:
            metrics = inspect_html(body, response.url)

            result["response_type"] = "html"
            result["title"] = metrics["title"]
            result["text_length"] = metrics["text_length"]
            result["body_sample"] = metrics["text_sample"][:1000]

            result["page_scripts"] = [
                item for item in metrics.get("scripts", [])
                if item.get("src")
            ][:100]

            result["page_forms"] = metrics.get("forms", [])[:50]

            result["page_click_handlers"] = (
                metrics.get("click_handlers", [])[:100]
            )

            result["rider_data_hints"] = [
                {
                    "name": name,
                    "count": len(re.findall(name, body)),
                }
                for name in ["車番", "選手名", "競走得点", "出走表", "ライン"]
            ]

            # 出走表らしい内容が返ったかを後から確認できるようにする
            result["response_links"] = [
                link for link in metrics["links"]
                if re.search(
                    r"出走表|選手|競走得点|車番|racelist|racecard",
                    link.get("text", "") + " " + link.get("href", ""),
                    re.IGNORECASE,
                )
            ][:50]

        return result

    except Exception as exc:
        return {
            "method": method,
            "requested_url": url,
            "params": params,
            "error": f"{type(exc).__name__}: {exc}",
        }


def main():
    report = {
        "diagnostic": "KEIRIN.JP race-list and entry data investigation",
        "started_at_jst": now_jst().isoformat(),
        "schedule_url": SCHEDULE_URL,
        "json_url": JSON_URL,
        "schedule": {},
        "json_source": {},
        "events_found": 0,
        "events": [],
        "html_discovery": {},
        "javascript_files": [],
        "candidate_tests": [],
        "summary": {},
        "warnings": [
            "HTTP 200だけでは出走表取得成功と判断しない。",
            "取得できたデータと推測した情報を区別する。",
            "update.pyとtoday.jsonは変更しない。",
        ],
    }

    session = make_session()

    print("=" * 50)
    print(" KEIRIN.JP 出走表・通信先 一括診断")
    print("=" * 50)

    # 1. 開催日程ページ
    print("[1/6] 開催日程ページを解析中...")

    try:
        response = fetch(session, SCHEDULE_URL)
        response.raise_for_status()

        report["schedule"] = {
            "status_code": response.status_code,
            "final_url": response.url,
            "bytes": len(response.content),
        }

        html_info = inspect_html(
            response.text,
            response.url,
        )

        report["html_discovery"] = html_info

    except Exception as exc:
        report["schedule_error"] = (
            f"{type(exc).__name__}: {exc}"
        )
        html_info = {"scripts": [], "links": [], "forms": []}

    # 2. 公式JSON
    print("[2/6] 公式JSON JSJ058を解析中...")

    try:
        payload, json_info = fetch_json(session, JSON_URL)
        report["json_source"] = json_info

        event_candidates = find_events(payload)
        events = [
            event_info(item)
            for item in event_candidates
        ]

        report["events_found"] = len(events)

        events.sort(
            key=event_priority,
            reverse=True,
        )

        report["events"] = events[:MAX_EVENTS]

        # JSONの上位構造も保存
        report["json_structure"] = {
            "top_level_type": type(payload).__name__,
            "top_level_keys": (
                list(payload.keys())
                if isinstance(payload, dict)
                else []
            ),
        }

    except Exception as exc:
        report["json_error"] = (
            f"{type(exc).__name__}: {exc}"
        )
        save_json(report)
        print("公式JSON取得失敗:", report["json_error"])
        raise SystemExit(1)

    print(f"開催候補: {len(events)}")

    # 3. JavaScriptを解析
    print("[3/6] JavaScriptの通信先を調査中...")

    script_urls = []

    for script in html_info.get("scripts", []):
        if script.get("src"):
            script_urls.append(script["src"])
        elif script.get("inline"):
            report.setdefault("inline_scripts", []).append({
                "scan": scan_javascript(script.get("text", "")),
                "text_sample": script.get("text", "")[:1500],
            })

    # HTML内に現れたJavaScript参照を重複排除
    script_urls = list(dict.fromkeys(script_urls))
    script_urls = script_urls[:MAX_JS_FILES]

    for index, script_url in enumerate(script_urls, 1):
        print(f"  JS {index}/{len(script_urls)}")
        js_result = download_javascript(session, script_url)
        report["javascript_files"].append(js_result)
        time.sleep(PAUSE_SECONDS)

    # 4. 発見したリンクを分類
    print("[4/6] 出走表関連リンクを抽出中...")

    relevant_links = []

    for link in html_info.get("links", []):
        combined = (
            link.get("text", "") + " " + link.get("href", "")
        )

        if re.search(
            r"出走表|racelist|racecard|raceprogram|"
            r"sensyu|entry|raceinfo",
            combined,
            re.IGNORECASE,
        ):
            relevant_links.append(link)

    report["relevant_links"] = relevant_links[:150]

    # JavaScriptから出走表関連URL候補を集約
    js_url_candidates = []

    for item in report["javascript_files"]:
        for match in (
            item.get("scan", {}).get("race_urls", [])
            if item.get("scan")
            else []
        ):
            js_url_candidates.append({
                "source": item.get("url"),
                "match": match,
            })

    report["javascript_url_candidates"] = js_url_candidates

    # 5. 開催JSONの中身をまとめる
    print("[5/6] 選手データの構造を整理中...")

    rider_data_events = []

    for event in report["events"]:
        rider_data_events.append({
            "venue": event["venue"],
            "venue_code": event["venue_code"],
            "race_num": event["race_num"],
            "start_date": event["start_date"],
            "json_path": event["json_path"],
            "raw_keys": event["raw_keys"],
            "sensyuList_type": event["sensyuList_type"],
            "sensyuList_count": event["sensyuList_count"],
            "sensyuList_sample": event["sensyuList_sample"],
            "raw_relevant_fields": event["raw_relevant_fields"],
        })

    report["rider_data_inspection"] = rider_data_events

    # 6. HTMLのクリック処理から実際のPOST通信を検証
    print("[6/6] 出走表へのPOST通信を検証中...")
    test_specs = []
    seen_specs = set()
    for item in html_info.get(
        "click_handlers", []
    ):
        onclick = str(
            item.get("onclick", "")
        )
        match = re.search(
            r"""formlnk\(\s*['"]([^'"]+)['"]"""
            r"""\s*,\s*['"]([^'"]+)['"]\s*\)""",
            onclick,
        )
        if not match:
            continue
        path = match.group(1)
        encp = match.group(2)
        url = urljoin(BASE, path)
        # 公式サイト内のURLだけを検証
        parsed_url = urlparse(url)
        if parsed_url.netloc != urlparse(BASE).netloc:
            continue
        if parsed_url.path != "/sp/racelist":
            continue
        signature = (url, encp)
        if signature in seen_specs:
            continue
        seen_specs.add(signature)
        test_specs.append({
            "url": url,
            "params": {
                "encp": encp,
                "disp": "SJ0301",
            },
            "source_text": item.get("text", ""),
        })
    print(
        f"発見したPOST候補: {len(test_specs)}"
    )
    for spec in test_specs[:8]:
        print(
            "  POST検証:",
            spec["url"],
            "開催:",
            spec["source_text"][:60],
        )
        result = test_candidate(
            session,
            spec["url"],
            params=spec["params"],
            method="POST",
            referer=SCHEDULE_URL,
        )
        result["source_text"] = (
            spec["source_text"]
        )
        report["candidate_tests"].append(
            result
        )
        time.sleep(PAUSE_SECONDS)
    report["post_candidates_found"] = len(
        test_specs
    )
    # 7. POST応答ページが読み込むJavaScriptを追加調査
    print("[7/7] 出走表ページのJavaScriptを追加調査中...")

    followup_pattern = re.compile(
        r"/(?:"
        r"SJ0301(?:_v|_c)?|"
        r"SJ0304(?:_v|_c)?|"
        r"SJ0305(?:_v|_c)?|"
        r"SJ0306(?:_v|_c)?|"
        r"SC0201(?:_v|_c)?|"
        r"commonJSON|commonLoad|commonLink|commonSubmit"
        r")\.js$",
        re.IGNORECASE,
    )

    followup_urls = []
    seen_followup_urls = set()

    for candidate in report.get("candidate_tests", []):
        for script in candidate.get("page_scripts", []):
            script_url = script.get("src", "")

            if not script_url:
                continue

            parsed = urlparse(script_url)

            if parsed.netloc != urlparse(BASE).netloc:
                continue

            if not followup_pattern.search(parsed.path):
                continue

            if script_url in seen_followup_urls:
                continue

            seen_followup_urls.add(script_url)
            followup_urls.append(script_url)

    report["followup_javascript_files"] = []

    print(f"追加調査するJavaScript: {len(followup_urls)}件")

    for index, script_url in enumerate(followup_urls, 1):
        print(
            f"  追加JS {index}/{len(followup_urls)}: "
            f"{script_url.rsplit('/', 1)[-1]}"
        )

        js_result = download_javascript(session, script_url)

        report["followup_javascript_files"].append(js_result)

        if js_result.get("status") == "ok":
            scan = js_result.get("scan", {})

            print(
                "    通信候補:",
                len(scan.get("race_urls", [])),
                "URL候補:",
                len(scan.get("json_urls", [])),
                "JSON候補"
            )

        time.sleep(PAUSE_SECONDS)

    print(
        "追加JavaScript取得:",
        sum(
            1 for item in report["followup_javascript_files"]
            if item.get("status") == "ok"
        ),
        "/",
        len(followup_urls),
    )
    # 集計
    js_ok = sum(
        1 for item in report["javascript_files"]
        if item.get("status") == "ok"
    )

    events_with_sensyu = sum(
        1 for event in report["events"]
        if event.get("sensyuList_count") is not None
    )

    report["finished_at_jst"] = now_jst().isoformat()

    report["summary"] = {
        "status": "diagnostic_completed",
        "events_found": len(events),
        "events_inspected": len(report["events"]),
        "events_with_sensyuList": events_with_sensyu,
        "javascript_files_found": len(script_urls),
        "javascript_files_read": js_ok,
        "relevant_links_found": len(relevant_links),
        "javascript_url_candidates": len(js_url_candidates),
        "candidate_urls_tested": len(report["candidate_tests"]),
        "confirmed_racecard_url": False,
        "next_step": (
            "JSONの選手データ構造とJavaScriptの通信先を確認し、"
            "実際の出走表取得先を特定する。"
        ),
    }

    save_json(report)

    print()
    print("=" * 50)
    print(" 診断完了")
    print("=" * 50)
    print(f"開催候補: {len(events)}")
    print(f"選手データ構造を調査した開催: {events_with_sensyu}")
    print(f"解析したJavaScript: {js_ok}/{len(script_urls)}")
    print(f"出走表関連リンク: {len(relevant_links)}")
    print(f"検証した候補URL: {len(report['candidate_tests'])}")
    print(f"保存先: {OUTPUT}")
    print("=" * 50)


if __name__ == "__main__":
    main()

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "race_list_post_probe.json"

BASE = "https://keirin.jp"
JSON_URL = f"{BASE}/sp/json"
RACE_LIST_URL = f"{BASE}/sp/racelist"
RACE_INFO_URL = f"{BASE}/sp/race"

TODAY = datetime.now(JST).strftime("%Y%m%d")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Referer": f"{BASE}/sp/top",
}


def summarize_html(response):
    soup = BeautifulSoup(response.text, "html.parser")

    return {
        "status_code": response.status_code,
        "final_url": response.url,
        "content_type": response.headers.get("Content-Type", ""),
        "bytes": len(response.content),
        "title": soup.title.get_text(" ", strip=True) if soup.title else "",
        "body_text": soup.get_text(" ", strip=True)[:6000],
        "links": [
            {
                "text": a.get_text(" ", strip=True)[:150],
                "href": a.get("href"),
                "onclick": a.get("onclick"),
            }
            for a in soup.select("a[href], a[onclick]")[:80]
        ],
        "forms": [
            {
                "action": form.get("action"),
                "method": form.get("method"),
                "inputs": [
                    {
                        "name": item.get("name"),
                        "value": item.get("value"),
                    }
                    for item in form.select("input")
                ],
            }
            for form in soup.select("form")[:10]
        ],
        "script_urls": [
            script.get("src")
            for script in soup.select("script[src]")
        ],
    }


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)

    results = {
        "generated_at": datetime.now(JST).isoformat(),
        "target_date": TODAY,
        "schedule_status": None,
        "venues": [],
        "error": None,
    }

    try:
        # 1. 開催情報を公式JSONから取得
        response = session.get(
            JSON_URL,
            params={"type": "JSJ058", "kday": TODAY},
            timeout=30,
        )
        response.raise_for_status()
        schedule = response.json()
        results["schedule_status"] = response.status_code

        venues = schedule.get("kInfo", [])
        print("開催情報取得:", len(venues), "件")

        # 2. 公式JavaScriptと同じPOST形式でレース一覧を開く
        for venue in venues:
            encp = venue.get("encPrm")
            name = venue.get("jyoName", "")
            code = venue.get("bKeirinCd", "")

            if not encp:
                continue

            print(f"\n検証中: {name}（{code}）")

            venue_result = {
                "venue_name": name,
                "venue_code": code,
                "encp_present": True,
                "race_list": None,
                "race_info": None,
                "errors": [],
            }

            payloads = [
                (
                    "race_list",
                    RACE_LIST_URL,
                    {"encp": encp, "disp": "SJ0305"},
                ),
                (
                    "race_info",
                    RACE_INFO_URL,
                    {"encp": encp, "disp": "SJ0315"},
                ),
            ]

            for key, url, payload in payloads:
                try:
                    response = session.post(
                        url,
                        data=payload,
                        headers={
                            "Referer": f"{BASE}/sp/top",
                            "Origin": BASE,
                        },
                        timeout=30,
                        allow_redirects=True,
                    )

                    summary = summarize_html(response)
                    venue_result[key] = summary

                    print(
                        key,
                        "HTTP", response.status_code,
                        "|", summary["title"],
                        "|", summary["bytes"], "bytes",
                    )
                    print("本文:", summary["body_text"][:350])

                except requests.RequestException as exc:
                    venue_result["errors"].append(
                        f"{key}: {exc}"
                    )
                    print(key, "取得エラー:", exc)

            results["venues"].append(venue_result)

    except Exception as exc:
        results["error"] = repr(exc)
        print("全体エラー:", repr(exc))

    OUTPUT.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n検証結果を保存:", OUTPUT)
    print("開催場の検証数:", len(results["venues"]))


if __name__ == "__main__":
    main()

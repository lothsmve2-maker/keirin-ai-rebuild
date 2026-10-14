import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "race_list_post_probe.json"

BASE = "https://keirin.jp"
TOP_URL = f"{BASE}/sp/top"
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
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def inspect_response(response):
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    body = soup.get_text(" ", strip=True)

    return {
        "status_code": response.status_code,
        "final_url": response.url,
        "title": title,
        "bytes": len(response.content),
        "body_text": body[:5000],
        "is_error_page": (
            "/error" in response.url
            or "予期せぬエラー" in body
            or "エラーページ" in title
        ),
        "has_race_list": (
            "出走表" in body
            or "開催情報" in title
        ),
        "cookies": requests.utils.dict_from_cookiejar(
            response.cookies
        ),
    }


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    result = {
        "generated_at": datetime.now(JST).isoformat(),
        "target_date": TODAY,
        "steps": {},
        "venues": [],
        "error": None,
    }

    try:
        # 1. 先にトップページを開き、セッションを確立する
        print("1. 公式トップページを取得中...")
        top = session.get(TOP_URL, timeout=30)
        top.raise_for_status()

        result["steps"]["top"] = {
            "status_code": top.status_code,
            "final_url": top.url,
            "cookies": requests.utils.dict_from_cookiejar(
                session.cookies
            ),
        }

        print("   HTTP:", top.status_code)
        print("   Cookie数:", len(session.cookies))

        # 2. トップページから開催情報JSONを取得
        print("\n2. 開催情報JSONを取得中...")
        schedule_response = session.get(
            JSON_URL,
            params={
                "type": "JSJ058",
                "kday": TODAY,
            },
            headers={
                "Referer": TOP_URL,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=30,
        )
        schedule_response.raise_for_status()

        schedule = schedule_response.json()
        venues = schedule.get("kInfo", [])
print("\n=== 開催情報の確認 ===")

for venue in venues:
    print({
        "競輪場": venue.get("jyoName"),
        "ボタン表示条件": venue.get("btnFlgLive"),
        "開催情報あり": bool(venue.get("encPrm")),
        "開催情報の長さ": len(str(venue.get("encPrm") or "")),
    })

print("====================")
        result["steps"]["schedule"] = {
            "status_code": schedule_response.status_code,
            "venue_count": len(venues),
        }

        print("   開催場数:", len(venues))

        # 3. 公式サイトと同じパラメータでPOSTする
        for venue in venues:
            encp = venue.get("encPrm")
            name = venue.get("jyoName", "")
            code = str(
                venue.get("bKeirinCd")
                or venue.get("KeirinCd")
                or ""
            )

            item = {
                "venue_name": name,
                "venue_code": code,
                "encp_present": bool(encp),
                "race_list": None,
                "race_info": None,
                "errors": [],
            }

            print(f"\n3. {name}（{code}）")

            if not encp:
                item["errors"].append("encPrmがありません")
                result["venues"].append(item)
                continue

            tests = [
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

            for key, url, payload in tests:
                try:
                    response = session.post(
                        url,
                        data=payload,
                        headers={
                            "Referer": TOP_URL,
                            "Origin": BASE,
                            "Content-Type": (
                                "application/x-www-form-urlencoded"
                            ),
                        },
                        timeout=30,
                        allow_redirects=True,
                    )

                    details = inspect_response(response)
                    item[key] = details

                    print(
                        f"   {key}: "
                        f"HTTP {details['status_code']} | "
                        f"{details['title']} | "
                        f"{details['bytes']} bytes"
                    )
                    print(
                        "   転送先:",
                        details["final_url"],
                    )
                    print(
                        "   エラーページ:",
                        details["is_error_page"],
                    )

                except Exception as exc:
                    item["errors"].append(
                        f"{key}: {type(exc).__name__}: {exc}"
                    )
                    print(f"   {key} 取得失敗:", exc)

            result["venues"].append(item)

        # 4. 診断結果を保存
        successful = sum(
            1
            for venue in result["venues"]
            if venue.get("race_list")
            and not venue["race_list"]["is_error_page"]
        )

        result["summary"] = {
            "venue_count": len(result["venues"]),
            "race_list_success_count": successful,
            "session_cookie_count": len(session.cookies),
        }

        print("\n==============================")
        print("調査完了")
        print("開催場:", len(result["venues"]))
        print("レース一覧取得成功:", successful)
        print("==============================")

    except Exception as exc:
        result["error"] = (
            f"{type(exc).__name__}: {exc}"
        )
        print("全体エラー:", result["error"])

    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("結果保存:", OUTPUT)


if __name__ == "__main__":
    main()

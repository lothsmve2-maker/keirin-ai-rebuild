import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests
JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "race_list_post_probe.json"
BASE = "https://keirin.jp"
TOP_URL = BASE + "/sp/top"
JSON_URL = BASE + "/sp/json"
RACE_LIST_URL = BASE + "/sp/racelist"
RACE_INFO_URL = BASE + "/sp/race"
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
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(response.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    body = soup.get_text(" ", strip=True)
    is_error = (
        "/error" in response.url
        or "予期せぬエラー" in body
        or "エラーページ" in title
    )
    return {
        "status_code": response.status_code,
        "final_url": response.url,
        "title": title,
        "bytes": len(response.content),
        "is_error_page": is_error,
        "has_race_list": (
            not is_error
            and "出走表" in body
            and "開催情報" in title
        ),
        "body_text": body[:2000],
    }
def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "generated_at": datetime.now(JST).isoformat(),
        "target_date": TODAY,
        "steps": {},
        "venue_diagnostics": [],
        "venues": [],
        "error": None,
    }
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        print("1. 公式トップページを取得中")
        top = session.get(TOP_URL, timeout=30)
        top.raise_for_status()
        result["steps"]["top"] = {
            "status_code": top.status_code,
            "final_url": top.url,
            "cookie_count": len(session.cookies),
        }
        print("2. 開催情報を取得中")
        response = session.get(
            JSON_URL,
            params={"type": "JSJ058", "kday": TODAY},
            headers={
                "Referer": TOP_URL,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=30,
        )
        response.raise_for_status()
        schedule = response.json()
        venues = schedule.get("kInfo", [])
        if not isinstance(venues, list):
            raise ValueError("開催情報の形式が想定と異なります")
        result["steps"]["schedule"] = {
            "status_code": response.status_code,
            "venue_count": len(venues),
        }
        print("\n=== 開催情報の確認 ===")
        for venue in venues:
            diagnostic = {
                "venue_name": venue.get("jyoName"),
                "btnFlgLive": venue.get("btnFlgLive"),
                "encPrm_present": bool(venue.get("encPrm")),
                "encPrm_length": len(
                    str(venue.get("encPrm") or "")
                ),
                "encPrm_type": type(
                    venue.get("encPrm")
                ).__name__,
            }
            result["venue_diagnostics"].append(diagnostic)
            print(diagnostic)
        print("======================")
        for venue in venues:
            name = venue.get("jyoName", "")
            code = str(
                venue.get("bKeirinCd")
                or venue.get("KeirinCd")
                or ""
            )
            encp = venue.get("encPrm")
            item = {
                "venue_name": name,
                "venue_code": code,
                "encp_present": bool(encp),
                "race_list": None,
                "race_info": None,
                "errors": [],
            }
            print("\n会場:", name, code)
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
                    res = session.post(
                        url,
                        data=payload,
                        headers={
                            "Referer": TOP_URL,
                            "Origin": BASE,
                        },
                        timeout=30,
                        allow_redirects=True,
                    )
                    details = inspect_response(res)
                    item[key] = details
                    print(
                        key,
                        "HTTP", details["status_code"],
                        details["title"],
                        "エラー:", details["is_error_page"],
                    )
                except Exception as exc:
                    message = (
                        f"{key}: {type(exc).__name__}: {exc}"
                    )
                    item["errors"].append(message)
                    print(message)
            result["venues"].append(item)
        success_count = sum(
            1
            for item in result["venues"]
            if item["race_list"]
            and item["race_list"]["has_race_list"]
        )
        result["summary"] = {
            "venue_count": len(result["venues"]),
            "race_list_success_count": success_count,
            "session_cookie_count": len(session.cookies),
        }
        print("\n調査完了")
        print("開催場数:", len(result["venues"]))
        print("レース一覧確認成功:", success_count)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        print("エラー:", result["error"])
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("結果保存:", OUTPUT)
if __name__ == "__main__":
    main()

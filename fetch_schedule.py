import json
import re
import sys
import urllib.request
from datetime import datetime, date, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from html.parser import HTMLParser


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

VENUES_FILE = DATA_DIR / "venues.json"
OUTPUT_FILE = DATA_DIR / "today_schedule.json"

SOURCE_URL = "https://www.keirin.jp/sp/raceschedule"

JST = ZoneInfo("Asia/Tokyo")

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; KeirinAI/1.0)"
)

DATE_RANGE_PATTERN = re.compile(
    r"(?<!\d)"
    r"(\d{1,2})\s*/\s*(\d{1,2})"
    r"\s*(?:\([^)]*\))?"
    r"\s*[～〜~〜\-－–—]\s*"
    r"(\d{1,2})\s*/\s*(\d{1,2})"
    r"\s*(?:\([^)]*\))?"
)


class PageTextParser(HTMLParser):
    """HTMLからテキストを抽出する。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texts = []

    def handle_data(self, data):
        value = " ".join(data.split())

        if value:
            self.texts.append(value)


def now_jst():
    return datetime.now(JST)


def iso_now():
    return now_jst().isoformat(timespec="seconds")


def compact(value):
    """全角空白などを除いて比較する。"""
    value = str(value or "")
    value = value.replace("\u3000", "")
    value = value.replace(" ", "")
    value = value.replace("\t", "")
    return value.strip()


def load_venues():
    if not VENUES_FILE.exists():
        raise FileNotFoundError(
            "data/venues.json がありません"
        )

    data = json.loads(
        VENUES_FILE.read_text(encoding="utf-8")
    )

    venues = data.get("venues")

    if not isinstance(venues, list) or not venues:
        raise ValueError(
            "venues.json の venues が空です"
        )

    result = {}

    for venue in venues:
        code = str(venue.get("venue_code", ""))
        name = str(venue.get("name", "")).strip()

        if not code or not name:
            raise ValueError(
                "競輪場マスターにコードまたは名称がありません"
            )

        key = compact(name)

        if key in result:
            raise ValueError(
                f"競輪場名が重複しています: {name}"
            )

        result[key] = {
            "venue_code": code,
            "name": name,
            "region": venue.get("region"),
            "prefecture": venue.get("prefecture"),
        }

    return result


def fetch_html():
    request = urllib.request.Request(
        SOURCE_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=30,
    ) as response:

        status = response.status
        raw = response.read()

        charset = (
            response.headers.get_content_charset()
            or "utf-8"
        )

    if status != 200:
        raise RuntimeError(
            f"公式サイトのHTTPステータスが異常です: {status}"
        )

    if not raw:
        raise RuntimeError(
            "公式サイトから空のデータが返されました"
        )

    html = raw.decode(
        charset,
        errors="replace",
    )

    return html, status, len(raw)


def parse_date_range(text, base_year):
    """
    10/14～10/16 のような開催期間を日付に変換する。
    年をまたぐ場合にも対応する。
    """

    match = DATE_RANGE_PATTERN.search(text)

    if not match:
        return None

    start_month = int(match.group(1))
    start_day = int(match.group(2))
    end_month = int(match.group(3))
    end_day = int(match.group(4))

    if not 1 <= start_month <= 12:
        return None

    if not 1 <= end_month <= 12:
        return None

    try:
        start_date = date(
            base_year,
            start_month,
            start_day,
        )

        end_year = base_year

        if (
            end_month < start_month
            or (
                end_month == start_month
                and end_day < start_day
            )
        ):
            end_year += 1

        end_date = date(
            end_year,
            end_month,
            end_day,
        )

    except ValueError:
        return None

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "matched_text": match.group(0),
        "match_end": match.end(),
    }


def is_generic_text(text):
    """
    開催名ではないと考えられる一般的な表示を除外する。
    """
    value = compact(text)

    if not value:
        return True

    generic = {
        "表示",
        "開催日程",
        "グレードレース開催日程",
        "競輪場",
        "開く",
        "Image",
    }

    return value in generic


def parse_schedule(html, venue_master, target_date):
    parser = PageTextParser()
    parser.feed(html)

    texts = parser.texts

    if not texts:
        raise RuntimeError(
            "公式ページからテキストを抽出できませんでした"
        )

    venue_names = sorted(
        venue_master.keys(),
        key=len,
        reverse=True,
    )

    current_venue = None
    events = []
    seen_events = set()

    for index, text in enumerate(texts):

        normalized = compact(text)

        # 競輪場名の見出しを認識する。
        matched_venue = None

        for venue_name in venue_names:

            if normalized == venue_name:
                matched_venue = venue_name
                break

            if normalized.startswith(
                venue_name + "開催がありません"
            ):
                matched_venue = venue_name
                break

        if matched_venue:
            current_venue = matched_venue

        if not current_venue:
            continue

        # 開催期間を探す。
        date_info = parse_date_range(
            text,
            target_date.year,
        )

        date_text_index = index

        # 日付が複数のテキスト要素に分割されている場合に備える。
        if not date_info:

            for extra in range(1, 4):

                if index + extra >= len(texts):
                    break

                combined = " ".join(
                    texts[index:index + extra + 1]
                )

                candidate = parse_date_range(
                    combined,
                    target_date.year,
                )

                if candidate:
                    date_info = candidate
                    date_text_index = index + extra
                    break

        if not date_info:
            continue

        # 開催期間の直後にある開催名を探す。
        event_name = ""

        remainder = text[
            date_info["match_end"]:
        ].strip()

        if remainder:
            event_name = remainder

        if not event_name:

            for next_index in range(
                date_text_index + 1,
                min(date_text_index + 5, len(texts)),
            ):

                candidate = texts[next_index].strip()

                if not candidate:
                    continue

                if parse_date_range(
                    candidate,
                    target_date.year,
                ):
                    break

                if is_generic_text(candidate):
                    continue

                # 次の競輪場見出しなら開催名にしない。
                candidate_compact = compact(candidate)

                if candidate_compact in venue_master:
                    break

                event_name = candidate
                break

        venue = venue_master[current_venue]

        event_key = (
            venue["venue_code"],
            date_info["start_date"],
            date_info["end_date"],
            compact(event_name),
        )

        if event_key in seen_events:
            continue

        seen_events.add(event_key)

        start_date = date.fromisoformat(
            date_info["start_date"]
        )

        end_date = date.fromisoformat(
            date_info["end_date"]
        )

        events.append({
            "venue_code": venue["venue_code"],
            "venue_name": venue["name"],
            "region": venue["region"],
            "prefecture": venue["prefecture"],
            "event_name": event_name or None,
            "start_date": date_info["start_date"],
            "end_date": date_info["end_date"],
            "is_active_today": (
                start_date <= target_date <= end_date
            ),
            "race_count": None,
            "race_count_status": "not_acquired",
            "source": SOURCE_URL,
        })

    events.sort(
        key=lambda item: (
            item["start_date"],
            item["venue_code"],
        )
    )

    if not events:
        raise RuntimeError(
            "開催期間を1件も抽出できませんでした。"
            "誤った日程を保存しないため処理を中止します。"
        )

    return events


def build_output(events, target_date, http_status, body_bytes):
    active_events = [
        event
        for event in events
        if event["is_active_today"]
    ]

    return {
        "schema_version": 1,
        "generated_at": iso_now(),
        "target_date": target_date.isoformat(),
        "status": "ok",
        "source": {
            "name": "KEIRIN.JP",
            "url": SOURCE_URL,
            "http_status": http_status,
            "body_bytes": body_bytes,
            "retrieved_at": iso_now(),
        },
        "summary": {
            "event_count": len(events),
            "active_event_count": len(active_events),
            "race_count": None,
            "race_count_status": "not_acquired",
        },
        "events": events,
        "active_today": active_events,
        "data_notes": [
            "開催日程は公式ページから抽出しています。",
            "レース数・出走表・選手情報はまだ取得していません。",
            "取得できていない値は推測で補完していません。",
            "開催日程の抽出結果は実データとの照合が必要です。",
        ],
    }


def main():
    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    target_date = now_jst().date()

    print("=" * 42)
    print(" KEIRIN AI SCHEDULE DATA v1")
    print("=" * 42)
    print(f"対象日: {target_date.isoformat()}")

    try:
        venue_master = load_venues()

        print(
            f"競輪場マスター: {len(venue_master)}場"
        )

        html, http_status, body_bytes = fetch_html()

        print(f"HTTP status: {http_status}")
        print(f"取得サイズ: {body_bytes} bytes")

        events = parse_schedule(
            html,
            venue_master,
            target_date,
        )

        output = build_output(
            events,
            target_date,
            http_status,
            body_bytes,
        )

        OUTPUT_FILE.write_text(
            json.dumps(
                output,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        print("-" * 42)
        print(
            f"開催日程抽出: {len(events)}件"
        )
        print(
            f"本日開催: {len(output['active_today'])}件"
        )

        for event in output["active_today"]:
            print(
                f"  {event['venue_name']}: "
                f"{event['start_date']} ～ "
                f"{event['end_date']}"
            )

        print("-" * 42)
        print(f"保存先: {OUTPUT_FILE}")
        print("状態: OK")
        print(
            "注意: 出走表・レース数は未取得です。"
        )

    except Exception as exc:
        print("=" * 42)
        print("開催日程の取得に失敗しました")
        print(f"原因: {exc}")
        print(
            "既存の正常データを誤って上書きしないため、"
            "出力を更新せず終了します。"
        )

        sys.exit(1)


if __name__ == "__main__":
    main()

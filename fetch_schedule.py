import json
import re
import sys
import urllib.request
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

VENUES_FILE = DATA_DIR / "venues.json"
OUTPUT_FILE = DATA_DIR / "today_schedule.json"

SOURCE_URL = "https://www.keirin.jp/sp/raceschedule"

JST = ZoneInfo("Asia/Tokyo")

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; KeirinAI/2.0)"
)

DATE_RANGE_PATTERN = re.compile(
    r"(?<!\d)"
    r"(\d{1,2})\s*/\s*(\d{1,2})"
    r"\s*(?:\([^)]*\))?"
    r"\s*[～〜~\-－–—]\s*"
    r"(\d{1,2})\s*/\s*(\d{1,2})"
    r"\s*(?:\([^)]*\))?"
)


def now_jst():
    return datetime.now(JST)


def compact(value):
    value = str(value or "")
    value = value.replace("\u3000", "")
    value = value.replace("\xa0", "")
    value = re.sub(r"\s+", "", value)
    return value.strip()


class TableRowParser(HTMLParser):
    """
    HTMLのtr単位でテキストを保持する。
    ページ全体のテキストを平坦化しない。
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)

        self.rows = []
        self.current_row = None
        self.row_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag == "tr":
            if self.current_row is None:
                self.current_row = []
                self.row_depth = 1
            else:
                self.row_depth += 1

        elif self.current_row is not None:
            if tag == "br":
                self.current_row.append(" ")

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "tr" and self.current_row is not None:
            self.row_depth -= 1

            if self.row_depth <= 0:
                text = " ".join(self.current_row)
                text = " ".join(text.split())

                if text:
                    self.rows.append(text)

                self.current_row = None
                self.row_depth = 0

    def handle_data(self, data):
        if self.current_row is not None:
            value = " ".join(data.split())

            if value:
                self.current_row.append(value)


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
        code = str(venue.get("venue_code", "")).strip()
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
            f"HTTPステータス異常: {status}"
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
    match = DATE_RANGE_PATTERN.search(text)

    if not match:
        return None

    start_month = int(match.group(1))
    start_day = int(match.group(2))
    end_month = int(match.group(3))
    end_day = int(match.group(4))

    try:
        start_date = date(
            base_year,
            start_month,
            start_day,
        )

        end_year = base_year

        if end_month < start_month:
            end_year += 1
        elif (
            end_month == start_month
            and end_day < start_day
        ):
            end_year += 1

        end_date = date(
            end_year,
            end_month,
            end_day,
        )

    except ValueError:
        return None

    if end_date < start_date:
        return None

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "matched_text": match.group(0),
    }


def find_venue_in_row(row, venue_master):
    """
    競輪場名が行内に独立して存在する場合のみ認識する。
    開催名の一部に似た文字列があっても認識しない。
    """

    normalized = compact(row)

    for key in sorted(
        venue_master.keys(),
        key=len,
        reverse=True,
    ):
        if normalized == key:
            return key

        if normalized.startswith(key + "開催がありません"):
            return key

    return None


def parse_schedule(html, venue_master, target_date):
    parser = TableRowParser()
    parser.feed(html)
    parser.close()

    rows = parser.rows

    if not rows:
        raise RuntimeError(
            "HTMLの行を抽出できませんでした。"
            "公式ページの構造を確認する必要があります。"
        )

    print(f"HTML行数: {len(rows)}")

    venue_names = sorted(
        venue_master.keys(),
        key=len,
        reverse=True,
    )

    current_venue = None
    events = []
    seen = set()

    for row_index, row in enumerate(rows):
        normalized_row = compact(row)

        # 競輪場の見出し行を認識
        matched_venue = find_venue_in_row(
            row,
            venue_master,
        )

        if matched_venue:
            current_venue = matched_venue

            # 「開催がありません」は開催情報ではない
            if "開催がありません" in normalized_row:
                current_venue = matched_venue
                continue

            # 競輪場名だけの行は開催情報ではない
            if normalized_row == matched_venue:
                continue

        if current_venue is None:
            continue

        date_info = parse_date_range(
            row,
            target_date.year,
        )

        if not date_info:
            continue

        # 開催日が含まれる行の情報だけを利用する。
        # 別の行の開催名を推測して結び付けない。
        match = DATE_RANGE_PATTERN.search(row)

        if not match:
            continue

        before = row[:match.start()].strip()
        after = row[match.end():].strip()

        # 開催名は日付より後ろにあるテキストを優先
        event_name = after

        if not event_name:
            # 同じ行の前側に開催名が含まれている場合のみ利用
            event_name = before

        event_name = event_name.strip(" |｜\t")

        # 開催名が取得できない場合も、日付から開催名を作らない
        if not event_name:
            event_name = None

        venue = venue_master[current_venue]

        event_key = (
            venue["venue_code"],
            date_info["start_date"],
            date_info["end_date"],
        )

        # 同じ競輪場・同じ期間の重複登録はエラーにする。
        # 別開催として勝手に採用しない。
        if event_key in seen:
            raise RuntimeError(
                "同じ競輪場・開催期間が重複しました: "
                f"{venue['name']} "
                f"{date_info['start_date']}～"
                f"{date_info['end_date']}"
            )

        seen.add(event_key)

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
            "event_name": event_name,
            "start_date": date_info["start_date"],
            "end_date": date_info["end_date"],
            "is_active_today": (
                start_date <= target_date <= end_date
            ),
            "race_count": None,
            "race_count_status": "not_acquired",
            "source": SOURCE_URL,
            "source_row_index": row_index,
        })

    if not events:
        raise RuntimeError(
            "開催日程を抽出できませんでした。"
            "誤ったデータの保存を防ぐため中止します。"
        )

    events.sort(
        key=lambda item: (
            item["start_date"],
            item["venue_code"],
        )
    )

    # 同じ競輪場の開催期間が重なっていないか検証
    by_venue = {}

    for event in events:
        by_venue.setdefault(
            event["venue_code"],
            [],
        ).append(event)

    for code, venue_events in by_venue.items():
        venue_events.sort(
            key=lambda item: item["start_date"]
        )

        for previous, current in zip(
            venue_events,
            venue_events[1:],
        ):
            previous_end = date.fromisoformat(
                previous["end_date"]
            )

            current_start = date.fromisoformat(
                current["start_date"]
            )

            if current_start <= previous_end:
                raise RuntimeError(
                    "同じ競輪場の開催期間が重なっています。"
                    "日程を自動確定せず中止します: "
                    f"{previous['venue_name']} "
                    f"{previous['start_date']}～"
                    f"{previous['end_date']} / "
                    f"{current['start_date']}～"
                    f"{current['end_date']}"
                )

    return events


def build_output(
    events,
    target_date,
    http_status,
    body_bytes,
):
    active_events = [
        event
        for event in events
        if event["is_active_today"]
    ]

    return {
        "schema_version": 2,
        "generated_at": now_jst().isoformat(
            timespec="seconds"
        ),
        "target_date": target_date.isoformat(),
        "status": "ok",
        "source": {
            "name": "KEIRIN.JP",
            "url": SOURCE_URL,
            "http_status": http_status,
            "body_bytes": body_bytes,
            "retrieved_at": now_jst().isoformat(
                timespec="seconds"
            ),
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
            "公式ページのHTML行単位で開催日程を抽出しています。",
            "抽出成功は全項目の正しさを保証するものではありません。",
            "同一競輪場の開催期間重複はエラーとして停止します。",
            "レース数・出走表・選手情報は未取得です。",
            "未取得の値を推測で補完していません。",
        ],
    }


def main():
    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    target_date = now_jst().date()

    print("=" * 42)
    print(" KEIRIN AI SCHEDULE DATA v2")
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

        # 既存ファイルを直接途中書き込みしない
        temporary_file = OUTPUT_FILE.with_suffix(".tmp")

        temporary_file.write_text(
            json.dumps(
                output,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        # JSONとして再読込できることを確認してから置換
        json.loads(
            temporary_file.read_text(
                encoding="utf-8"
            )
        )

        temporary_file.replace(OUTPUT_FILE)

        print("-" * 42)
        print(f"開催日程抽出: {len(events)}件")
        print(
            f"本日開催: {len(output['active_today'])}件"
        )

        for event in output["active_today"]:
            print(
                f"  {event['venue_name']}: "
                f"{event['start_date']}～"
                f"{event['end_date']} "
                f"({event['event_name'] or '開催名未取得'})"
            )

        print("-" * 42)
        print(f"保存先: {OUTPUT_FILE}")
        print("状態: OK")
        print("注意: レース数・出走表は未取得です。")

    except Exception as exc:
        print("=" * 42)
        print("開催日程の検証に失敗しました")
        print(f"原因: {exc}")
        print(
            "既存のtoday_schedule.jsonは上書きしていません。"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

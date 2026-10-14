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

DATE_PATTERN = re.compile(
    r"(?<!\d)"
    r"(\d{1,2})\s*/\s*(\d{1,2})"
    r"\s*(?:\([^)]*\))?"
    r"\s*[～〜~\-－–—]\s*"
    r"(\d{1,2})\s*/\s*(\d{1,2})"
    r"\s*(?:\([^)]*\))?"
)


def now_jst():
    return datetime.now(JST)


def normalize(value):
    value = str(value or "")
    value = value.replace("\u3000", " ")
    value = value.replace("\xa0", " ")
    return " ".join(value.split()).strip()


def compact(value):
    return re.sub(r"\s+", "", normalize(value))


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

    by_code = {}

    for item in venues:
        code = str(
            item.get("venue_code", "")
        ).strip()

        name = normalize(item.get("name", ""))

        if not code or not name:
            raise ValueError(
                "競輪場コードまたは名称がありません"
            )

        if code in by_code:
            raise ValueError(
                f"競輪場コードが重複しています: {code}"
            )

        by_code[code] = {
            "venue_code": code,
            "name": name,
            "region": item.get("region"),
            "prefecture": item.get("prefecture"),
        }

    return by_code


def fetch_html():
    request = urllib.request.Request(
        SOURCE_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": (
                "ja,en-US;q=0.8,en;q=0.6"
            ),
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
            "公式サイトから空のHTMLが返されました"
        )

    html = raw.decode(
        charset,
        errors="replace",
    )

    return html, status, len(raw)


def parse_date_range(text, target_year):
    match = DATE_PATTERN.search(text)

    if not match:
        return None

    start_month = int(match.group(1))
    start_day = int(match.group(2))
    end_month = int(match.group(3))
    end_day = int(match.group(4))

    try:
        start_date = date(
            target_year,
            start_month,
            start_day,
        )

        end_year = target_year

        if end_month < start_month:
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


class ScheduleParser(HTMLParser):
    """
    公式HTMLから競輪場コードと開催テーブルを抽出する。
    標準ライブラリのみで動作する。
    """

    def __init__(self, venue_master):
        super().__init__(convert_charrefs=True)

        self.venue_master = venue_master

        self.current_venue_code = None
        self.current_button_code = None
        self.current_button_name = ""
        self.capturing_venue_name = False

        self.active_table = False
        self.table_depth = 0

        self.current_cell = None
        self.cells = []

        self.events = []
        self.seen_venues = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()

        classes = set(
            (attrs.get("class") or "").split()
        )

        # 競輪場ボタンの識別コードを取得
        if tag == "button":
            onclick = attrs.get("onclick") or ""

            match = re.search(
                r"jocd=(\d+)",
                onclick,
            )

            if match:
                code = match.group(1)

                if code not in self.venue_master:
                    raise ValueError(
                        "競輪場マスターにないコードです: "
                        f"{code}"
                    )

                self.current_button_code = code
                self.current_button_name = ""

        # ボタン内の競輪場名を取得
        if (
            tag == "p"
            and self.current_button_code
            and "btn_base_fsz" in classes
        ):
            self.capturing_venue_name = True

        # 開催情報のテーブルを検出
        if tag == "table":
            if (
                not self.active_table
                and "race_tbl" in classes
            ):
                if self.current_venue_code is None:
                    raise RuntimeError(
                        "開催テーブルの前に競輪場コードを"
                        "確認できませんでした"
                    )

                self.active_table = True
                self.table_depth = 1
                self.cells = []
                self.current_cell = None

            elif self.active_table:
                self.table_depth += 1

        if not self.active_table:
            return

        # 開催テーブル内のセルを記録
        if tag == "td":
            self.current_cell = {
                "class": attrs.get("class", ""),
                "colspan": attrs.get("colspan"),
                "text": "",
            }

        elif tag == "img" and self.current_cell:
            alt = attrs.get("alt")

            if alt:
                self.current_cell["text"] += (
                    " " + normalize(alt)
                )

        elif tag == "br" and self.current_cell:
            self.current_cell["text"] += " "

    def handle_data(self, data):
        value = normalize(data)

        if not value:
            return

        if self.capturing_venue_name:
            self.current_button_name += value

        if self.active_table and self.current_cell:
            self.current_cell["text"] += (
                " " + value
            )

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag == "p" and self.capturing_venue_name:
            self.capturing_venue_name = False

        if tag == "button" and self.current_button_code:
            code = self.current_button_code

            master_name = self.venue_master[code]["name"]
            actual_name = normalize(
                self.current_button_name
            )

            if compact(actual_name) != compact(master_name):
                raise RuntimeError(
                    "競輪場コードと名称が一致しません: "
                    f"コード={code}, "
                    f"マスター={master_name}, "
                    f"HTML={actual_name}"
                )

            self.current_venue_code = code
            self.seen_venues.add(code)

            self.current_button_code = None
            self.current_button_name = ""

        if not self.active_table:
            return

        if tag == "td" and self.current_cell:
            self.current_cell["text"] = normalize(
                self.current_cell["text"]
            )

            self.cells.append(self.current_cell)
            self.current_cell = None

        if tag == "table":
            self.table_depth -= 1

            if self.table_depth <= 0:
                self._finish_table()

                self.active_table = False
                self.table_depth = 0
                self.current_cell = None
                self.cells = []

    def _finish_table(self):
        venue_code = self.current_venue_code

        if not venue_code:
            return

        date_text = ""

        for cell in self.cells:
            classes = set(
                cell["class"].split()
            )

            if "kikan" in classes:
                date_text = cell["text"]
                break

        date_info = parse_date_range(
            date_text,
            now_jst().year,
        )

        if not date_info:
            # 開催テーブルの構造が違う場合は採用しない
            return

        event_name = None
        grade = None

        for cell in self.cells:
            classes = set(
                cell["class"].split()
            )

            text = normalize(cell["text"])

            if "grade" in classes and text:
                grade = text

            if (
                cell.get("colspan") == "5"
                and text
            ):
                event_name = text

        venue = self.venue_master[venue_code]

        event = {
            "venue_code": venue_code,
            "venue_name": venue["name"],
            "region": venue["region"],
            "prefecture": venue["prefecture"],
            "event_name": event_name,
            "grade": grade,
            "start_date": date_info["start_date"],
            "end_date": date_info["end_date"],
            "source": SOURCE_URL,
        }

        self.events.append(event)


def parse_schedule(html, venue_master, target_date):
    parser = ScheduleParser(venue_master)

    parser.feed(html)
    parser.close()

    if not parser.seen_venues:
        raise RuntimeError(
            "競輪場コードを一件も確認できませんでした"
        )

    if not parser.events:
        raise RuntimeError(
            "開催期間を抽出できませんでした。"
            "既存データの上書きを中止します"
        )

    events = []
    seen = set()

    for event in parser.events:
        key = (
            event["venue_code"],
            event["start_date"],
            event["end_date"],
        )

        if key in seen:
            continue

        seen.add(key)

        start_date = date.fromisoformat(
            event["start_date"]
        )

        end_date = date.fromisoformat(
            event["end_date"]
        )

        event["is_active_today"] = (
            start_date <= target_date <= end_date
        )

        event["race_count"] = None
        event["race_count_status"] = "not_acquired"

        events.append(event)

    events.sort(
        key=lambda item: (
            item["start_date"],
            item["venue_code"],
        )
    )

    return events, len(parser.seen_venues)


def build_output(
    events,
    target_date,
    http_status,
    body_bytes,
    venue_count,
):
    active_today = [
        event
        for event in events
        if event["is_active_today"]
    ]

    now = now_jst().isoformat(
        timespec="seconds"
    )

    return {
        "schema_version": 2,
        "generated_at": now,
        "target_date": target_date.isoformat(),
        "status": "ok",
        "source": {
            "name": "KEIRIN.JP",
            "url": SOURCE_URL,
            "http_status": http_status,
            "body_bytes": body_bytes,
            "retrieved_at": now,
        },
        "summary": {
            "venue_count": venue_count,
            "event_count": len(events),
            "active_event_count": len(active_today),
            "race_count": None,
            "race_count_status": "not_acquired",
        },
        "events": events,
        "active_today": active_today,
        "data_notes": [
            "開催日程は公式サイトのHTMLから取得しています。",
            "競輪場コードと名称をマスターと照合しています。",
            "開催期間を取得できない場合は保存を中止します。",
            "レース数・出走表・選手情報は未取得です。",
        ],
    }


def save_json_safely(output):
    temporary_file = OUTPUT_FILE.with_suffix(".tmp")

    temporary_file.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # JSONの再読込に成功した場合だけ置き換える
    json.loads(
        temporary_file.read_text(
            encoding="utf-8"
        )
    )

    temporary_file.replace(OUTPUT_FILE)


def main():
    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    target_date = now_jst().date()

    print("=" * 44)
    print("KEIRIN AI SCHEDULE DATA")
    print("=" * 44)
    print("対象日:", target_date.isoformat())

    try:
        venue_master = load_venues()

        print(
            "競輪場マスター:",
            len(venue_master),
            "場",
        )

        html, status, body_bytes = fetch_html()

        print("HTTP status:", status)
        print("取得サイズ:", body_bytes, "bytes")

        events, venue_count = parse_schedule(
            html,
            venue_master,
            target_date,
        )

        output = build_output(
            events,
            target_date,
            status,
            body_bytes,
            venue_count,
        )

        if not output["active_today"]:
            raise RuntimeError(
                "対象日の開催場が0件です。"
                "誤った結果の保存を防ぐため中止します"
            )

        save_json_safely(output)

        print("-" * 44)
        print("競輪場検出:", venue_count)
        print("開催情報:", len(events))
        print(
            "本日開催:",
            len(output["active_today"]),
        )

        for event in output["active_today"]:
            print(
                f"  {event['venue_name']} "
                f"{event['start_date']}～"
                f"{event['end_date']} "
                f"{event['event_name'] or '開催名未取得'}"
            )

        print("-" * 44)
        print("保存先:", OUTPUT_FILE)
        print("状態: OK")
        print(
            "注意: レース数と出走表はまだ未取得です"
        )

    except Exception as exc:
        print("=" * 44)
        print("開催日程の取得・検証に失敗しました")
        print("原因:", exc)
        print(
            "既存のtoday_schedule.jsonは"
            "上書きしていません"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

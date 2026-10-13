import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from datetime import datetime, timezone

URL = "https://www.keirin.jp/sp/raceschedule"
OUT = Path("data/schedule_inspection.json")


class ScheduleInspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.current_row = None
        self.current_cell = None
        self.in_cell = False
        self.page_text = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.current_row = []
        elif tag in ("td", "th") and self.current_row is not None:
            self.current_cell = []
            self.in_cell = True

    def handle_data(self, data):
        text = " ".join(data.split())
        if not text:
            return

        self.page_text.append(text)

        if self.in_cell and self.current_cell is not None:
            self.current_cell.append(text)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.current_row is not None:
            if self.current_cell is not None:
                cell = " ".join(self.current_cell).strip()
                self.current_row.append(cell)

            self.current_cell = None
            self.in_cell = False

        elif tag == "tr":
            if self.current_row:
                cells = [c for c in self.current_row if c]
                if cells:
                    self.rows.append(cells)

            self.current_row = None
            self.current_cell = None
            self.in_cell = False


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)

    request = urllib.request.Request(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; KeirinAI/1.0)"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        status = response.status
        charset = response.headers.get_content_charset() or "utf-8"
        html = raw.decode(charset, errors="replace")

    parser = ScheduleInspector()
    parser.feed(html)

    patterns = re.compile(
        r"\d{1,2}月|\d{1,2}日|"
        r"函館|青森|いわき平|弥彦|前橋|取手|宇都宮|大宮|"
        r"西武園|京王閣|立川|松戸|川崎|平塚|小田原|"
        r"伊東|静岡|名古屋|岐阜|大垣|豊橋|富山|松阪|四日市|"
        r"福井|奈良|向日町|和歌山|岸和田|玉野|広島|防府|"
        r"高松|小松島|高知|松山|小倉|久留米|武雄|佐世保|"
        r"別府|熊本|ＧⅠ|ＧⅡ|ＧⅢ|G1|G2|G3|FⅠ|FⅡ|F1|F2",
        re.IGNORECASE
    )

    matching_rows = []

    for row in parser.rows:
        joined = " | ".join(row)
        if patterns.search(joined):
            matching_rows.append(row)

    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_url": URL,
        "http_status": status,
        "body_bytes": len(raw),
        "table_row_count": len(parser.rows),
        "matching_row_count": len(matching_rows),
        "matching_rows": matching_rows[:100],
        "page_text_preview": " ".join(parser.page_text)[:5000],
        "note": (
            "調査用データです。実際の開催日程として利用する前に、"
            "開催日・競輪場・レース番号の対応を検証してください。"
        )
    }

    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print("開催日程ページの内容調査完了")
    print(f"HTTP status: {status}")
    print(f"取得サイズ: {len(raw)} bytes")
    print(f"テーブル行数: {len(parser.rows)}")
    print(f"候補行数: {len(matching_rows)}")
    print(f"保存先: {OUT}")


if __name__ == "__main__":
    main()

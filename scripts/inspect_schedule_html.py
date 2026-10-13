import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from datetime import datetime, timezone
URL = "https://www.keirin.jp/sp/raceschedule"
OUT = Path("data/schedule_inspection.json")
class PageInspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = {}
        self.links = []
        self.tables = 0
        self.current_link = None
        self.link_text = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags[tag] = self.tags.get(tag, 0) + 1
        if tag == "table":
            self.tables += 1
        if tag == "a":
            self.current_link = attrs.get("href", "")
            self.link_text = []
    def handle_data(self, data):
        text = " ".join(data.split())
        if text and self.current_link is not None:
            self.link_text.append(text)
    def handle_endtag(self, tag):
        if tag == "a" and self.current_link is not None:
            text = " ".join(self.link_text).strip()
            href = self.current_link
            if text or href:
                self.links.append({
                    "text": text[:120],
                    "href": href[:300]
                })
            self.current_link = None
            self.link_text = []
def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; KeirinAI/1.0)"}
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read()
        status = response.status
        charset = response.headers.get_content_charset() or "utf-8"
    html = raw.decode(charset, errors="replace")
    parser = PageInspector()
    parser.feed(html)
    # 日程取得に使えそうなリンクだけ抽出
    candidates = []
    keywords = ("race", "kaisai", "schedule", "開催", "競輪場", "レース")
    for link in parser.links:
        combined = (link["text"] + " " + link["href"]).lower()
        if any(word.lower() in combined for word in keywords):
            candidates.append(link)
    # ページ構造の調査用。予想データや架空のレース情報は作らない。
    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_url": URL,
        "http_status": status,
        "body_bytes": len(raw),
        "page_title": (
            re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        ).group(1).strip() if re.search(
            r"<title[^>]*>(.*?)</title>", html, re.I | re.S
        ) else "",
        "tag_counts": parser.tags,
        "table_count": parser.tables,
        "candidate_link_count": len(candidates),
        "candidate_links": candidates[:150],
        "html_markers": {
            "開催日程": "開催日程" in html,
            "2026": "2026" in html,
            "10月": "10月" in html,
            "開催場": "開催場" in html,
        },
        "note": "これはHTML構造の調査結果であり、開催日程の確定データではありません。"
    }
    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print("開催日程ページの調査完了")
    print(f"HTTP status: {status}")
    print(f"取得サイズ: {len(raw)} bytes")
    print(f"リンク候補: {len(candidates)}")
    print(f"保存先: {OUT}")
if __name__ == "__main__":
    main()

import json
import re
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

URL = “https://www.keirin.jp/sp/raceschedule”
OUT = Path(“data/schedule_inspection.json”)

VENUES = [
“函館”, “青森”, “いわき平”, “弥彦”, “前橋”,
“取手”, “宇都宮”, “大宮”, “西武園”, “京王閣”,
“立川”, “松戸”, “川崎”, “平塚”, “小田原”,
“伊東”, “静岡”, “名古屋”, “岐阜”, “大垣”,
“豊橋”, “富山”, “松阪”, “四日市”, “福井”,
“奈良”, “向日町”, “和歌山”, “岸和田”, “玉野”,
“広島”, “防府”, “高松”, “小松島”, “高知”,
“松山”, “小倉”, “久留米”, “武雄”, “佐世保”,
“別府”, “熊本”
]

def main():
OUT.parent.mkdir(parents=True, exist_ok=True)

request = urllib.request.Request(
    URL,
    headers={
        "User-Agent": "Mozilla/5.0 (compatible; KeirinAI/2.0)",
        "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
    },
)
with urllib.request.urlopen(request, timeout=30) as response:
    raw = response.read()
    status = response.status
    charset = response.headers.get_content_charset() or "utf-8"
html = raw.decode(charset, errors="replace")
# 玉野の出現位置と、その前後のHTMLを取得
target = "玉野"
positions = [
    match.start()
    for match in re.finditer(re.escape(target), html)
]
contexts = []
for number, position in enumerate(positions[:10], start=1):
    start = max(0, position - 1200)
    end = min(len(html), position + 1800)
    snippet = html[start:end]
    contexts.append({
        "occurrence": number,
        "html_position": position,
        "html_snippet": snippet,
    })
# HTML内の日付表記を調査
date_pattern = re.compile(
    r".{0,150}"
    r"(?:\d{1,2}\s*/\s*\d{1,2}"
    r"| \d{1,2}月\s*\d{1,2}日"
    r"|開催がありません)"
    r".{0,250}",
    re.DOTALL,
)
date_matches = []
for match in date_pattern.finditer(html):
    snippet = match.group(0)
    # 長い空白や改行を整理
    snippet = re.sub(r"\s+", " ", snippet).strip()
    date_matches.append(snippet)
    if len(date_matches) >= 40:
        break
report = {
    "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    "source_url": URL,
    "http_status": status,
    "body_bytes": len(raw),
    "venue_name": target,
    "venue_occurrence_count": len(positions),
    "venue_html_contexts": contexts,
    "date_pattern_candidates": date_matches,
    "note": (
        "診断専用データです。"
        "開催日程として直接利用しないでください。"
    ),
}
OUT.write_text(
    json.dumps(report, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print("=" * 45)
print("開催日程HTML診断")
print(f"HTTP status: {status}")
print(f"取得サイズ: {len(raw)} bytes")
print(f"玉野の出現回数: {len(positions)}")
print(f"日付候補数: {len(date_matches)}")
for context in contexts[:3]:
    print("-" * 45)
    print(
        f"玉野 出現位置: {context['html_position']}"
    )
    print(context["html_snippet"][:1800])
print("-" * 45)
print(f"保存先: {OUT}")
print("診断完了")

if name == “main”:
main()

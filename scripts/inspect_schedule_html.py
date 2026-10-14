import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
URL = "https://www.keirin.jp/sp/raceschedule"
OUT = Path("data/schedule_inspection.json")
def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; KeirinAI/2.0)",
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        },
    )
    # 公式サイトのHTMLを取得
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        status = response.status
        charset = response.headers.get_content_charset() or "utf-8"
    html = raw.decode(charset, errors="replace")
    # 調査対象の競輪場
    target = "玉野"
    # HTML内で競輪場名が出現する位置を取得
    positions = [
        match.start()
        for match in re.finditer(re.escape(target), html)
    ]
    # 競輪場名の前後にあるHTMLを記録
    contexts = []
    for number, position in enumerate(positions[:10], start=1):
        start = max(0, position - 1200)
        end = min(len(html), position + 1800)
        contexts.append({
            "occurrence": number,
            "html_position": position,
            "html_snippet": html[start:end],
        })
    # 日付や開催情報に関係するHTML断片を抽出
    date_pattern = re.compile(
        r".{0,150}(?:\d{1,2}\s*/\s*\d{1,2}|"
        r"\d{1,2}月\s*\d{1,2}日|開催がありません).{0,250}",
        re.DOTALL,
    )
    date_matches = []
    for match in date_pattern.finditer(html):
        snippet = re.sub(r"\s+", " ", match.group(0)).strip()
        date_matches.append(snippet)
        if len(date_matches) >= 40:
            break
    # 診断結果を作成
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
    # JSONを一時ファイルに保存して検証後に置き換える
    temporary_file = OUT.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # JSONとして正常に読み込めることを確認
    json.loads(temporary_file.read_text(encoding="utf-8"))
    temporary_file.replace(OUT)
    # 実行結果を表示
    print("=" * 45)
    print("開催日程HTML診断")
    print("HTTP status:", status)
    print("取得サイズ:", len(raw), "bytes")
    print("玉野の出現回数:", len(positions))
    print("日付候補数:", len(date_matches))
    for context in contexts[:3]:
        print("-" * 45)
        print("玉野 出現位置:", context["html_position"])
        print(context["html_snippet"][:1800])
    print("-" * 45)
    print("保存先:", OUT)
    print("診断完了")
if __name__ == "__main__":
    main()

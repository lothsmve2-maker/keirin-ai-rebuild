import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent.parent
REPORT_FILE = ROOT / "data" / "source_probe.json"

SOURCES = [
    {
        "name": "KEIRIN.JP 開催日程",
        "url": "https://www.keirin.jp/sp/raceschedule",
        "markers": ["開催日程"],
    },
    {
        "name": "KEIRIN.JP 競輪場一覧",
        "url": "https://www.keirin.jp/sp/jyolist",
        "markers": ["競輪場一覧"],
    },
]


def check_source(source):
    result = {
        "name": source["name"],
        "url": source["url"],
        "checked_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "http_status": None,
        "body_bytes": 0,
        "page_title": None,
        "markers_found": [],
        "status": "failed",
        "error": None,
    }

    request = Request(
        source["url"],
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "ja,en;q=0.8",
        },
    )

    try:
        with urlopen(request, timeout=25) as response:
            body = response.read(4_000_000)
            result["http_status"] = response.status

        html = body.decode("utf-8", errors="replace")
        result["body_bytes"] = len(body)

        title = re.search(
            r"<title[^>]*>(.*?)</title>",
            html,
            flags=re.I | re.S,
        )

        if title:
            result["page_title"] = re.sub(
                r"\s+", " ", title.group(1)
            ).strip()

        text = re.sub(
            r"<script\b[^>]*>.*?</script>",
            " ",
            html,
            flags=re.I | re.S,
        )

        text = re.sub(
            r"<style\b[^>]*>.*?</style>",
            " ",
            text,
            flags=re.I | re.S,
        )

        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)

        result["markers_found"] = [
            marker
            for marker in source["markers"]
            if marker in text
        ]

        if (
            result["http_status"] == 200
            and result["body_bytes"] > 1000
            and result["markers_found"]
        ):
            result["status"] = "reachable"
        else:
            result["error"] = (
                "取得できたが、必要な内容を確認できません"
            )

    except (
        HTTPError,
        URLError,
        TimeoutError,
        OSError,
    ) as exc:
        result["error"] = (
            f"{type(exc).__name__}: {exc}"
        )

    return result


def main():
    reports = [
        check_source(source)
        for source in SOURCES
    ]

    overall_status = (
        "reachable"
        if all(
            item["status"] == "reachable"
            for item in reports
        )
        else "needs_review"
    )

    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "overall_status": overall_status,
        "sources": reports,
        "warning": (
            "接続成功は、開催情報を完全に取得できた "
            "ことを意味しません。"
        ),
    }

    REPORT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    REPORT_FILE.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
    ))

    if overall_status != "reachable":
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

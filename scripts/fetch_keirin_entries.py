#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
KEIRIN.JP 公式出走表取得 v4

・公式開催一覧を取得
・JSJ049で開催別レース一覧を取得
・JSJ049が失敗した場合は公式POST画面を試す
・個別レースの出走表を取得
・公式コメントを取得
・公式ライン情報を保存
・レース日付を検証
・選手数、車番、ライン情報を検証
・data/entries_today.json に保存

重要:
確認できない選手情報やライン構造は推測で補完しない。
日付を確定できないレースは当日分として登録しない。
"""

import json
import re
import sys
import time
from html.parser import HTMLParser
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests


# ============================================================
# 設定
# ============================================================

BASE = "https://keirin.jp"
SCHEDULE_URL = f"{BASE}/sp/raceschedule"
SCHEDULE_API = f"{BASE}/sp/json?type=JSJ058"
RACE_LIST_API = f"{BASE}/sp/json"

OUTPUT = Path("data/entries_today.json")

JST = timezone(timedelta(hours=9))

TIMEOUT = 25
PAUSE = 0.15
SCHEMA_VERSION = 4

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/json,"
        "application/javascript,*/*;q=0.8"
    ),
    "Accept-Language": "ja-JP,ja;q=0.9",
}


# ============================================================
# 基本関数
# ============================================================

def now_jst():
    return datetime.now(JST)


def clean(value):
    if value is None:
        return ""

    if isinstance(value, (dict, list)):
        return value

    return str(value).strip()


def first_value(obj, *keys):
    if not isinstance(obj, dict):
        return ""

    for key in keys:
        value = obj.get(key)

        if value not in (None, ""):
            return value

    return ""


def parse_date(value):
    """日本語・区切り付き・8桁の日付を解析する。"""

    if value is None:
        return None

    text = str(value).strip()

    patterns = [
        r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})",
        r"(20\d{2})(\d{2})(\d{2})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if not match:
            continue

        try:
            year, month, day = map(int, match.groups())
            return datetime(year, month, day).date()
        except ValueError:
            continue

    return None


def request_page(session, url, params=None, referer=None):
    """GETリクエスト。失敗は呼び出し元で処理する。"""

    headers = dict(HEADERS)

    if referer:
        headers["Referer"] = referer

    response = session.get(
        url,
        params=params,
        headers=headers,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"

    return response


def request_post_page(session, url, data, referer=None):
    """公式フォームへのPOSTリクエスト。"""

    headers = dict(HEADERS)
    headers["Origin"] = BASE

    if referer:
        headers["Referer"] = referer

    response = session.post(
        url,
        data=data,
        headers=headers,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"

    return response


def request_json(session, url, params=None, referer=None):
    response = request_page(
        session,
        url,
        params=params,
        referer=referer,
    )

    return response.json()


def walk_dicts(obj, path="$"):
    """JSON階層内の辞書を列挙する。"""

    if isinstance(obj, dict):
        yield path, obj

        for key, value in obj.items():
            yield from walk_dicts(value, f"{path}.{key}")

    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from walk_dicts(
                value,
                f"{path}[{index}]",
            )


# ============================================================
# 開催情報
# ============================================================

def find_events(payload):
    """公式開催JSONから開催候補を抽出する。"""

    events = []
    seen = set()

    for path, obj in walk_dicts(payload):
        encp = first_value(
            obj,
            "encPrm",
            "encp",
            "encprm",
        )

        venue = first_value(
            obj,
            "jyoName",
            "venueName",
            "jyo",
        )

        venue_code = first_value(
            obj,
            "bKeirinCd",
            "KeirinCd",
            "jyoCd",
        )

        start_date = first_value(
            obj,
            "kStartDate",
            "startDate",
            "kaisaiDate",
            "start_date",
        )

        if not encp or not venue:
            continue

        key = (
            str(venue),
            str(venue_code),
            str(start_date),
            str(encp),
        )

        if key in seen:
            continue

        seen.add(key)

        events.append({
            "venue": clean(venue),
            "venue_code": clean(venue_code),
            "start_date": clean(start_date),
            "encp": clean(encp),
            "source_path": path,
            "raw_fields": {
                key: value
                for key, value in obj.items()
                if key != "sensyuList"
            },
            "sensyu_count": (
                len(obj.get("sensyuList", []))
                if isinstance(obj.get("sensyuList"), list)
                else 0
            ),
        })
        
    return events
def find_schedule_event_encp(html, venue):
"""公式開催一覧HTMLから、指定開催場の開催遷移パラメータを探す。"""

def find_schedule_event_encp(html, venue):
“”“Find the event parameter in the official schedule HTML.”””

if not html or not venue:
    return ""
pattern = re.compile(
    r"""lnk\.formlnk\(\s*['"](?P<path>/sp/racelist)['"]\s*,\s*['"](?P<encp>[^'"]+)['"]\s*\)""",
    re.IGNORECASE,
)
candidates = []
for match in pattern.finditer(html):
    start = max(0, match.start() - 5000)
    end = min(len(html), match.end() + 5000)
    context = html[start:end]
    context_text = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        context,
        flags=re.IGNORECASE | re.DOTALL,
    )
    context_text = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        context_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    context_text = re.sub(r"<[^>]+>", " ", context_text)
    context_text = (
        context_text
        .replace("&nbsp;", " ")
        .replace("&#160;", " ")
        .replace("&amp;", "&")
    )
    context_text = re.sub(r"\s+", " ", context_text).strip()
    venue_position = context_text.find(venue)
    if venue_position < 0:
        continue
    candidates.append({
        "encp": match.group("encp"),
        "distance": abs(venue_position - len(context_text) // 2),
    })
if not candidates:
    return ""
candidates.sort(key=lambda item: item["distance"])
return candidates[0]["encp"]

# ============================================================
# 公式レース一覧HTMLの解析
# ============================================================

class RaceLinkParser(HTMLParser):
    """
    公式レース一覧HTMLから個別レースへの遷移を抽出する。

    修正点:
    ・br、img、inputなどの終了タグを持たない要素に対応
    ・クリック対象の要素名を記録
    ・ネストしたタグの終了時に解析を終了しない
    ・レース番号の表記ゆれに対応
    """

    VOID_TAGS = {
        "area", "base", "br", "col", "embed",
        "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)

        self.stack = []

        self.target_tag = None
        self.target_depth = None
        self.target_encp = ""
        self.target_path = ""
        self.target_text = []

        self.links = []

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        onclick = attr.get("onclick", "") or ""

        # クリック対象がすでに解析中なら、
        # その内部のテキストを収集する。
        if self.target_tag is not None:
            if tag not in self.VOID_TAGS:
                self.stack.append(tag)
            return

        match = re.search(
            r"""lnk\.formlnk\(\s*['"](?P<path>/sp/race(?:/[^'"]*)?)['"]\s*,\s*['"](?P<encp>[^'"]+)['"]\s*\)""",
            onclick,
        )

        if not match:
            if tag not in self.VOID_TAGS:
                self.stack.append(tag)
            return

        self.target_tag = tag
        self.target_depth = len(self.stack) + (
            0 if tag in self.VOID_TAGS else 1
        )
        self.target_encp = match.group("encp")
        self.target_path = match.group("path")
        self.target_text = []

        if tag not in self.VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        # <input /> などの自己終了タグ
        # クリック対象ではないため、階層を変更しない。
        return

    def handle_data(self, data):
        if self.target_tag is not None:
            self.target_text.append(data)

    def handle_endtag(self, tag):
        if not self.stack:
            return

        # 開始タグと終了タグが一致する位置まで戻す。
        # 不正なHTMLでも階層が負にならないようにする。
        if tag not in self.stack:
            return

        while self.stack:
            popped = self.stack.pop()

            if popped == tag:
                break

        # クリック対象そのものが閉じられたときだけ確定する。
        if (
            self.target_tag == tag
            and self.target_depth == len(self.stack) + 1
        ):
            self._finish_target()

    def _finish_target(self):
        text = " ".join(self.target_text)
        text = re.sub(r"\s+", " ", text).strip()

        self.links.append({
            "race_encp": self.target_encp,
            "race_path": self.target_path,
            "text": text,
        })

        self.target_tag = None
        self.target_depth = None
        self.target_encp = ""
        self.target_path = ""
        self.target_text = []

    def close(self):
        super().close()

        # 閉じタグが欠落したHTMLにも対応する。
        if self.target_tag is not None:
            self._finish_target()


def extract_race_number(text):
    """画面上の表記からレース番号を抽出する。"""

    if not text:
        return ""

    text = re.sub(r"\s+", " ", str(text)).strip()

    patterns = [
        r"(?<!\d)(\d{1,2})\s*R(?!\w)",
        r"第\s*(\d{1,2})\s*レース",
        r"(\d{1,2})\s*レース",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            number = int(match.group(1))

            if 1 <= number <= 12:
                return str(number)

    return ""


def fetch_races_from_post(session, event):
    """
    公式POST画面を取得し、HTML全体から個別レースリンクを探す。

    クリック対象要素のテキストだけに依存せず、
    リンク周辺のHTMLからレース番号を検出する。
    """

    response = request_post_page(
        session,
        urljoin(BASE, "/sp/racelist"),
        {
            "encp": event["encp"],
            "disp": "SJ0301",
        },
        referer=SCHEDULE_URL,
    )

    html = response.text or ""

    print(
        f'    POST HTTP: {response.status_code}'
        f' / URL: {response.url}'
        f' / HTML: {len(html)} bytes'
    )

    if not html:
        print("    診断: HTML本文が空です")
        return []

    # HTML内の個別レースリンクを直接抽出する。
    pattern = re.compile(
        r"""lnk\.formlnk\(\s*['"](?P<path>/sp/race(?:/[^'"]*)?)['"]\s*,\s*['"](?P<encp>[^'"]+)['"]\s*\)""",
        re.IGNORECASE,
    )

    matches = list(pattern.finditer(html))

    print(f"    HTML内の個別レースリンク数: {len(matches)}")

    if not matches:
        # レースリンクが見つからない場合は、
        # 返却された画面が想定どおりか確認するための情報を出す。
        title_match = re.search(
            r"<title[^>]*>(.*?)</title>",
            html,
            re.IGNORECASE | re.DOTALL,
        )

        title = (
            re.sub(r"\s+", " ", title_match.group(1)).strip()
            if title_match
            else "(titleなし)"
        )

        print(f"    診断: ページタイトル={title}")
        print(
            "    診断: 本文先頭=",
            re.sub(r"\s+", " ", html[:300])[:200],
        )

        return []

    races = []
    seen = set()
    rejected = 0

    # レース番号の候補となる表記。
    race_number_patterns = [
        re.compile(r"(?<!\d)(\d{1,2})\s*R(?!\w)", re.I),
        re.compile(r"第\s*(\d{1,2})\s*レース"),
        re.compile(r"(\d{1,2})\s*レース"),
    ]

    for index, match in enumerate(matches):
        race_encp = match.group("encp")
        race_path = match.group("path")

        if not race_encp or race_encp in seen:
            continue

        # 各リンクの周辺を調べる。
        # クリック対象の内部だけでなく、前後のHTMLも含める。
        start = max(0, match.start() - 1200)
        end = min(len(html), match.end() + 1800)
        context = html[start:end]

        # HTMLタグを除去して、文字参照をデコードする。
        context_text = re.sub(
            r"<script\b[^>]*>.*?</script>",
            " ",
            context,
            flags=re.IGNORECASE | re.DOTALL,
        )

        context_text = re.sub(
            r"<style\b[^>]*>.*?</style>",
            " ",
            context_text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        context_text = re.sub(
            r"<[^>]+>",
            " ",
            context_text,
        )

        context_text = (
            context_text
            .replace("&nbsp;", " ")
            .replace("&#160;", " ")
            .replace("&amp;", "&")
        )

        context_text = re.sub(
            r"\s+",
            " ",
            context_text,
        ).strip()

        race_no = ""

        for number_pattern in race_number_patterns:
            number_match = number_pattern.search(context_text)

            if number_match:
                number = int(number_match.group(1))

                if 1 <= number <= 12:
                    race_no = str(number)
                    break

        if not race_no:
            rejected += 1

            if rejected <= 3:
                print(
                    "    診断: レース番号を抽出できないリンク",
                    race_encp,
                    "周辺文字列:",
                    context_text[:180],
                )

            continue

        seen.add(race_encp)

        races.append({
            "venue": event["venue"],
            "venue_code": event["venue_code"],
            "event_start_date": event["start_date"],
            "day": None,
            "race_date": None,
            "race_no": race_no,
            "race_name": "",
            "race_url": urljoin(BASE, race_path),
            "race_encp": race_encp,
            "event_encp": event["encp"],
            "list_source": "official_post_fallback",
        })

    print(
        f"    POST解析結果: "
        f"リンク={len(matches)}, "
        f"採用={len(races)}, "
        f"番号不明={rejected}"
    )

    return races


# ============================================================
# 開催別レース一覧
# ============================================================

def fetch_event_races(session, event):
    """JSJ049の応答を診断し、0件の場合は公式POST画面を試す。"""

    races = []
    event_encp = event.get("encp")

    if not event_encp:
        raise ValueError("開催パラメータencpがありません")

    try:
        payload = request_json(
            session,
            RACE_LIST_API,
            params={
                "type": "JSJ049",
                "encp": event_encp,
            },
            referer=SCHEDULE_URL,
        )

        if not isinstance(payload, dict):
            print(
                f'  {event["venue"]}: '
                f"JSJ049応答形式={type(payload).__name__}"
            )
            raise ValueError("JSJ049の応答が辞書形式ではありません")

        print(
            f'  {event["venue"]}: '
            f'開催コード={event.get("venue_code", "")}, '
            f'encp={event_encp}, '
            f'取得元={event.get("source_path", "")}'
        )
        if event["venue"] in ("静岡", "豊橋", "四日市"):
            print(
                f'  {event["venue"]}: '
                f'公式開催情報={json.dumps(event.get("raw_fields", {}), ensure_ascii=False)}'
            )
            print(
                f'  {event["venue"]}: '
                f'選手リスト件数={event.get("sensyu_count", 0)}'
            )
        print(
            f'  {event["venue"]}: '
            f'JSJ049 resultCd={payload.get("resultCd", "(なし)")}, '
            f'messageCd={payload.get("messageCd", "(なし)")}, '
            f'message={payload.get("message", "(なし)")}, '
            f'keys={list(payload.keys())[:12]}'
        )

        days = payload.get("kaisaiDayList", [])

        if not isinstance(days, list):
            print(
                f'  {event["venue"]}: '
                "kaisaiDayListがリストではありません"
            )
            days = []

        print(
            f'  {event["venue"]}: '
            f"開催日リスト={len(days)}件"
        )
        if event["venue"] in ("静岡", "豊橋", "四日市"):
            print(
                f'  {event["venue"]}: '
                f'公式開催情報={json.dumps(event.get("raw_fields", {}), ensure_ascii=False)}'
            )
            print(
                f'  {event["venue"]}: '
                f'選手リスト件数={event.get("sensyu_count", 0)}'
            )
        for day in days:
            if not isinstance(day, dict):
                continue

            day_name = first_value(
                day,
                "strNitiji",
                "strDay",
                "strKaisaiDate",
            )

            day_date = first_value(
                day,
                "strKaisaiDate",
                "kaisaiDate",
                "strEventDate",
            )

            programs = day.get("raceProgramList", [])

            if not isinstance(programs, list):
                continue

            for program in programs:
                if not isinstance(program, dict):
                    continue

                race_path = first_value(
                    program,
                    "strRaceLnk",
                )

                race_encp = first_value(
                    program,
                    "strRaceLnkPrm",
                )

                race_no = first_value(
                    program,
                    "strRaceNo",
                )

                race_name = first_value(
                    program,
                    "strSyumokuName",
                )

                if not race_encp or not race_no:
                    continue

                races.append({
                    "venue": event["venue"],
                    "venue_code": event["venue_code"],
                    "event_start_date": event["start_date"],
                    "day": clean(day_name),
                    "race_date": clean(day_date),
                    "race_no": clean(race_no),
                    "race_name": clean(race_name),
                    "race_url": urljoin(
                        BASE,
                        race_path or "/sp/race",
                    ),
                    "race_encp": clean(race_encp),
                    "event_encp": event_encp,
                    "list_source": "JSJ049",
                })

        print(
            f'  {event["venue"]}: '
            f"JSJ049レース抽出={len(races)}件"
        )

    except Exception as exc:
        print(
            f'  {event["venue"]}: JSJ049失敗: '
            f'{type(exc).__name__}: {exc}'
        )

    if races:
        print(
            f'  {event["venue"]}: JSONから'
            f"{len(races)}レース取得"
        )
        return races

    print(
        f'  {event["venue"]}: JSONが0件のため'
        "公式POST画面を試します"
    )

    try:
        fallback_races = fetch_races_from_post(
            session,
            event,
        )

        if fallback_races:
            print(
                f'  {event["venue"]}: POST画面から'
                f"{len(fallback_races)}レース取得"
            )
        else:
            print(
                f'  {event["venue"]}: '
                "JSON・POSTともにレースを抽出できませんでした"
            )

        return fallback_races

    except Exception as exc:
        print(
            f'  {event["venue"]}: POST取得失敗: '
            f'{type(exc).__name__}: {exc}'
        )

        raise


# ============================================================
# 公式ページ内JSON
# ============================================================

def extract_assignment(html, variable_name):
    """
    JavaScript内のJSONを抽出する。

    例:
    jsonData["SJ0315"] = {...};
    """

    pattern = (
        r"""jsonData\s*\[\s*["']"""
        + re.escape(variable_name)
        + r"""["']\s*\]\s*=\s*"""
    )

    match = re.search(pattern, html)

    if not match:
        return None

    decoder = json.JSONDecoder()
    source = html[match.end():].lstrip()

    try:
        value, _ = decoder.raw_decode(source)
        return value
    except json.JSONDecodeError:
        return None


# ============================================================
# 公式コメント
# ============================================================

def fetch_official_comments(session, race_encp, referer=None):
    """公式JSONから選手コメントを取得する。"""

    comments = {}

    try:
        payload = request_json(
            session,
            f"{BASE}/sp/json",
            params={
                "type": "JSJ041",
                "encp": race_encp,
                "skbn": "1",
            },
            referer=referer or SCHEDULE_URL,
        )

        if not isinstance(payload, dict):
            return comments, "取得失敗"

        riders = payload.get("sensyuTypeInfo")

        if not isinstance(riders, list):
            return comments, "取得失敗"

        for rider in riders:
            if not isinstance(rider, dict):
                continue

            car_no = str(
                first_value(rider, "syaban", "shaban")
            ).strip()

            comment = str(
                first_value(
                    rider,
                    "sensyuComment",
                    "comment",
                ) or ""
            ).strip()

            if car_no and comment:
                comments[car_no] = comment

        if comments:
            return comments, "取得済み"

        return comments, "未掲載"

    except Exception as exc:
        print(
            "  コメント取得失敗:",
            f"{type(exc).__name__}: {exc}",
        )
        return comments, "取得失敗"


# ============================================================
# 選手データ
# ============================================================

def normalize_rider(raw):
    """公式選手データを共通形式に変換する。"""

    comment = first_value(
        raw,
        "sensyuComment",
        "comment",
        "commentText",
        "strComment",
        "jikouComment",
    )

    if not isinstance(comment, str):
        comment = ""

    return {
        "car_no": clean(first_value(
            raw,
            "syaban",
            "shaban",
            "carNo",
        )),
        "registration_no": clean(first_value(
            raw,
            "sensyuRegistNo",
            "registNo",
        )),
        "name": clean(first_value(
            raw,
            "sensyuName",
            "name",
        )),
        "prefecture": clean(first_value(
            raw,
            "huKen",
            "prefecture",
        )),
        "previous_class": clean(first_value(
            raw,
            "prevKyuhan",
        )),
        "class": clean(first_value(
            raw,
            "kyuhan",
            "class",
        )),
        "style": clean(first_value(
            raw,
            "kyakusitu",
            "style",
        )),
        "points": clean(first_value(
            raw,
            "heikinTokuten",
            "tokuten",
        )),
        "nige": clean(first_value(
            raw,
            "nigeCnt",
        )),
        "makuri": clean(first_value(
            raw,
            "makuriCnt",
        )),
        "sashi": clean(first_value(
            raw,
            "sasiCnt",
        )),
        "mark": clean(first_value(
            raw,
            "markCnt",
        )),
        "back": clean(first_value(
            raw,
            "backCnt",
        )),
        "comment": comment,
        "comment_available": bool(comment.strip()),
    }


# ============================================================
# ライン情報
# ============================================================

def extract_official_lines(line_info):
    """公式の位置情報を保存する。ラインを推測で分割しない。"""

    if not isinstance(line_info, dict):
        return {
            "line_type": "",
            "line_order": [],
            "lines": [],
            "line_status": "公式データなし",
        }

    line_data = line_info.get("narabiyoso", {})

    if not isinstance(line_data, dict):
        line_data = {}

    raw_order = line_data.get("shaban", [])

    if not isinstance(raw_order, list):
        raw_order = []

    line_order = []

    for item in raw_order:
        if not isinstance(item, dict):
            continue

        car_no = clean(first_value(
            item,
            "shaban",
            "syaban",
        ))

        position = clean(first_value(
            item,
            "ichi",
        ))

        if not car_no:
            continue

        try:
            position_value = int(position)
        except (TypeError, ValueError):
            position_value = None

        line_order.append({
            "position": position_value,
            "car_no": car_no,
        })

    return {
        "line_type": clean(first_value(
            line_data,
            "lineKeitai",
        )),
        "line_order": line_order,
        "lines": [],
        "line_status": "公式位置情報を保存・グループ未確定",
    }


# ============================================================
# 個別レース
# ============================================================

def extract_race_data(session, race):
    """個別レースページから選手・ライン・コメントを取得する。"""

    response = request_page(
        session,
        race["race_url"],
        params={
            "encp": race["race_encp"],
        },
        referer=SCHEDULE_URL,
    )

    html = response.text

    base_info = extract_assignment(html, "SC0201")
    rider_info = extract_assignment(html, "SJ0315")
    line_info = extract_assignment(html, "SJ0314")

    if not isinstance(rider_info, dict):
        raise ValueError("SJ0315を抽出できません")

    raw_riders = rider_info.get("sensyuTypeInfo", [])

    if not isinstance(raw_riders, list):
        raw_riders = []

    riders = []

    for raw in raw_riders:
        if not isinstance(raw, dict):
            continue

        rider = normalize_rider(raw)

        if rider["car_no"] or rider["name"]:
            riders.append(rider)

    official_comments, comment_fetch_status = (
        fetch_official_comments(
            session,
            race["race_encp"],
            referer=response.url,
        )
    )

    for rider in riders:
        car_no = str(rider.get("car_no", "")).strip()
        comment = official_comments.get(car_no, "")

        if comment:
            rider["comment"] = comment

        rider["comment_available"] = bool(
            rider.get("comment", "").strip()
        )

        if rider["comment_available"]:
            rider["comment_status"] = "取得済み"
        elif comment_fetch_status == "取得失敗":
            rider["comment_status"] = "取得失敗"
        else:
            rider["comment_status"] = "未掲載"

    meta = {}

    if isinstance(base_info, dict):
        meta = base_info.get("C0201data", {}) or {}

    if not isinstance(meta, dict):
        meta = {}

    official_lines = extract_official_lines(line_info)

    race_date_display = clean(
        first_value(meta, "txtEventDate")
    )

    race_date = parse_date(race_date_display)

    verified_race_date = (
        race_date.isoformat()
        if race_date is not None
        else ""
    )

    car_numbers = [
        rider["car_no"]
        for rider in riders
        if rider["car_no"]
    ]

    line_car_numbers = [
        item["car_no"]
        for item in official_lines["line_order"]
    ]

    duplicate_car_numbers = (
        len(car_numbers) != len(set(car_numbers))
    )

    duplicate_line_numbers = (
        len(line_car_numbers) != len(set(line_car_numbers))
    )

    missing_line_numbers = sorted(
        set(car_numbers) - set(line_car_numbers)
    )

    unknown_line_numbers = sorted(
        set(line_car_numbers) - set(car_numbers)
    )

    comment_count = sum(
        1 for rider in riders
        if rider["comment_available"]
    )

    if riders and comment_count == len(riders):
        comment_status = "取得済み"
    elif comment_count > 0:
        comment_status = "一部取得"
    else:
        comment_status = "本文未取得"

    return {
        **race,

        "race_date_display": race_date_display,
        "verified_race_date": verified_race_date,

        "race_name": clean(first_value(
            meta,
            "syumoku",
        )) or race.get("race_name", ""),

        "venue": clean(first_value(
            meta,
            "joName",
        )) or race["venue"],

        "start_time": clean(first_value(
            meta,
            "aftStartTime",
            "bfrStartTime",
        )),

        "distance_m": clean(first_value(
            meta,
            "kyori",
        )),

        "line_type": official_lines["line_type"],
        "line_order": official_lines["line_order"],
        "lines": official_lines["lines"],
        "line_status": official_lines["line_status"],

        "riders": riders,
        "rider_count": len(riders),

        "validation": {
            "duplicate_car_numbers": duplicate_car_numbers,
            "duplicate_line_numbers": duplicate_line_numbers,
            "missing_line_numbers": missing_line_numbers,
            "unknown_line_numbers": unknown_line_numbers,
            "rider_count_valid": len(riders) > 0,
        },

        "comment_count": comment_count,
        "comment_status": comment_status,

        "source": "KEIRIN.JP",
        "http_status": response.status_code,
    }


# ============================================================
# メイン処理
# ============================================================

def main():
    started = now_jst()
    today = started.date()

    result = {
        "schema_version": SCHEMA_VERSION,
        "source": "KEIRIN.JP",
        "target_date": today.isoformat(),
        "started_at": started.isoformat(),
        "finished_at": "",
        "status": "running",

        "events_found": 0,
        "events_checked": 0,
        "events_with_races": 0,

        "races_found": 0,
        "races_checked": 0,
        "races_fetched": 0,
        "races_excluded_other_date": 0,
        "races_date_unknown": 0,
        "races_failed": 0,

        "rider_count": 0,
        "commented_rider_count": 0,

        "events": [],
        "races": [],
        "errors": [],
        "warnings": [],
    }

    session = requests.Session()
    session.headers.update(HEADERS)

    print("=" * 60)
    print(" KEIRIN.JP 公式出走表取得 v4")
    print("=" * 60)
    print("対象日:", today.isoformat())

    try:
        # ----------------------------------------------------
        # 1. 開催一覧
        # ----------------------------------------------------

        print("[1/4] 公式開催一覧を取得...")

        landing = request_page(
            session,
            SCHEDULE_URL,
        )

        schedule = request_json(
            session,
            SCHEDULE_API,
            referer=landing.url,
        )

        all_events = find_events(schedule)

print(“公式開催JSONから取得した開催数:”, len(all_events))

開催一覧HTMLにある遷移パラメータとJSONの値を照合する。

for event in all_events:
html_encp = find_schedule_event_encp(
landing.text,
event[“venue”],
)

if html_encp:
    if html_encp != event["encp"]:
        print(
            f'  {event["venue"]}: '
            "開催パラメータがJSONとHTMLで異なります。"
            "HTML側の値を採用します。"
        )
        event["encp"] = html_encp
    else:
        print(
            f'  {event["venue"]}: '
            "JSONとHTMLの開催パラメータが一致"
        )
else:
    print(
        f'  {event["venue"]}: '
        "HTML側の開催パラメータを特定できません。"
        "JSONの値を維持します。"
    )

result[“events_found”] = len(all_events)

        print("開催候補:", len(all_events))

        candidate_events = []

        for event in all_events:
            start = parse_date(event.get("start_date"))

            if start is None:
                result["warnings"].append({
                    "stage": "event_date",
                    "venue": event["venue"],
                    "message": "開催初日を解析できません",
                })
                continue

            if start <= today:
                candidate_events.append(event)

        print("開催詳細の確認対象:", len(candidate_events))

        # ----------------------------------------------------
        # 2. 開催別レース一覧
        # ----------------------------------------------------

        print("[2/4] 開催別レース一覧を取得...")

        candidate_races = []

        for event in candidate_events:
            result["events_checked"] += 1

            event_record = {
                "venue": event["venue"],
                "venue_code": event["venue_code"],
                "start_date": event["start_date"],
                "race_count": 0,
                "status": "確認中",
            }

            try:
                races = fetch_event_races(session, event)

                event_record["race_count"] = len(races)
                event_record["status"] = (
                    "レース一覧取得済み"
                    if races
                    else "レース一覧0件"
                )

                candidate_races.extend(races)

                if races:
                    result["events_with_races"] += 1

            except Exception as exc:
                event_record["status"] = "取得失敗"

                result["errors"].append({
                    "stage": "race_list",
                    "venue": event["venue"],
                    "error": f"{type(exc).__name__}: {exc}",
                })

            result["events"].append(event_record)

            print(
                f'  {event["venue"]}: '
                f'{event_record["race_count"]}レース'
            )

            time.sleep(PAUSE)

        unique_races = []
        seen_races = set()

        for race in candidate_races:
            key = (
                race.get("venue_code"),
                race.get("race_encp"),
            )

            if key in seen_races:
                continue

            seen_races.add(key)
            unique_races.append(race)

        result["races_found"] = len(unique_races)

        print("個別レース候補:", len(unique_races))

        # ----------------------------------------------------
        # 3. 個別レース・日付検証
        # ----------------------------------------------------

        print("[3/4] 個別レースの出走表を取得...")

        for index, race in enumerate(unique_races, start=1):
            label = f'{race["venue"]} {race["race_no"]}R'

            result["races_checked"] += 1

            print(
                f"  [{index}/{len(unique_races)}] {label}"
            )

            try:
                parsed = extract_race_data(session, race)

                # 個別ページの日付を最優先する。
                actual_date = parse_date(
                    parsed.get("race_date_display")
                )

                # 個別ページの日付が不明なら、
                # 開催別JSONの日付を補助情報として確認する。
                if actual_date is None:
                    actual_date = parse_date(
                        race.get("race_date")
                    )

                if actual_date is None:
                    result["races_date_unknown"] += 1

                    result["warnings"].append({
                        "stage": "race_date",
                        "venue": race["venue"],
                        "race_no": race["race_no"],
                        "message": (
                            "個別ページと開催一覧の両方で"
                            "レース日を確認できません"
                        ),
                    })

                    print("    日付不明: 対象外")
                    time.sleep(PAUSE)
                    continue

                parsed["verified_race_date"] = actual_date.isoformat()

                if actual_date != today:
                    result["races_excluded_other_date"] += 1

                    print(
                        "    別日として除外:",
                        actual_date.isoformat(),
                    )

                    time.sleep(PAUSE)
                    continue

                if not parsed["riders"]:
                    raise ValueError(
                        "出走表の選手データが0人です"
                    )

                if parsed["validation"]["duplicate_car_numbers"]:
                    result["warnings"].append({
                        "stage": "validation",
                        "venue": race["venue"],
                        "race_no": race["race_no"],
                        "message": "車番が重複しています",
                    })

                result["races"].append(parsed)
                result["races_fetched"] += 1
                result["rider_count"] += parsed["rider_count"]
                result["commented_rider_count"] += parsed[
                    "comment_count"
                ]

                print(
                    "    成功:",
                    parsed["rider_count"],
                    "人 / コメント:",
                    parsed["comment_status"],
                )

            except Exception as exc:
                result["races_failed"] += 1

                result["errors"].append({
                    "stage": "race_detail",
                    "venue": race.get("venue", ""),
                    "race_no": race.get("race_no", ""),
                    "error": f"{type(exc).__name__}: {exc}",
                })

                print(
                    "    取得失敗:",
                    f"{type(exc).__name__}: {exc}",
                )

            time.sleep(PAUSE)

        # ----------------------------------------------------
        # 4. 検証・集計
        # ----------------------------------------------------

        print("[4/4] 取得結果を検証...")

        events_without_races = [
            event
            for event in result["events"]
            if event["race_count"] == 0
        ]

        # 開催場のレース一覧が1つでも0件なら、
        # 全開催の取得が完了したとは判定しない。
        if events_without_races:
            result["warnings"].append({
                "stage": "event_coverage",
                "message": "レース一覧を取得できていない開催場があります",
                "venues": [
                    event["venue"]
                    for event in events_without_races
                ],
            })

        if result["races_fetched"] == 0:
            result["status"] = "error"

        elif (
            result["races_failed"] > 0
            or result["races_date_unknown"] > 0
            or result["errors"]
            or events_without_races
        ):
            result["status"] = "partial"

        else:
            result["status"] = "ok"

        fetched_by_venue = {}

        for race in result["races"]:
            venue = race["venue"]

            fetched_by_venue[venue] = (
                fetched_by_venue.get(venue, 0) + 1
            )

        result["summary"] = {
            "target_date": today.isoformat(),
            "fetched_by_venue": fetched_by_venue,
            "race_count": len(result["races"]),
            "rider_count": result["rider_count"],
            "commented_rider_count": (
                result["commented_rider_count"]
            ),
            "comment_missing_rider_count": max(
                0,
                result["rider_count"]
                - result["commented_rider_count"],
            ),
            "events_without_races": [
                event["venue"]
                for event in events_without_races
            ],
        }

        if not result["races"]:
            result["warnings"].append({
                "stage": "final_validation",
                "message": (
                    "対象日の出走表が0件です。"
                    "開催一覧、レース一覧、日付判定を確認してください。"
                ),
            })

    except Exception as exc:
        result["status"] = "error"

        result["errors"].append({
            "stage": "main",
            "error": f"{type(exc).__name__}: {exc}",
        })

    finally:
        session.close()

    # --------------------------------------------------------
    # JSON保存
    # --------------------------------------------------------

    result["finished_at"] = now_jst().isoformat()

    OUTPUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("=" * 60)
    print("取得結果")
    print("状態:", result["status"])
    print("開催候補:", result["events_found"])
    print("開催確認:", result["events_checked"])
    print("レース候補:", result["races_found"])
    print("確認レース:", result["races_checked"])
    print("当日分の取得成功:", result["races_fetched"])
    print("別日として除外:", result["races_excluded_other_date"])
    print("日付不明:", result["races_date_unknown"])
    print("取得失敗:", result["races_failed"])
    print("選手データ件数:", result["rider_count"])
    print("コメント取得件数:", result["commented_rider_count"])
    print("エラー件数:", len(result["errors"]))
    print("警告件数:", len(result["warnings"]))
    print("保存先:", OUTPUT)
    print("=" * 60)

    if result["errors"]:
        print(json.dumps(
            result["errors"],
            ensure_ascii=False,
            indent=2,
        ))

    if result["warnings"]:
        print(json.dumps(
            result["warnings"],
            ensure_ascii=False,
            indent=2,
        ))

    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()

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
    headers = dict(HEADERS)
    headers["Accept"] = "application/json, text/javascript, */*; q=0.01"

    if referer:
        headers["Referer"] = referer

    response = session.get(
        url,
        params=params,
        headers=headers,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    try:
        return response.json()
    except ValueError as exc:
        raise ValueError(
            f"JSON解析失敗: {response.url}"
        ) from exc



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
    """
    指定開催場の公式HTMLから開催パラメータ候補をすべて抽出する。
    候補が複数あっても捨てず、後続のJSJ049検証に渡す。
    """
    from bs4 import BeautifulSoup

    if not html or not venue:
        return []

    soup = BeautifulSoup(html, "html.parser")
    today = now_jst().date()
    venue_normalized = re.sub(r"\s+", "", venue)

    link_pattern = re.compile(
        r"""formlnk\(\s*['"]/sp/racelist['"]\s*,\s*['"]([^'"]+)['"]""",
        re.IGNORECASE,
    )

    date_pattern = re.compile(
        r"(\d{1,2})/(\d{1,2})"
        r"(?:\s*[（(][月火水木金土日][）)])?"
        r"\s*[～〜~－―-]\s*"
        r"(\d{1,2})/(\d{1,2})"
    )

    candidates = []
    seen = set()

    for element in soup.find_all(True):
        onclick = element.get("onclick", "")

        if not isinstance(onclick, str):
            continue

        match = link_pattern.search(onclick)

        if not match:
            continue

        encp = match.group(1).strip()

        if not encp or encp in seen:
            continue

        parent = element
        matched_today = False

        # リンクから親要素をたどり、開催場と日付を確認する。
        for _ in range(10):
            if parent is None:
                break

            text = parent.get_text(" ", strip=True)
            normalized_text = re.sub(r"\s+", "", text)

            if venue_normalized not in normalized_text:
                parent = parent.parent
                continue

            # ページ全体などの巨大な領域は使わない。
            if len(text) > 1500:
                break

            for date_match in date_pattern.finditer(text):
                sm, sd, em, ed = map(
                    int,
                    date_match.groups(),
                )

                try:
                    start_date = datetime(
                        today.year, sm, sd
                    ).date()

                    end_year = today.year

                    if (em, ed) < (sm, sd):
                        end_year += 1

                    end_date = datetime(
                        end_year, em, ed
                    ).date()

                except ValueError:
                    continue

                if start_date <= today <= end_date:
                    matched_today = True
                    break

            if matched_today:
                break

            parent = parent.parent

        if matched_today:
            seen.add(encp)
            candidates.append(encp)

    print(
        f"  {venue}: HTML開催パラメータ候補="
        f"{len(candidates)}件"
    )

    return candidates

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
            "day": "",
            "race_date": "",
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
    """
    開催パラメータ候補を公式JSJ049で検証し、
    対象日に実際のレースが存在する候補を採用する。
    """
    event_encp = clean(event.get("encp"))
    venue = clean(event.get("venue"))
    venue_code = clean(event.get("venue_code"))

    if not event_encp:
        raise ValueError("開催パラメータencpがありません")

    today = now_jst().date()
    today_text = today.isoformat()

    candidates = []
    seen_encp = set()

    def add_candidate(encp, source):
        encp = clean(encp)

        if not encp or encp in seen_encp:
            return

        seen_encp.add(encp)
        candidates.append((encp, source))

    # JSON由来の候補を最初に試す。
    add_candidate(event_encp, "schedule_json")

    # 公式HTMLから候補を追加する。
    try:
        schedule_url = (
            f"{SCHEDULE_URL}?kday={today.strftime('%Y%m%d')}"
        )
        schedule_response = request_page(session, schedule_url)

        html_candidates = find_schedule_event_encp(
            schedule_response.text,
            venue,
        )

        for encp in html_candidates:
            add_candidate(encp, "schedule_html")

    except Exception as exc:
        print(
            f"  {venue}: HTML候補取得失敗 "
            f"{type(exc).__name__}: {exc}"
        )

    print(
        f"  {venue}: 検証する開催パラメータ="
        f"{len(candidates)}件"
    )

    # 候補ごとに公式APIを呼び出し、当日分を検証する。
    for candidate_encp, source in candidates:
        print(
            f"  {venue}: JSJ049試行 "
            f"source={source} encp={candidate_encp}"
        )

        try:
            payload = request_json(
                session,
                RACE_LIST_API,
                params={"type": "JSJ049", "encp": candidate_encp},
            )

        except Exception as exc:
            print(
                f"  {venue}: JSJ049通信失敗 "
                f"{type(exc).__name__}: {exc}"
            )
            continue

        if not isinstance(payload, dict):
            print(f"  {venue}: JSON形式が不正")
            continue

        result_cd = str(payload.get("resultCd", ""))

        print(
            f"  {venue}: JSJ049 resultCd={result_cd} "
            f"messageCd={payload.get('messageCd', '')}"
        )

        if result_cd != "0":
            continue

        days = payload.get("kaisaiDayList", [])

        if not isinstance(days, list):
            continue

        races = []
        seen_races = set()

        for day_index, day in enumerate(days):
            if not isinstance(day, dict):
                continue

            day_date_raw = first_value(
                day,
                "strKaisaiDate",
                "kaisaiDate",
                "strEventDate",
                "kaisaiYmd",
                "strKaisaiYmd",
                "raceDate",
                "strRaceDate",
            )

            day_date = parse_date(day_date_raw)

            # 開催日が明示されていない場合は、
            # 開催一覧の初日と日程リストの順序から補完する。
            # 日付が確定できない場合は採用しない。
            if day_date is None:
                event_start_date = parse_date(
                    event.get("start_date")
                )

                if event_start_date is not None:
                    from datetime import timedelta
                    day_date = (
                        event_start_date
                        + timedelta(days=day_index)
                    )

            if day_date is None:
                print(
                    f"  {venue}: 日付を確認できない日程を除外 "
                    f"day_index={day_index}, "
                    f"keys={list(day.keys())}"
                )
                continue

            actual_date = day_date.isoformat()

            if actual_date != today_text:
                print(
                    f"  [日付除外] 開催場={venue} "
                    f"日程番号={day_index} "
                    f"取得日={actual_date} "
                    f"対象日={today_text}"
                )
                continue

            print(
                f"  [当日確認] 開催場={venue} "
                f"日程番号={day_index} "
                f"開催日={actual_date}"
            )

            day_name = clean(
                first_value(
                    day,
                    "strNitiji",
                    "strDay",
                    "strKaisaiDate",
                )
            )

            programs = day.get("raceProgramList", [])

            if not isinstance(programs, list):
                continue

            for program in programs:
                if not isinstance(program, dict):
                    continue

                race_encp = clean(
                    first_value(program, "strRaceLnkPrm")
                )

                race_no = clean(
                    first_value(program, "strRaceNo")
                )

                if not race_encp or not race_no:
                    continue

                try:
                    race_no_int = int(
                        re.sub(r"\D", "", race_no)
                    )
                except ValueError:
                    continue

                if not 1 <= race_no_int <= 12:
                    continue

                race_key = (
                    today_text,
                    str(race_no_int),
                    race_encp,
                )

                if race_key in seen_races:
                    continue

                seen_races.add(race_key)

                race_path = clean(
                    first_value(program, "strRaceLnk")
                )

                races.append({
                    "venue": venue,
                    "venue_code": venue_code,
                    "event_start_date": event.get("start_date"),
                    "day": day_name,
                    "race_date": today_text,
                    "race_no": str(race_no_int),
                    "race_name": clean(
                        first_value(
                            program,
                            "strSyumokuName",
                        )
                    ),
                    "race_url": urljoin(
                        BASE,
                        race_path or "/sp/race",
                    ),
                    "race_encp": race_encp,
                    "event_encp": candidate_encp,
                    "list_source": f"JSJ049_{source}",
                })

        if races:
            races.sort(
                key=lambda race: int(race["race_no"])
            )

            # 一覧の日付だけで採用せず、
            # 個別レースの開催日と競輪場も確認する。
            if not validate_race_list_candidate(
                session,
                races[0],
                venue,
                today_text,
            ):
                print(
                    f"  {venue}: 候補を不採用。"
                    "次の開催パラメータを検証します"
                )
                continue

            print(
                f"  {venue}: 当日分{len(races)}レース取得 "
                f"source={source}"
            )

            return races

        print(
            f"  {venue}: 正常応答だが当日レースなし"
        )

    # すべての候補で取得できなかった場合のみPOSTを試す。
    for candidate_encp, source in candidates:
        fallback_event = dict(event)
        fallback_event["encp"] = candidate_encp

        print(
            f"  {venue}: 公式POSTを試行 "
            f"source={source}"
        )

        try:
            fallback_races = fetch_races_from_post(
                session,
                fallback_event,
            )

            if fallback_races:
                print(
                    f"  {venue}: POST候補"
                    f"{len(fallback_races)}レース"
                )

                # POSTの候補には日付がないため、
                # ここでは当日分と確定せず検証対象として返す。
                for race in fallback_races:
                    race["list_source"] = (
                        f"official_post_{source}"
                    )

                return fallback_races

        except Exception as exc:
            print(
                f"  {venue}: POST失敗 "
                f"{type(exc).__name__}: {exc}"
            )

    print(
        f"  {venue}: 全候補でレース一覧を取得できませんでした"
    )

    return []


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

def validate_race_list_candidate(session, race, expected_venue, today_text):
    """
    レース一覧の候補から先頭レースを開き、
    実際の開催日と競輪場が一致するか検証する。
    """
    try:
        response = request_page(
            session,
            race["race_url"],
            params={"encp": race["race_encp"]},
            referer=SCHEDULE_URL,
        )

        html = response.text or ""
        base_info = extract_assignment(html, "SC0201")

        if not isinstance(base_info, dict):
            print(
                f"    候補検証NG: 開催情報を解析できない "
                f"{expected_venue}"
            )
            return False

        meta = base_info.get("C0201data", {})

        if not isinstance(meta, dict):
            return False

        actual_date_raw = first_value(meta, "txtEventDate")
        actual_date = parse_date(actual_date_raw)

        actual_venue = clean(
            first_value(meta, "joName", "jyoName")
        )

        normalized_expected = re.sub(r"\s+", "", expected_venue)
        normalized_actual = re.sub(r"\s+", "", actual_venue)

        date_ok = (
            actual_date is not None
            and actual_date.isoformat() == today_text
        )
        venue_ok = (
            normalized_actual != ""
            and normalized_expected in normalized_actual
        )

        print(
            f"    候補検証: 期待={expected_venue}/{today_text} "
            f"実際={actual_venue}/{actual_date_raw} "
            f"結果={'OK' if date_ok and venue_ok else 'NG'}"
        )

        return date_ok and venue_ok

    except Exception as exc:
        print(
            f"    候補検証失敗: "
            f"{type(exc).__name__}: {exc}"
        )
        return False

# ============================================================
# 公式コメント
# ============================================================

def fetch_official_comments(
    session,
    race_encp,
    referer=None,
    html=None,
    race_info=None,
):
    """公式API・ページ内JSON・代替サイトからコメントを取得する。"""
    race_info = race_info or {}
    comments = {}
    found_paths = set()

    def inspect_payload(payload, source):
        if not isinstance(payload, (dict, list)):
            return

        for path, obj in walk_dicts(payload):
            # コメントに関連するキーを持つ階層を調べる。
            comment_keys = [
                key for key in obj
                if any(
                    word in str(key).lower()
                    for word in (
                        "comment",
                        "kome",
                        "jikou",
                    )
                )
                or "コメント" in str(key)
            ]

            for key in comment_keys:
                value = obj.get(key)

                if isinstance(value, str) and value.strip():
                    diagnostic = (
                        source,
                        path,
                        str(key),
                        value.strip()[:100],
                    )

                    if diagnostic not in found_paths:
                        found_paths.add(diagnostic)
                        print(
                            "  コメント候補:",
                            f"取得元={source}",
                            f"階層={path}",
                            f"項目={key}",
                            f"値={value.strip()[:100]}",
                        )

            # 車番とコメントが同じ選手データ内にあるか確認する。
            car_no = str(first_value(
                obj,
                "syaban",
                "shaban",
                "carNo",
                "car_no",
            )).strip()

            comment = first_value(
                obj,
                "sensyuComment",
                "comment",
                "commentText",
                "strComment",
                "jikouComment",
                "riderComment",
                "rider_comment",
                "playerComment",
                "player_comment",
            )

            if (
                car_no.isdigit()
                and 1 <= int(car_no) <= 9
                and isinstance(comment, str)
                and comment.strip()
            ):
                comments[car_no] = comment.strip()

    # 1. 公式JSON APIを調べる。
    try:
        # まず従来の条件で取得する
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

        inspect_payload(payload, "JSJ041-skbn=1")

        # コメントが見つからなければ、skbnを省略して再取得する
        if not comments:
            try:
                payload_without_skbn = request_json(
                    session,
                    f"{BASE}/sp/json",
                    params={
                        "type": "JSJ041",
                        "encp": race_encp,
                    },
                    referer=referer or SCHEDULE_URL,
                )

                inspect_payload(
                    payload_without_skbn,
                    "JSJ041-skbn省略",
                )

                if isinstance(payload_without_skbn, dict):
                    print(
                        "  skbn省略時のトップ階層:",
                        list(payload_without_skbn.keys())[:40],
                    )

                if comments:
                    print(
                        "  コメント取得成功: skbn省略条件"
                    )

            except Exception as exc:
                print(
                    "  skbn省略取得失敗:",
                    f"{type(exc).__name__}: {exc}",
                )
        # レースごとのコメント項目の有無を診断
        if isinstance(payload, dict):
            print(
                "  コメント項目診断:",
                f"senComMei={payload.get('senComMei', '<なし>')!r}",
                f"yosoinMei={payload.get('yosoinMei', '<なし>')!r}",
            )

            riders = payload.get("sensyuTypeInfo", [])

            if isinstance(riders, list):
                print(
                    f"  選手データ件数={len(riders)}"
                )

                for rider in riders:
                    if not isinstance(rider, dict):
                        continue

                    print(
                        "  選手コメント診断:",
                        f"車番={rider.get('syaban', '?')}",
                        f"選手={rider.get('sensyuName', '?')}",
                        f"sensyuComment="
                        f"{rider.get('sensyuComment', '<キーなし>')!r}",
                    )
        inspect_payload(payload, "JSJ041")

        if isinstance(payload, dict):
            print(
                "  JSJ041トップ階層:",
                list(payload.keys())[:40],
            )

    except Exception as exc:
        print(
            "  JSJ041取得失敗:",
            f"{type(exc).__name__}: {exc}",
        )

    # 2. レースページ内に埋め込まれたJSONを調べる。
    if html:
        assignment_pattern = re.compile(
            r"""jsonData\s*\[\s*["']([^"']+)["']\s*\]\s*=\s*"""
        )

        decoder = json.JSONDecoder()
        matches = list(assignment_pattern.finditer(html))

        print(
            f"  ページ内JSON診断: "
            f"埋め込みデータ候補={len(matches)}"
        )

        for match in matches:
            variable_name = match.group(1)
            source_text = html[match.end():].lstrip()

            try:
                payload = decoder.raw_decode(source_text)[0]
            except (json.JSONDecodeError, ValueError):
                continue

            inspect_payload(
                payload,
                f"ページ内JSON:{variable_name}",
            )
    official_comment_count = len(comments)

    # 3. netkeirinの選手コメント一覧を代替取得先として試す。
    # 日付・会場コード・レース番号が不足している場合は推測しない。
    race_date = clean(race_info.get("race_date", ""))
    venue_code = clean(race_info.get("venue_code", ""))
    race_no = clean(race_info.get("race_no", ""))

    parsed_date = parse_date(race_date)
    venue_digits = re.sub(r"\D", "", venue_code)
    race_digits = re.sub(r"\D", "", race_no)

    if (
        parsed_date is not None
        and venue_digits
        and race_digits
        and 1 <= int(race_digits) <= 12
    ):
        race_id = (
            parsed_date.strftime("%Y%m%d")
            + venue_digits.zfill(2)
            + race_digits.zfill(2)
        )

        fallback_url = (
            "https://keirin.netkeiba.com/race/player_comment/"
            f"?race_id={race_id}"
        )

        try:
            from bs4 import BeautifulSoup

            response = request_page(
                session,
                fallback_url,
                referer=referer or SCHEDULE_URL,
            )

            soup = BeautifulSoup(
                response.text,
                "html.parser",
            )

            heading = soup.find(
                lambda tag: (
                    tag.name in ("h1", "h2", "h3", "h4")
                    and "選手コメント一覧"
                    in tag.get_text(" ", strip=True)
                )
            )

            if heading:
                table = heading.find_next("table")

                if table:
                    for row in table.find_all("tr"):
                        cells = row.find_all(
                            ["td", "th"],
                            recursive=False,
                        )

                        if len(cells) < 3:
                            continue

                        car_text = cells[0].get_text(
                            " ", strip=True
                        )
                        car_match = re.match(
                            r"^\s*([1-9])\b",
                            car_text,
                        )

                        if not car_match:
                            continue

                        car_no = car_match.group(1)
                        cell_texts = [
                            cell.get_text(" ", strip=True)
                            for cell in cells
                        ]

                        # コメント欄は通常、行の末尾にある。
                        comment = cell_texts[-1].strip()

                        if (
                            comment
                            and comment not in (
                                "コメント",
                                "選手名",
                                "車",
                            )
                            and len(comment) <= 300
                        ):
                            if car_no not in comments:
                                comments[car_no] = comment

            if len(comments) > official_comment_count:
                print(
                    "  代替取得でコメントを追加:",
                    f"追加={len(comments) - official_comment_count}人分",
                    f"合計={len(comments)}人分",
                    f"race_id={race_id}",
                )
                return comments, "取得済み"

            print(
                "  netkeirinページからコメント本文を抽出できません:",
                race_id,
            )

        except Exception as exc:
            print(
                "  netkeirinコメント取得失敗:",
                f"{type(exc).__name__}: {exc}",
            )
    else:
        print(
            "  netkeirin代替取得をスキップ:",
            "日付・会場コード・レース番号を確認できません",
        )

    if comments:
        car_numbers = sorted(comments.keys(), key=int)
        print(
            "  コメント診断結果:",
            f"部分取得={len(comments)}人分",
            f"車番={','.join(car_numbers)}",
        )
        return comments, "取得済み"

    print(
        "  コメント診断結果: 本文の取得先を特定できていません"
    )
    return {}, "本文未取得"


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
    """
    公式の位置情報を読み取り、連続する位置番号から
    ライン候補を作成する。

    注意:
    位置情報が欠落している場合は無理に補完しない。
    生成したグループは検証前の候補として扱う。
    """

    empty_result = {
        "line_type": "",
        "line_order": [],
        "lines": [],
        "line_status": "公式データなし",
    }

    if not isinstance(line_info, dict):
        return empty_result

    line_data = line_info.get("narabiyoso", {})

    if not isinstance(line_data, dict):
        return empty_result

    line_type = clean(
        first_value(line_data, "lineKeitai")
    )

    raw_order = line_data.get("shaban", [])

    if not isinstance(raw_order, list):
        raw_order = []

    line_order = []
    seen_cars = set()

    for item in raw_order:
        if not isinstance(item, dict):
            continue

        car_no = clean(
            first_value(item, "shaban", "syaban")
        )

        if not car_no:
            continue

        position_raw = first_value(item, "ichi")

        try:
            position = int(position_raw)
        except (TypeError, ValueError):
            position = None

        # 同じ車番が重複していた場合は重複を保存しない。
        if car_no in seen_cars:
            continue

        seen_cars.add(car_no)

        line_order.append({
            "position": position,
            "car_no": car_no,
        })

    # 位置番号のない選手を含む場合、
    # 不確かなグループを作らず位置情報のみ保存する。
    if not line_order:
        return {
            "line_type": line_type,
            "line_order": [],
            "lines": [],
            "line_status": "公式位置情報なし",
        }

    if any(
        item["position"] is None
        or item["position"] < 1
        for item in line_order
    ):
        return {
            "line_type": line_type,
            "line_order": line_order,
            "lines": [],
            "line_status": "位置番号不明・グループ未確定",
        }

    # 位置番号の順番で並べる。
    line_order.sort(
        key=lambda item: item["position"]
    )

    positions = [
        item["position"]
        for item in line_order
    ]

    # 同じ位置番号が複数ある場合は、安全のため分割しない。
    if len(positions) != len(set(positions)):
        return {
            "line_type": line_type,
            "line_order": line_order,
            "lines": [],
            "line_status": "位置番号重複・グループ未確定",
        }

    # 連続する位置番号ごとにグループ化する。
    # 例: 1,2,3 / 5 / 7,8,9
    groups = []
    current_group = []
    previous_position = None

    for item in line_order:
        position = item["position"]

        if (
            current_group
            and position != previous_position + 1
        ):
            groups.append(current_group)
            current_group = []

        current_group.append(item)
        previous_position = position

    if current_group:
        groups.append(current_group)

    lines = []

    for index, group in enumerate(groups, start=1):
        car_numbers = [
            item["car_no"]
            for item in group
        ]

        lines.append({
            "line_no": index,
            "car_numbers": car_numbers,
            "positions": [
                item["position"]
                for item in group
            ],
            "rider_count": len(car_numbers),
            "is_single": len(car_numbers) == 1,
            "status": "位置番号から生成した候補",
        })

    return {
        "line_type": line_type,
        "line_order": line_order,
        "lines": lines,
        "line_status": "位置番号からグループ候補を生成・検証待ち",
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

        meta_for_comments = {}

    if isinstance(base_info, dict):
        meta_for_comments = base_info.get(
            "C0201data", {}
        ) or {}

    if not isinstance(meta_for_comments, dict):
        meta_for_comments = {}

    official_comments, comment_fetch_status = (
        fetch_official_comments(
            session,
            race["race_encp"],
            referer=response.url,
            html=html,
            race_info={
                "race_date": (
                    race.get("race_date")
                    or first_value(
                        meta_for_comments,
                        "txtEventDate",
                    )
                ),
                "venue_code": race.get("venue_code", ""),
                "race_no": race.get("race_no", ""),
            },
        )
    )

    for rider in riders:
        car_no = str(rider.get("car_no", "")).strip()
        rider_name = str(rider.get("name", "")).strip()

        comment = str(
            official_comments.get(car_no, "")
        ).strip()

        # 選手名と同じ文字列はコメント本文として扱わない。
        normalize_text = lambda value: re.sub(
            r"[\s　]+", "", str(value or "")
        )

        if (
            not comment
            or normalize_text(comment) == normalize_text(rider_name)
        ):
            comment = ""

        if comment:
            rider["comment"] = comment
            rider["comment_available"] = True
            rider["comment_status"] = "取得済み"
        else:
            rider["comment"] = ""
            rider["comment_available"] = False
            rider["comment_status"] = "本文未取得"

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
def load_previous_comments(target_date):
    """同じ日付の前回コメントを読み込む。選手名だけの値は除外する。"""

    if not OUTPUT.exists():
        return {}

    try:
        previous = json.loads(
            OUTPUT.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"前回コメントの読み込み失敗: {exc}")
        return {}

    if not isinstance(previous, dict):
        return {}

    if previous.get("target_date") != target_date.isoformat():
        print("前回データは別日付のため、コメントを引き継ぎません")
        return {}

    def normalize_text(value):
        return re.sub(r"[\s　]+", "", str(value or ""))

    cache = {}
    rejected_names = 0

    for race in previous.get("races", []):
        if not isinstance(race, dict):
            continue

        race_key = str(race.get("race_encp", "")).strip()
        if not race_key:
            continue

        valid_riders = {}

        for rider in race.get("riders", []):
            if not isinstance(rider, dict):
                continue

            car_no = str(rider.get("car_no", "")).strip()
            name = str(rider.get("name", "")).strip()
            comment = str(rider.get("comment", "")).strip()

            if not car_no or not comment:
                continue

            if normalize_text(comment) == normalize_text(name):
                rejected_names += 1
                continue

            if len(comment) > 300:
                continue

            valid_riders[car_no] = comment

        if valid_riders:
            cache[race_key] = valid_riders

    print(
        "前回コメント引き継ぎ:",
        sum(len(riders) for riders in cache.values()),
        "人分",
    )
    print(
        "選手名と一致したため除外:",
        rejected_names,
        "人分",
    )

    return cache
# ============================================================
# メイン処理
# ============================================================

def main():
    started = now_jst()
    today = started.date()
    previous_comments = load_previous_comments(today)
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
            params={
                "kday": today.strftime("%Y%m%d"),
            },
            referer=landing.url,
        )

        # 公式JSONから開催情報を取得
        all_events = find_events(schedule)

        result["events_found"] = len(all_events)

        print("開催候補:", len(all_events))

        # 対象日までに開催初日を迎えた開催を抽出
        candidate_events = []

        for event in all_events:
            start = parse_date(
                event.get("start_date")
            )

            if start is None:
                result["warnings"].append({
                    "stage": "event_date",
                    "venue": event["venue"],
                    "message": "開催初日を解析できません",
                })
                continue

            if start <= today:
                candidate_events.append(event)

        print(
            "開催詳細の確認対象:",
            len(candidate_events),
        )

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
                races = fetch_event_races(
                    session,
                    event,
                )

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

                # 同じ日付・同じレース・同じ車番の前回コメントを確認する。
                race_key = str(
                    parsed.get("race_encp", "")
                ).strip()

                old_riders = previous_comments.get(
                    race_key, {}
                )

                def normalize_comment_text(value):
                    return re.sub(
                        r"[\s　]+",
                        "",
                        str(value or ""),
                    )

                riders = parsed.get("riders", [])

                # 現在のコメントと前回コメントを照合する。
                for rider in riders:
                    car_no = str(
                        rider.get("car_no", "")
                    ).strip()

                    rider_name = str(
                        rider.get("name", "")
                    ).strip()

                    current_comment = str(
                        rider.get("comment", "")
                    ).strip()

                    old_comment = str(
                        old_riders.get(car_no, "")
                    ).strip()

                    # 選手名そのものはコメントとして扱わない。
                    if (
                        current_comment
                        and normalize_comment_text(current_comment)
                        == normalize_comment_text(rider_name)
                    ):
                        current_comment = ""

                    if (
                        old_comment
                        and normalize_comment_text(old_comment)
                        == normalize_comment_text(rider_name)
                    ):
                        old_comment = ""

                    if not current_comment and old_comment:
                        rider["comment"] = old_comment
                        rider["comment_available"] = True
                        rider["comment_status"] = "前回取得分を保持"

                    elif current_comment:
                        rider["comment"] = current_comment
                        rider["comment_available"] = True

                        if rider.get("comment_status") != "前回取得分を保持":
                            rider["comment_status"] = "取得済み"

                    else:
                        rider["comment"] = ""
                        rider["comment_available"] = False
                        rider["comment_status"] = "本文未取得"

                # コメント本文がある選手だけを集計する。
                parsed["comment_count"] = sum(
                    1
                    for rider in riders
                    if rider.get("comment_available") is True
                    and str(
                        rider.get("comment", "")
                    ).strip()
                )

                if (
                    riders
                    and parsed["comment_count"] == len(riders)
                ):
                    parsed["comment_status"] = "取得済み"

                elif parsed["comment_count"] > 0:
                    parsed["comment_status"] = "一部取得"

                else:
                    parsed["comment_status"] = "本文未取得"

                # 個別ページの日付を最優先する。
                actual_date = parse_date(
                    parsed.get("race_date_display")
                )

                # 個別ページの日付が不明なら開催一覧の日付を確認する。
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
        print(f"DEBUG events: {len(result['events'])}")
        print(f"DEBUG races: {len(result['races'])}")
        print(f"DEBUG races_fetched: {result['races_fetched']}")
        print(f"DEBUG races_failed: {result['races_failed']}")
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

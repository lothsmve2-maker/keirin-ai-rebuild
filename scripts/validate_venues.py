import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "venues.json"


def main():
    if not DATA_FILE.exists():
        print("ERROR: data/venues.json がありません")
        return 1

    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"ERROR: JSON形式が正しくありません: {exc}")
        return 1

    venues = data.get("venues")

    if not isinstance(venues, list) or not venues:
        print("ERROR: venues が空、または配列ではありません")
        return 1

    errors = []
    ids = []
    names = []

    for index, venue in enumerate(venues, start=1):
        if not isinstance(venue, dict):
            errors.append(f"{index}件目: データ形式が不正")
            continue

        for field in ("venue_code", "name", "region", "prefecture"):
            if not venue.get(field):
                errors.append(f"{index}件目: {field} がありません")

        if venue.get("venue_code"):
            ids.append(str(venue["venue_code"]))

        if venue.get("name"):
            names.append(venue["name"])

    if len(ids) != len(set(ids)):
        errors.append("競輪場コードが重複しています")

    if len(names) != len(set(names)):
        errors.append("競輪場名が重複しています")

    if errors:
        print("検証失敗")
        for error in errors:
            print("-", error)
        return 1

    print("検証成功")
    print(f"登録件数: {len(venues)}")
    print("必須項目: OK")
    print("コードの重複: なし")
    print("名前の重複: なし")
    print("注意: 外部コードとの一致は別途確認が必要です")

    return 0


if __name__ == "__main__":
    sys.exit(main())

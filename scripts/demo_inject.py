# -*- coding: utf-8 -*-
"""筒井晃生のアカウント専用：日にち・場所・言葉・色を指定してデモ用のたよりを1通投入する。

投入された手紙は demo_mode=1 になり、画面上（受信箱の「封の中」の棚）の
「demo 開封日を変更」から開封予定日時を自由に動かせる。
通常の手紙（demo_mode=0）には一切影響しない。対象ユーザーは「筒井晃生」に固定。

使い方（例）:
    python scripts/demo_inject.py --text "あの日の決断は、正しかったと思う。"
    python scripts/demo_inject.py \\
        --text "今日の自分は、3か月後の自分を信頼できるだろうか。" \\
        --date 2026-05-01T08:30 \\
        --place 東京都渋谷区 \\
        --color "#C9D4D2" \\
        --arrive 2026-08-01T09:00

引数:
    --text   言葉（必須・80字まで。超過分は切り捨て）
    --date   投函日時（ISO形式。日付だけなら 12:00 扱い。省略時は今）
    --place  封じた場所の名前（下のプリセットにあれば座標も自動で入る）
    --lat/--lng  場所の座標（プリセットにない場所を使う時に指定）
    --color  いまの気分の色（#rrggbb。省略時 #C9D4D2）
    --arrive 開封予定日時（ISO形式。省略時は今から30日後。過去にすれば「届いた」状態になる）

本番（Render）では Shell から同じコマンドを実行する。DBの場所は app.py の
DB_PATH 解決（TAYORI_DB_PATH 等の環境変数）をそのまま使う。
"""
import argparse
import json
import os
import re
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import DB_PATH, init_db  # noqa: E402  DBパス解決とスキーマ担保はアプリ本体に任せる

# このスクリプトで投入できるのは筒井晃生のアカウントだけ（デモ操作の誤爆防止）
ALLOWED_USERNAME = "筒井晃生"

# 場所のプリセット（名前 → 代表座標）。アプリ本体と同じく小数3桁のエリア座標のみ持つ。
PLACES = {
    "東京都渋谷区": (35.664, 139.698),
    "東京都新宿区": (35.694, 139.703),
    "横浜市": (35.444, 139.638),
    "名古屋市": (35.181, 136.906),
    "京都市": (35.011, 135.768),
    "大阪市": (34.694, 135.502),
    "札幌市": (43.062, 141.354),
    "仙台市": (38.268, 140.869),
    "金沢市": (36.561, 136.657),
    "福岡市": (33.590, 130.402),
}

# 封をした日の気象スナップショット（seed_demo_data.py と同じ穏やかな既定値）
SEAL_ENV = json.dumps({"temp": 18.0, "condition": "cloud", "tag": "mild"})


def _parse_dt(raw, label):
    try:
        dt = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        print(f"{label} の日時が読めません: {raw}（例: 2026-05-01 または 2026-05-01T08:30）")
        sys.exit(1)
    # 日付だけ渡された時は昼の12:00として扱う（time_bucket が「昼」になる）
    if "T" not in raw and " " not in raw:
        dt = dt.replace(hour=12)
    return dt


def _time_bucket(dt):
    # アプリ本体（index.html の timeBucket）と同じ区切り
    h = dt.hour
    if 5 <= h < 11:
        return "morning"
    if 11 <= h < 17:
        return "day"
    if 17 <= h < 21:
        return "evening"
    return "night"


def main():
    ap = argparse.ArgumentParser(description=f"デモ用のたよりを {ALLOWED_USERNAME} に1通投入する")
    ap.add_argument("--text", required=True, help="言葉（80字まで）")
    ap.add_argument("--date", help="投函日時（ISO形式。省略時は今）")
    ap.add_argument("--place", help="封じた場所の名前")
    ap.add_argument("--lat", type=float, help="場所の緯度（プリセット外の場所用）")
    ap.add_argument("--lng", type=float, help="場所の経度（プリセット外の場所用）")
    ap.add_argument("--color", default="#C9D4D2", help="いまの気分の色（#rrggbb）")
    ap.add_argument("--arrive", help="開封予定日時（ISO形式。省略時は今から30日後）")
    args = ap.parse_args()

    # --lat / --lng は必ずペアで渡す（片方だけでは座標にならない）
    if (args.lat is None) != (args.lng is None):
        print("--lat と --lng は必ずセットで指定してください（片方だけでは座標になりません）。")
        return 1
    # 座標だけ渡されても、場所名（--place）が無いと地図のラベルにできない
    if args.lat is not None and not args.place:
        print("--lat/--lng を使うときは --place で場所名も一緒に指定してください（地図のラベルに使います）。")
        return 1

    poem = args.text.rstrip()[:80]
    if not poem.strip():
        print("言葉が空です。--text に本文を渡してください。")
        return 1

    if not re.fullmatch(r"#[0-9a-fA-F]{6}", args.color):
        print(f"色は #rrggbb 形式で指定してください: {args.color}")
        return 1

    sent = _parse_dt(args.date, "--date") if args.date else datetime.now()
    arrive = _parse_dt(args.arrive, "--arrive") if args.arrive else datetime.now() + timedelta(days=30)

    # 場所：プリセットにあれば座標を補完。なければ --lat/--lng が必須（地図に点として出すため）
    area_name = area_lat = area_lng = time_bucket = None
    if args.place:
        latlng = PLACES.get(args.place)
        if args.lat is not None and args.lng is not None:
            latlng = (args.lat, args.lng)
        if not latlng:
            print(f"「{args.place}」はプリセットにない地名です。詳しい地名を使うときは、"
                  f"--lat（緯度）と --lng（経度）を一緒に渡してください。")
            print(f"例: --place 東京都目黒区下目黒 --lat 35.633 --lng 139.706")
            print(f"（座標なしで使えるプリセット: {', '.join(PLACES)}）")
            return 1
        area_name = args.place.strip()[:80]
        area_lat, area_lng = round(latlng[0], 3), round(latlng[1], 3)
        if not (-90.0 <= area_lat <= 90.0 and -180.0 <= area_lng <= 180.0):
            print(f"座標が範囲外です: {area_lat}, {area_lng}")
            return 1
        time_bucket = _time_bucket(sent)

    init_db()
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    user = db.execute("SELECT id, username FROM users WHERE username=?", (ALLOWED_USERNAME,)).fetchone()
    if not user:
        print(f"ユーザー「{ALLOWED_USERNAME}」が見つかりません。（DB: {DB_PATH}）")
        db.close()
        return 1

    arrive_label = f"{arrive.month}月{arrive.day}日 {arrive:%H:%M}"
    db.execute(
        """INSERT INTO letters
           (id, user_id, poem, photo, voice, sent_date, arrive_date, arrive_at,
            arrive_label, arrive_hidden, opened, emos, from_reply,
            seal_env, seal_color, area_name, area_lat, area_lng, time_bucket, demo_mode)
           VALUES (?,?,?,NULL,NULL,?,?,?,?,0,0,'[]',0,?,?,?,?,?,?,1)""",
        (
            secrets.token_hex(8),
            user["id"],
            poem,
            sent.isoformat(timespec="seconds"),
            arrive.date().isoformat(),
            arrive.isoformat(timespec="seconds"),
            arrive_label,
            SEAL_ENV,
            args.color,
            area_name,
            area_lat,
            area_lng,
            time_bucket,
        ),
    )
    db.commit()
    db.close()
    where = f" / 場所 {area_name}" if area_name else ""
    print(f"デモたよりを {user['username']} に投入しました。"
          f"投函 {sent:%Y-%m-%d %H:%M} → 開封 {arrive:%Y-%m-%d %H:%M}{where} / 色 {args.color}（DB: {DB_PATH}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

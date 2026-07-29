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
        --color "#C9D4D2" \\
        --arrive 2026-08-01T09:00

引数:
    --text   言葉（必須・80字まで。超過分は切り捨て）
    --date   投函日時（ISO形式。日付だけなら 12:00 扱い。省略時は今）
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

# 2026-07-29：場所のプリセット（--place / --lat / --lng）は地図ごと畳んだ。
# letters に位置カラムがもう無い。

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
    ap.add_argument("--color", default="#C9D4D2", help="いまの気分の色（#rrggbb）")
    ap.add_argument("--arrive", help="開封予定日時（ISO形式。省略時は今から30日後）")
    args = ap.parse_args()

    poem = args.text.rstrip()[:80]
    if not poem.strip():
        print("言葉が空です。--text に本文を渡してください。")
        return 1

    if not re.fullmatch(r"#[0-9a-fA-F]{6}", args.color):
        print(f"色は #rrggbb 形式で指定してください: {args.color}")
        return 1

    sent = _parse_dt(args.date, "--date") if args.date else datetime.now()
    arrive = _parse_dt(args.arrive, "--arrive") if args.arrive else datetime.now() + timedelta(days=30)

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
            seal_env, seal_color, time_bucket, demo_mode)
           VALUES (?,?,?,NULL,NULL,?,?,?,?,0,0,'[]',0,?,?,?,1)""",
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
            time_bucket,
        ),
    )
    db.commit()
    db.close()
    print(f"デモたよりを {user['username']} に投入しました。"
          f"投函 {sent:%Y-%m-%d %H:%M} → 開封 {arrive:%Y-%m-%d %H:%M} / 色 {args.color}（DB: {DB_PATH}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

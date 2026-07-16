# -*- coding: utf-8 -*-
"""デモ用のたよりを既存アカウントに投入する。

投入された手紙は demo_mode=1 になり、画面上（封の中の棚・受信箱）に現れる
「demo 開封日を変更」の操作列から開封予定日時を自由に動かせる。
通常の手紙（demo_mode=0）には一切影響しない。

使い方:
    python scripts/seed_demo_data.py <username>
    python scripts/seed_demo_data.py <username> --force   # 既にデモ手紙があっても追加投入

本番（Render）では Shell から同じコマンドを実行する。DBの場所は app.py の
DB_PATH 解決（TAYORI_DB_PATH 等の環境変数）をそのまま使うので、ローカルでも
本番でも実行環境に合ったDBへ入る。
"""
import json
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import DB_PATH, init_db  # noqa: E402  DBパス解決とスキーマ担保はアプリ本体に任せる

# デモの3通：投函日は過去（60日前・30日前・今日）、開封予定はすべて未来（+30/+60/+90日）。
# 「まだ開けられない」状態から、開封日を動かして開封体験までを見せられる。
DEMO_LETTERS = [
    ("今日の自分は、3か月後の自分を信頼できるだろうか。", "#5b7c99"),
    ("やっぱり忘れてしまったことが多いんだな。", "#8b9d6f"),
    ("あのときの決断は、今でも正しかったと思う。", "#e67e50"),
]

# 封をした日の気象スナップショット（指示書の partially_cloudy / 18℃ 相当）
SEAL_ENV = json.dumps({"temp": 18.0, "condition": "cloud", "tag": "mild"})


def seed_demo_data(username, force=False):
    init_db()
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    user = db.execute("SELECT id, username FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        names = [r["username"] for r in db.execute("SELECT username FROM users").fetchall()]
        print(f"ユーザー「{username}」が見つかりません。存在するユーザー: {', '.join(names)}")
        return 1

    existing = db.execute(
        "SELECT COUNT(*) AS c FROM letters WHERE user_id=? AND demo_mode=1", (user["id"],)
    ).fetchone()["c"]
    if existing and not force:
        print(f"既にデモ手紙が {existing} 通あります。追加するなら --force を付けてください。")
        return 1

    now = datetime.now()
    for i, (poem, seal_color) in enumerate(DEMO_LETTERS):
        sent = now - timedelta(days=30 * (2 - i))
        arrive = now + timedelta(days=30 * (i + 1))
        db.execute(
            """INSERT INTO letters
               (id, user_id, poem, photo, voice, sent_date, arrive_date, arrive_at,
                arrive_label, arrive_hidden, opened, emos, from_reply,
                seal_env, seal_color, demo_mode)
               VALUES (?,?,?,NULL,NULL,?,?,?,?,0,0,'[]',0,?,?,1)""",
            (
                secrets.token_hex(8),
                user["id"],
                poem,
                sent.isoformat(timespec="seconds"),
                arrive.date().isoformat(),
                arrive.isoformat(timespec="seconds"),
                f"{30 * (i + 1)}日後",
                SEAL_ENV,
                seal_color,
            ),
        )
    db.commit()
    db.close()
    print(f"デモ手紙 {len(DEMO_LETTERS)} 通を {user['username']} に投入しました。（DB: {DB_PATH}）")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    sys.exit(seed_demo_data(args[0], force="--force" in sys.argv))

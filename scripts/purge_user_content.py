# -*- coding: utf-8 -*-
"""指定ユーザーの「手紙・気分・地図」データだけを消す。アカウント（users行）は残す。

消す対象テーブル:
    - letters        手紙本体（seal_color/open_color=気分色、area_lat/lng=地図座標を含む）
    - thread         手紙に紐づくやりとり（letter_id 経由）
    - answers        一筆箋の回答（letter_id 経由）
    - notes          気分メモ（色つきノート）
    - drafts         下書き
    - survey_letters 一筆箋の枠（answers の親）

一切触らないもの:
    - users 行そのもの（ログイン情報・メール・認証状態・last_lat/last_lon 等）
      → 「アカウントはそのまま」を守る。

安全装置:
    - 既定はドライラン（件数を表示するだけ）。実際に消すには --yes が必須。
    - 消す前に必ず (1) DB全体のスナップショットと (2) 対象ユーザーの全行JSON を保存する。
    - 対象は --username で明示（既定は筒井晃生）。誤爆防止のため username 完全一致のみ。

本番(Render)では Shell から実行する。app.py の DB 解決に相乗りするため、
稼働中アプリと同じライブDBを書き換え、アプリが自動で永続ディスクへ保存する
（demo_inject.py と同じ仕組み）。

使い方:
    # まず件数だけ確認（何も消えない）
    python scripts/purge_user_content.py --username 筒井晃生
    # 確認できたら実行（バックアップを取ってから削除）
    python scripts/purge_user_content.py --username 筒井晃生 --yes
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import DB_PATH, init_db  # noqa: E402  DBパス解決とスキーマ担保はアプリ本体に任せる
try:
    from app import _PERSIST_DB_PATH  # 永続ディスク上のDB（本番）。バックアップの置き場所に使う
except ImportError:
    _PERSIST_DB_PATH = DB_PATH

DEFAULT_USERNAME = "筒井晃生"

# user_id を直接持つテーブル（この順で消す＝子→親）
USER_TABLES = ["notes", "drafts", "letters", "survey_letters"]
# letter_id 経由で letters にぶら下がるテーブル（letters より先に消す）
LETTER_CHILD_TABLES = ["thread", "answers"]


def _counts(db, user_id, letter_ids):
    """対象ユーザーの各テーブル件数を数える。"""
    out = {}
    for t in USER_TABLES:
        out[t] = db.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id=?", (user_id,)).fetchone()[0]
    if letter_ids:
        ph = ",".join("?" * len(letter_ids))
        for t in LETTER_CHILD_TABLES:
            out[t] = db.execute(
                f"SELECT COUNT(*) FROM {t} WHERE letter_id IN ({ph})", letter_ids
            ).fetchone()[0]
    else:
        for t in LETTER_CHILD_TABLES:
            out[t] = 0
    return out


def _dump_rows(db, user_id, letter_ids):
    """対象ユーザーの全行を辞書にまとめる（削除前バックアップ用）。"""
    data = {"user_id": user_id, "dumped_at": datetime.now().isoformat(timespec="seconds")}
    # users 行も記録しておく（消さないが、万一の復元の手がかりに）
    urow = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    data["users"] = dict(urow) if urow else None
    for t in USER_TABLES:
        rows = db.execute(f"SELECT * FROM {t} WHERE user_id=?", (user_id,)).fetchall()
        data[t] = [dict(r) for r in rows]
    if letter_ids:
        ph = ",".join("?" * len(letter_ids))
        for t in LETTER_CHILD_TABLES:
            rows = db.execute(f"SELECT * FROM {t} WHERE letter_id IN ({ph})", letter_ids).fetchall()
            data[t] = [dict(r) for r in rows]
    else:
        for t in LETTER_CHILD_TABLES:
            data[t] = []
    return data


def main():
    ap = argparse.ArgumentParser(description="指定ユーザーの手紙・気分・地図データだけを消す（アカウントは残す）")
    ap.add_argument("--username", default=DEFAULT_USERNAME, help=f"対象ユーザー名（既定: {DEFAULT_USERNAME}）")
    ap.add_argument("--yes", action="store_true", help="実際に削除する（無しならドライラン）")
    args = ap.parse_args()

    init_db()
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")

    user = db.execute("SELECT id, username FROM users WHERE username=?", (args.username,)).fetchone()
    if not user:
        print(f"ユーザー「{args.username}」が見つかりません。（DB: {DB_PATH}）")
        db.close()
        return 1
    user_id = user["id"]

    letter_ids = [r["id"] for r in db.execute("SELECT id FROM letters WHERE user_id=?", (user_id,)).fetchall()]
    counts = _counts(db, user_id, letter_ids)
    total = sum(counts.values())

    print(f"対象ユーザー: {user['username']}（id={user_id}）")
    print(f"DB: {DB_PATH}")
    print("削除予定の件数:")
    for t in USER_TABLES + LETTER_CHILD_TABLES:
        print(f"  {t:<16} {counts[t]}")
    print(f"  {'合計':<16} {total}")
    print("※ users 行（アカウント・ログイン・メール認証）は一切消しません。")

    if total == 0:
        print("消すデータがありません。終了します。")
        db.close()
        return 0

    if not args.yes:
        print("\n[ドライラン] 何も削除していません。実行するには --yes を付けてください。")
        db.close()
        return 0

    # --- 削除前バックアップ（永続ディスク側に置く。/var/data はデプロイでも消えない） ---
    backup_dir = os.path.dirname(os.path.abspath(_PERSIST_DB_PATH))
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = "".join(ch for ch in args.username if ch.isalnum()) or "user"
    snap_path = os.path.join(backup_dir, f"purge-{safe_name}-{ts}.db")
    json_path = os.path.join(backup_dir, f"purge-{safe_name}-{ts}.json")
    try:
        snap = sqlite3.connect(snap_path)
        with snap:
            db.backup(snap)  # WAL安全な一貫スナップショット
        snap.close()
        print(f"\nDBスナップショットを保存: {snap_path}")
    except Exception as e:
        print(f"スナップショット保存に失敗しました。中止します: {e}")
        db.close()
        return 1
    try:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(_dump_rows(db, user_id, letter_ids), fh, ensure_ascii=False, indent=2)
        print(f"対象ユーザーの全行JSONを保存: {json_path}")
    except Exception as e:
        print(f"JSONダンプに失敗しました。中止します: {e}")
        db.close()
        return 1

    # --- 削除（子テーブル→親テーブルの順） ---
    deleted = {}
    with db:  # トランザクション：途中で失敗したら全部ロールバック
        if letter_ids:
            ph = ",".join("?" * len(letter_ids))
            for t in LETTER_CHILD_TABLES:
                cur = db.execute(f"DELETE FROM {t} WHERE letter_id IN ({ph})", letter_ids)
                deleted[t] = cur.rowcount
        else:
            for t in LETTER_CHILD_TABLES:
                deleted[t] = 0
        for t in USER_TABLES:
            cur = db.execute(f"DELETE FROM {t} WHERE user_id=?", (user_id,))
            deleted[t] = cur.rowcount

    print("\n削除しました:")
    for t in USER_TABLES + LETTER_CHILD_TABLES:
        print(f"  {t:<16} {deleted.get(t, 0)}")
    print(f"  {'合計':<16} {sum(deleted.values())}")

    # 残存確認
    remain = _counts(db, user_id, [r["id"] for r in
                     db.execute("SELECT id FROM letters WHERE user_id=?", (user_id,)).fetchall()])
    still = sum(remain.values())
    print(f"残存: {still} 件" + ("（クリーンです）" if still == 0 else "（要確認）"))
    print("本番では、この直後にアプリの保存ループが永続ディスクへ反映します（最大30秒）。")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

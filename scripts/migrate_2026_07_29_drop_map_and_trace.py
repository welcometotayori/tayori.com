# -*- coding: utf-8 -*-
"""2026-07-29 レガシー掃除：地図の系統と、旧 trace 列を落とす。

app.py の起動時マイグレーション（ADD COLUMN の羅列）には**入れない**。
ADD COLUMN は取り返しがつくが DROP COLUMN はつかないので、デプロイのたびに
黙って走る場所には置かない。人が一度だけ、バックアップを取ってから走らせる。

    python3 scripts/migrate_2026_07_29_drop_map_and_trace.py            # 下見（何もしない）
    python3 scripts/migrate_2026_07_29_drop_map_and_trace.py --apply    # 実行

DBの場所は TAYORI_DB_PATH（未設定なら ./tayori.db）。--apply の時は必ず
<db>.bak-before-drop-map をそばに作ってから触る。

──────────────────────────────────────────────────────────────
落とすもの
  letters : trace, area_name, area_lat, area_lng,
            open_area_name, open_area_lat, open_area_lng,
            grid_id, excluded_from_aggregate
  users   : aggregate_opt_out, night_map_notice_seen_at
  table   : mood_grid

残すもの（消さないこと）
  letters.trace_z          … 公開経路に出る唯一の打鍵データ
  unemptyable_trash.trace  … 屑籠の7日溶解用。letters とは別物
  users.last_lat / last_lon … 天気（雨の日に届く便り）が使う。地図とは別系統

──────────────────────────────────────────────────────────────
ロールバック

 A. まるごと戻す（推奨・データも戻る）
      1. アプリを止める
      2. cp <db>.bak-before-drop-map <db>
      3. app.py を bceb0f6（フェーズ2の最後）まで戻して起動
    ※ バックアップを取ってから戻すまでの間に書かれたことばは失われる。
      先に <db> を別名で退避してから上書きすること。

 B. 列だけ戻す（中身は戻らない＝全部 NULL）
      アプリのコードを戻したうえで、下を流す。旧 trace と旧エリアの中身は
      消えているので、あくまで「スキーマだけ元の形」に戻る。
        ALTER TABLE letters ADD COLUMN trace TEXT;
        ALTER TABLE letters ADD COLUMN area_name TEXT;
        ALTER TABLE letters ADD COLUMN area_lat REAL;
        ALTER TABLE letters ADD COLUMN area_lng REAL;
        ALTER TABLE letters ADD COLUMN open_area_name TEXT;
        ALTER TABLE letters ADD COLUMN open_area_lat REAL;
        ALTER TABLE letters ADD COLUMN open_area_lng REAL;
        ALTER TABLE letters ADD COLUMN grid_id TEXT;
        ALTER TABLE letters ADD COLUMN excluded_from_aggregate INTEGER DEFAULT 0;
        ALTER TABLE users   ADD COLUMN aggregate_opt_out INTEGER DEFAULT 0;
        ALTER TABLE users   ADD COLUMN night_map_notice_seen_at TEXT;
        CREATE TABLE IF NOT EXISTS mood_grid (
            grid_id TEXT NOT NULL, mood INTEGER NOT NULL, n INTEGER NOT NULL,
            latest TEXT, lat REAL, lng REAL, PRIMARY KEY (grid_id, mood));
"""
import os
import shutil
import sqlite3
import sys

DROP_COLUMNS = [
    ("letters", "trace"),
    ("letters", "area_name"),
    ("letters", "area_lat"),
    ("letters", "area_lng"),
    ("letters", "open_area_name"),
    ("letters", "open_area_lat"),
    ("letters", "open_area_lng"),
    ("letters", "grid_id"),
    ("letters", "excluded_from_aggregate"),
    ("users", "aggregate_opt_out"),
    ("users", "night_map_notice_seen_at"),
]
DROP_TABLES = ["mood_grid"]

# 消してはいけないもの。取り違えの事故を型で止める（人の注意力に預けない）。
NEVER_DROP = {("letters", "trace_z"), ("unemptyable_trash", "trace"),
              ("users", "last_lat"), ("users", "last_lon")}
assert not (set(DROP_COLUMNS) & NEVER_DROP)


def columns(db, table):
    try:
        return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return set()


def tables(db):
    return {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def main():
    apply = "--apply" in sys.argv
    path = os.environ.get("TAYORI_DB_PATH") or "tayori.db"
    if not os.path.exists(path):
        print(f"DBが見つかりません: {path}")
        return 1

    ver = tuple(int(x) for x in sqlite3.sqlite_version.split("."))
    if ver < (3, 35, 0):
        print(f"SQLite {sqlite3.sqlite_version} では DROP COLUMN が使えません（3.35以降が要る）。")
        return 1

    db = sqlite3.connect(path)
    have_tables = tables(db)
    todo_cols = [(t, c) for t, c in DROP_COLUMNS
                 if t in have_tables and c in columns(db, t)]
    todo_tables = [t for t in DROP_TABLES if t in have_tables]

    print(f"DB: {path}  (SQLite {sqlite3.sqlite_version})")
    if not todo_cols and not todo_tables:
        print("落とすものはもうありません（このスクリプトは冪等です）。")
        return 0
    for t, c in todo_cols:
        n = db.execute(
            f"SELECT COUNT(*) FROM {t} WHERE {c} IS NOT NULL").fetchone()[0]
        print(f"  列  {t}.{c}    （値の入っている行: {n}）")
    for t in todo_tables:
        n = db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  表  {t}        （{n}行）")

    if not apply:
        print("\n下見です。実行するには --apply を付けてください。")
        return 0

    bak = path + ".bak-before-drop-map"
    db.close()
    shutil.copy2(path, bak)
    print(f"\nバックアップ: {bak}")

    db = sqlite3.connect(path)
    try:
        for t, c in todo_cols:
            db.execute(f"ALTER TABLE {t} DROP COLUMN {c}")
            print(f"  drop {t}.{c}")
        for t in todo_tables:
            db.execute(f"DROP TABLE {t}")
            print(f"  drop table {t}")
        db.commit()
    except Exception:
        db.rollback()
        print(f"\n失敗しました。DBは触れていないか途中です。"
              f"戻すには: cp {bak} {path}")
        raise
    finally:
        db.close()

    print("\n完了。戻すには、アプリを止めてから:")
    print(f"  cp {bak} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

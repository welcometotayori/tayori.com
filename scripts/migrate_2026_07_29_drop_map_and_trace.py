# -*- coding: utf-8 -*-
"""2026-07-29 レガシー掃除：地図の系統と、旧 trace 列を落とす。

app.py の起動時マイグレーション（ADD COLUMN の羅列）には**入れない**。
ADD COLUMN は取り返しがつくが DROP COLUMN はつかないので、デプロイのたびに
黙って走る場所には置かない。人が一度だけ、バックアップを取ってから走らせる。

    python3 scripts/migrate_2026_07_29_drop_map_and_trace.py            # 下見
    python3 scripts/migrate_2026_07_29_drop_map_and_trace.py --apply    # 実行

──────────────────────────────────────────────────────────────
【DBが二枚あることについて】ここを外すと移行が黙って消える

TAYORI_DB_PATH が設定されていると、app.py はローカルキャッシュ方式で動く
（app.py の _LOCAL_CACHE）：

    実行用（ライブ）  $TMPDIR/tayori-live.db   ← アプリが実際に読み書きする
    永続用（ディスク） TAYORI_DB_PATH          ← 30秒ごと＋終了時に上書きされる

本番（Render）は TAYORI_DB_PATH=/var/data/tayori.db なので、この方式で動いている。
だから永続用だけを移行しても、**30秒以内にライブ側の古いスキーマで上書きされて
移行が消える**。このスクリプトは両方を見て、ライブ側から先に落とす。

そして順序：**新しいコードを先にデプロイしてから、これを走らせる。**
新コードは旧スキーマのままでも動くことを確認してある（INSERT は列を明示していて、
SELECT はもう消した列を見ない）。逆順にすると、旧コードが消えた列を SELECT して
500 を返す時間ができる。

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
      1. サービスを止める（Render なら Suspend）
      2. cp <永続DB>.bak-before-drop-map <永続DB>
      3. ライブDB（$TMPDIR/tayori-live.db）は消す。次の起動で永続から復元される
         ※ _restore_from_durable はライブDBが「存在しない時だけ」復元する。
           消し忘れると古いライブがそのまま使われ、永続を上書きし返す
      4. コードを bceb0f6（フェーズ2の最後）まで戻して起動
    ※ バックアップ以後に書かれたことばは失われる。先に現物を別名で退避すること。

 B. 列だけ戻す（中身は戻らない＝全部 NULL）
      コードを戻したうえで、ライブDBと永続DBの**両方**に下を流す。
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
import sqlite3
import sys
import tempfile

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


def targets():
    """移行すべきDBを、アプリと同じ規則で並べて返す。ライブが先（順序が意味を持つ）。

    永続を先に落とすと、その直後の定期永続化（30秒）でライブの古いスキーマに
    上書きされて移行が消える。ライブを先に落としておけば、途中で永続化が走っても
    書き下ろされるのは移行済みのほうになる。"""
    durable = os.environ.get("TAYORI_DB_PATH")
    out = []
    if durable:
        local_cache = os.environ.get("TAYORI_DB_LOCAL_CACHE", "1") == "1"
        if local_cache:
            live = (os.environ.get("TAYORI_LIVE_DB_PATH")
                    or os.path.join(tempfile.gettempdir(), "tayori-live.db"))
            out.append(("ライブ（アプリが実際に使う）", live))
        out.append(("永続（ディスク）", durable))
    else:
        out.append(("DB", "tayori.db"))
    return [(label, p) for label, p in out if os.path.exists(p)]


def columns(db, table):
    try:
        return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return set()


def tables(db):
    return {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def pending(db):
    have = tables(db)
    cols = [(t, c) for t, c in DROP_COLUMNS if t in have and c in columns(db, t)]
    tbls = [t for t in DROP_TABLES if t in have]
    return cols, tbls


def backup(path):
    """SQLite 自身のバックアップAPIで取る。アプリが動いたまま実行されるので、
    ファイルの丸ごとコピーは使わない（書き込みの途中を掴むと、integrity_check は
    通るのに中身が古い、という一番たちの悪い壊れ方をする）。"""
    bak = path + ".bak-before-drop-map"
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    dst = sqlite3.connect(bak)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    # 取ったものが本当に読めるか、その場で確かめる（黙って壊れた控えを残さない）
    chk = sqlite3.connect(f"file:{bak}?mode=ro", uri=True)
    try:
        if chk.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError(f"バックアップの検査に失敗: {bak}")
        n_l = chk.execute("SELECT COUNT(*) FROM letters").fetchone()[0]
        n_u = chk.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        chk.close()
    return bak, n_l, n_u


def main():
    apply = "--apply" in sys.argv
    tg = targets()
    if not tg:
        print("DBが見つかりません。TAYORI_DB_PATH を確認してください。")
        return 1

    ver = tuple(int(x) for x in sqlite3.sqlite_version.split("."))
    if ver < (3, 35, 0):
        print(f"SQLite {sqlite3.sqlite_version} では DROP COLUMN が使えません（3.35以降が要る）。")
        return 1

    print(f"SQLite {sqlite3.sqlite_version}")
    if len(tg) > 1:
        print("※ ローカルキャッシュ方式です。ライブと永続の両方を移行します"
              "（片方だけだと30秒後に上書きされて消えます）。")

    work = []
    for label, path in tg:
        db = sqlite3.connect(path)
        cols, tbls = pending(db)
        n_l = db.execute("SELECT COUNT(*) FROM letters").fetchone()[0]
        db.close()
        print(f"\n[{label}] {path}  （letters {n_l}行）")
        if not cols and not tbls:
            print("  落とすものはもうありません")
            continue
        for t, c in cols:
            print(f"  列  {t}.{c}")
        for t in tbls:
            print(f"  表  {t}")
        work.append((label, path, cols, tbls))

    if not work:
        print("\nすべて移行済みです（このスクリプトは冪等です）。")
        return 0
    if not apply:
        print("\n下見です。実行するには --apply を付けてください。")
        return 0

    for label, path, cols, tbls in work:
        print(f"\n[{label}] {path}")
        bak, n_l, n_u = backup(path)
        print(f"  バックアップ: {bak}  （letters {n_l}行 / users {n_u}人・検査OK）")
        db = sqlite3.connect(path, timeout=30)
        try:
            for t, c in cols:
                db.execute(f"ALTER TABLE {t} DROP COLUMN {c}")
                print(f"  drop {t}.{c}")
            for t in tbls:
                db.execute(f"DROP TABLE {t}")
                print(f"  drop table {t}")
            db.commit()
        except Exception:
            db.rollback()
            print(f"  失敗。戻すには: cp {bak} {path}")
            raise
        finally:
            db.close()

    print("\n完了。")
    print("ローカルキャッシュ方式の場合、ライブ側の変更は次の永続化（30秒以内）で"
          "ディスクへ書き下ろされます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

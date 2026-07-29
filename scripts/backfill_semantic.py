# -*- coding: utf-8 -*-
"""既にあることばに、意味ベクトルを付ける（冪等）。

    python3 scripts/backfill_semantic.py            # 下見
    python3 scripts/backfill_semantic.py --apply    # 実行

本番（ローカルキャッシュ方式）でも、アプリが実際に使っているライブDBへ書く。
永続ディスクへは、いつもどおり30秒ごとの永続化が運んでくれる
（詳しくは scripts/migrate_2026_07_29_drop_map_and_trace.py の説明を参照）。

作れなかったことば（絵文字だけ・表に無い語だけ、など）は行を作らない。
それは欠損ではなく「意味の成分を持たないことば」で、air_distance は無い成分を
外して残りの重みで正規化する——式を変えずに済む形になっている。

モデルの版（_SEM_MODEL）が変わったら --remodel を付けて走らせると、
古い版のベクトルを作り直す。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import (DB_PATH, _connect, init_db, sem_ready, sem_store,  # noqa: E402
                 _SEM_MODEL, _WRITE_LOCK)


def main():
    apply = "--apply" in sys.argv
    remodel = "--remodel" in sys.argv

    init_db()
    if not sem_ready():
        print("語ベクトル表が読めません。先に scripts/build_semantic_table.py を"
              "走らせて semantic/ を用意してください。")
        return 1

    db = _connect()
    try:
        if remodel:
            todo = db.execute(
                "SELECT l.id, l.poem FROM letters l"
                " LEFT JOIN letter_vectors v ON v.letter_id = l.id"
                " WHERE COALESCE(l.poem,'')<>''"
                "   AND (v.letter_id IS NULL OR v.model<>?)"
                " ORDER BY l.sent_date", (_SEM_MODEL,)).fetchall()
        else:
            todo = db.execute(
                "SELECT l.id, l.poem FROM letters l"
                " LEFT JOIN letter_vectors v ON v.letter_id = l.id"
                " WHERE COALESCE(l.poem,'')<>'' AND v.letter_id IS NULL"
                " ORDER BY l.sent_date").fetchall()

        have = db.execute("SELECT COUNT(*) c FROM letter_vectors").fetchone()["c"]
        total = db.execute(
            "SELECT COUNT(*) c FROM letters WHERE COALESCE(poem,'')<>''").fetchone()["c"]
        print(f"DB: {DB_PATH}")
        print(f"本文のあることば {total} 通 / ベクトル済み {have} / これから {len(todo)}")
        if not todo:
            print("付けるものはありません。")
            return 0
        if not apply:
            print("\n下見です。実行するには --apply を付けてください。")
            return 0

        made = skipped = 0
        with _WRITE_LOCK:
            for r in todo:
                if sem_store(db, r["id"], r["poem"]):
                    made += 1
                else:
                    skipped += 1
            db.commit()
        print(f"\n付けました: {made} 通"
              + (f" / 測れなかった: {skipped} 通（意味の成分を持たないことば）"
                 if skipped else ""))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

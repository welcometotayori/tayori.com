# -*- coding: utf-8 -*-
"""既存の手紙に、気分の宙(v7)用の emos タグを本文から後付けする（バックフィル）。

背景:
    2026-07-24 以降、手紙は投函時に本文から語(emos)を生成して保存するようになった。
    それ以前に投函された手紙は emos が空のままで、気分の宙の「他者経路」（本文を読まない）
    では一つも星にならない。ここで一度だけ本文から語を抽出して emos を埋め、
    以後は他者にも安全に共有できる状態にする。

安全性:
    - 抽出は app 本体と同じ _mood_words_from_poem を使う。保存するのは「語」だけで、
      本文そのものはコピーしない（本文秘匿の鉄則を破らない）。
    - 人の名前・あだ名は所有者の登録名を含めて除く（_mood_name_block_for_user）。
    - 既に emos が入っている手紙（手動タグ付け含む）は絶対に上書きしない。
    - 本文が無い（写真/声だけの）手紙は対象外。
    - 既定はドライラン（何件がどう変わるか表示するだけ）。実書き込みは --yes が必須。
    - --only-intransit を付けると、未開封かつ未到着の「配送中」だけを対象にする
      （宙に実際に出るのは配送中だけなので、影響範囲を最小にしたい時に使う）。

使い方:
    # まず変化を確認（何も書き込まない）
    python scripts/backfill_emos.py
    # 配送中だけに絞って確認
    python scripts/backfill_emos.py --only-intransit
    # 確認できたら実書き込み
    python scripts/backfill_emos.py --yes
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import (  # noqa: E402  DBパス解決・スキーマ担保・抽出ロジックはアプリ本体に相乗り
    DB_PATH, init_db, _is_arrived, _letter_opened,
    _mood_words_from_poem, _mood_name_block_for_user,
)


def _is_empty_emos(v):
    """emos列が実質空か（NULL / '' / '[]' / 空配列JSON）。"""
    if not v:
        return True
    try:
        return not json.loads(v)
    except (ValueError, TypeError):
        return True


def main():
    ap = argparse.ArgumentParser(
        description="既存の手紙に本文から emos タグを後付けする（気分の宙v7用）")
    ap.add_argument("--yes", action="store_true", help="実際に書き込む（無しならドライラン）")
    ap.add_argument("--only-intransit", action="store_true",
                    help="未開封かつ未到着の『配送中』の手紙だけを対象にする")
    args = ap.parse_args()

    init_db()
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")

    rows = db.execute("SELECT * FROM letters").fetchall()
    block_cache = {}   # user_id -> 名前ブロック集合（同一ユーザーで使い回す）
    planned = []       # (id, user_id, [tags])
    skipped_has_emos = skipped_no_words = skipped_not_intransit = 0

    for r in rows:
        if not _is_empty_emos(r["emos"]):
            skipped_has_emos += 1
            continue
        if args.only_intransit:
            try:
                if _letter_opened(r) or _is_arrived(r):
                    skipped_not_intransit += 1
                    continue
            except (TypeError, ValueError):
                skipped_not_intransit += 1
                continue
        uid_ = r["user_id"]
        if uid_ not in block_cache:
            block_cache[uid_] = _mood_name_block_for_user(db, uid_)
        tags = _mood_words_from_poem(r["poem"], block_cache[uid_])
        if not tags:
            skipped_no_words += 1
            continue
        planned.append((r["id"], uid_, tags))

    print(f"DB: {DB_PATH}")
    print(f"全手紙: {len(rows)} 通")
    print(f"  既にemosあり（対象外）      : {skipped_has_emos}")
    if args.only_intransit:
        print(f"  配送中でない（対象外）      : {skipped_not_intransit}")
    print(f"  本文から語が取れず（対象外）: {skipped_no_words}")
    print(f"  → 付与予定                  : {len(planned)}")
    for lid, uid_, tags in planned:
        print(f"     {lid}  user={uid_}  emos={json.dumps(tags, ensure_ascii=False)}")

    if not planned:
        print("変更なし。")
        db.close()
        return 0

    if not args.yes:
        print("\n※ ドライランです。実際に書き込むには --yes を付けてください。")
        db.close()
        return 0

    now = datetime.now().isoformat(timespec="seconds")
    for lid, _uid, tags in planned:
        db.execute("UPDATE letters SET emos=? WHERE id=? AND COALESCE(emos,'[]') IN ('','[]')",
                   (json.dumps(tags, ensure_ascii=False), lid))
    db.commit()
    print(f"\n{len(planned)} 通に emos を付与しました。（{now}）")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

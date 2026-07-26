# -*- coding: utf-8 -*-
"""部屋への移送（_backfill_rooms）が、どの部屋に何通入れるかだけを先に見る（dry-run）。

背景:
    2026-07-26 の部屋機能で、既存の手紙は init_db の中で自動的に部屋へ移される。
    本番では移送が起きてからでないと結果が見えないので、その前に件数だけを確認する。

安全性:
    - 出力するのは「部屋の名前と件数」だけ。本文（poem）も題（title）も一切表示しない。
      運営は本文を読まない、という原則をこのスクリプトも守る。
    - DB には一切書き込まない。read-only で開く。

使い方:
    python scripts/preview_room_backfill.py                   # 既定のDBを見る
    python scripts/preview_room_backfill.py --db /var/data/tayori.db
"""
import argparse
import os
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# app を import すると init_db が走って本番DBを書き換えかねないので、
# 分類ロジックだけを取り出す（TAYORI_DISABLE_NOTIFIER と一時DBで隔離する）。
os.environ.setdefault("TAYORI_DISABLE_NOTIFIER", "1")
os.environ.setdefault("TAYORI_DB_LOCAL_CACHE", "0")
os.environ.setdefault("TAYORI_ENABLE_NETWORK", "0")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="対象のSQLiteファイル（既定: TAYORI_DB_PATH かカレントの tayori.db）")
    args = ap.parse_args()

    path = args.db or os.environ.get("TAYORI_DB_PATH") or "tayori.db"
    if not os.path.exists(path):
        print(f"DBが見つかりません: {path}")
        return 1

    # 隔離：app 本体の init_db が触るDBを一時ファイルに逃がしてから import する。
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["TAYORI_DB_PATH"] = tmp
    try:
        from app import _classify_room, ARCHIVE_ROOM, DEFAULT_ROOMS
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    moved, total = Counter(), 0
    for r in db.execute("SELECT poem, title, mode FROM letters"):
        total += 1
        room = None
        if (r["mode"] or "letter") == "sky":
            room = _classify_room((r["title"] or "") + "\n" + (r["poem"] or ""))
        moved[room or ARCHIVE_ROOM] += 1
    db.close()

    print(f"対象: {path}")
    print(f"手紙 {total} 通の移送先（本文は表示しません）\n")
    import unicodedata

    def disp(s):   # 全角は2幅として数える（等幅端末で列を揃えるため）
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)

    names = list(DEFAULT_ROOMS) + [ARCHIVE_ROOM]
    width = max(disp(n) for n in names)
    for name in names:
        n = moved.get(name, 0)
        bar = "▍" * min(40, n)
        note = "  ← 決め手が無かった分と旧「未来の自分へ」" if name == ARCHIVE_ROOM else ""
        print(f"  {name}{' ' * (width - disp(name))}  {n:>5}  {bar}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

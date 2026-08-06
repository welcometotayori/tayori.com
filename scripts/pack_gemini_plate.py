# -*- coding: utf-8 -*-
"""符号化した束（scripts/embed_gemini.py の出力）を、配る一枚の板に詰め直す。

    python3 scripts/pack_gemini_plate.py --dim 256

出来るもの（semantic/plate/ に置く。**gitには入れない**・R2から配る）:
    plate.npy       float32 (N, dim)  ← np.load(mmap_mode='r') で開く前提
    plate_meta.npz  id列・部屋id列（-1 は部屋なし）
    plate.json      版（模型・次元・片数・作った日・DBの世代）

【なぜ .npy をファイルのまま置くのか】
いまの板は SQLite から10万行を読んで RAM に積んでいる＝**無名メモリ**で、器を
超えた瞬間にプロセスごと殺される（2026-08-02 に実際に落ちた壊れ方）。板は
取り込みの時以外びくとも動かない不変物なので、ファイルにして memmap すれば
ページキャッシュになる——足りなくなればカーネルが捨てて読み直すだけで、死なない。

【なぜ次元をここで決めるのか】
束は768次元で持ってある。Matryoshka なので、前から dim 本だけ切って長さ1に
直せば、その次元で学習された表とほぼ同じに働く。**切るのは無料で、取り直すのは
33時間**。だから配る次元の決定は、いつでもやり直せるこちら側に置く。

  128次元 …  20万片で  102MB（いまの板と同じ重さ）
  256次元 …  20万片で  205MB
  768次元 …  20万片で  614MB（器に載らない）
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARD_DIR = os.path.join(ROOT, "seed", "gemini")
OUT_DIR = os.path.join(ROOT, "semantic", "plate")
DB_PATH = os.environ.get("TAYORI_DB_PATH", os.path.join(ROOT, "tayori.db"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dim", type=int, default=256, help="配る次元（768から切り出す）")
    ap.add_argument("--model", default=os.environ.get("TAYORI_EMB_MODEL", "gemini-embedding-2"))
    args = ap.parse_args()
    import numpy as np

    shards = sorted(glob.glob(os.path.join(SHARD_DIR, "shard_*.npz")))
    if not shards:
        print("束がありません（先に scripts/embed_gemini.py を回す）"); return 1
    os.makedirs(OUT_DIR, exist_ok=True)

    # 何片あるか先に数えて、板を一枚ぶん確保してから書く（2026-08-01 の教訓——
    # list に貯めてから stack すると同じ中身が三重に在る瞬間ができる）。
    n = 0
    for p in shards:
        with np.load(p, allow_pickle=False) as z:
            n += z["vecs"].shape[0]
    print("束 %d ／ 片 %d ／ 切り出す次元 %d" % (len(shards), n, args.dim))

    mat = np.empty((n, args.dim), dtype="float32")
    ids = np.empty(n, dtype=object)
    k = 0
    for p in shards:
        with np.load(p, allow_pickle=False) as z:
            v = z["vecs"][:, :args.dim].astype("float32")
            # Matryoshka は「前から切って長さ1に直す」まででひと組。直さないと
            # 内積がコサインでなくなる（短い次元ほどノルムが1から離れる）。
            nrm = np.linalg.norm(v, axis=1, keepdims=True)
            nrm[nrm == 0] = 1.0
            m = v.shape[0]
            mat[k:k + m] = v / nrm
            ids[k:k + m] = z["ids"]
            k += m

    # 部屋は板に焼く。部屋の割り当てが変わるのは取り込みと同じ運用の出来事で、
    # そのときは板ごと詰め直す。起動のたびに20万行を読んで辞書にすると、
    # ページキャッシュで済ませた板の横で30MBの無名メモリを持つことになる。
    db = sqlite3.connect(DB_PATH)
    room_of = {r[0]: (-1 if r[1] is None else int(r[1]))
               for r in db.execute("SELECT id, room_id FROM external_texts")}
    rooms = np.fromiter((room_of.get(i, -1) for i in ids), dtype="int32", count=n)
    gen = db.execute("SELECT COUNT(*), COALESCE(MAX(rowid),0)"
                     "  FROM external_texts WHERE sky_status='live'").fetchone()

    np.save(os.path.join(OUT_DIR, "plate.npy"), mat)
    np.savez(os.path.join(OUT_DIR, "plate_meta.npz"),
             ids=np.array(list(ids)), rooms=rooms)
    json.dump({"model": args.model, "dim": args.dim, "n": int(n),
               "made_at": datetime.now().isoformat(timespec="seconds"),
               "db_rows": int(gen[0]), "db_max_rowid": int(gen[1])},
              open(os.path.join(OUT_DIR, "plate.json"), "w"), ensure_ascii=False, indent=1)
    mb = mat.nbytes / 1e6
    print("板 %s（%.0fMB）を %s に置きました" % (mat.shape, mb, OUT_DIR))
    print("部屋つき %d片 ／ 部屋なし %d片" % (int((rooms >= 0).sum()), int((rooms < 0).sum())))
    return 0


if __name__ == "__main__":
    sys.exit(main())

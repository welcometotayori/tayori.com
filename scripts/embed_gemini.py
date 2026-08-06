# -*- coding: utf-8 -*-
"""20万片を Gemini の埋め込みAPIで符号化する（手元で回す／本番では走らせない）。

    export GEMINI_API_KEY=...
    python3 scripts/embed_gemini.py            # 続きから。何度止めても良い
    python3 scripts/embed_gemini.py --status   # どこまで進んだかだけ見る

【なぜこの作りなのか】
無料枠は **embed 100リクエスト/分**（実測 2026-08-04）。batchEmbedContents に100件
まとめて入れても *100リクエストとして* 引かれるので、まとめても速くならない。
20万片で約29〜33時間——**一晩では終わらない**。だから、

  ・**途中で止められること**が第一。5000片ごとに束（shard）を書き、次に起こすと
    書けている束を数えて続きから始める。Macを閉じても、429で待たされても、
    電源が落ちても、失うのは書きかけの一束だけ。
  ・**768次元で保存する**。配る板は256か128になるだろうが、Matryoshka なので
    768から切って正規化し直せば作れる（scripts/pack_gemini_plate.py）。
    ここを256で取ってしまうと、「やっぱり512に」でもう一度33時間払うことになる。
  ・**本文は書き出さない**。束に入れるのは id とベクトルだけ。

出来るもの: seed/gemini/shard_00000.npz … （id列 + fp32 768次元）
そのあと scripts/pack_gemini_plate.py で一枚の板に詰め直す。
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "seed", "gemini")
DB_PATH = os.environ.get("TAYORI_DB_PATH", os.path.join(ROOT, "tayori.db"))

MODEL = os.environ.get("TAYORI_EMB_MODEL", "gemini-embedding-2")
DIM = int(os.environ.get("TAYORI_EMB_DIM", "768"))       # 保存する次元（配る次元ではない）
URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:batchEmbedContents" % MODEL

SHARD = 5000          # 一束の片数。これが「止められる粒度」
BATCH = 50            # 1リクエストに入れる件数（割当はこの数だけ引かれる）
RPM = int(os.environ.get("TAYORI_EMB_RPM", "100"))       # 無料枠の実測値。課金したら上げる
TASK = "RETRIEVAL_DOCUMENT"


def _rows(db):
    """符号化する片を、**必ず同じ順**で返す（途中から続けられるのはこれが理由）。
    rowid 順にするのは、取り込みで足された片が必ず後ろに付くから
    ——先頭が動かないので、束の番号と中身の対応が崩れない。"""
    return db.execute(
        "SELECT id, body FROM external_texts WHERE sky_status='live' ORDER BY rowid").fetchall()


def _embed(texts, key, task):
    """まとめて符号化して、長さ1に正規化した list[list[float]] を返す。
    429 は API が言ってきた待ち時間ぶん素直に待つ（こちらで勝手に短くしない）。"""
    import numpy as np
    out = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i:i + BATCH]
        body = {"requests": [{"model": "models/" + MODEL,
                              "content": {"parts": [{"text": t}]},
                              "taskType": task,
                              "outputDimensionality": DIM} for t in chunk]}
        tries = 0
        while True:
            try:
                req = urllib.request.Request(
                    URL, data=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": "application/json", "x-goog-api-key": key})
                with urllib.request.urlopen(req, timeout=180) as r:
                    d = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                wait = 35
                try:
                    err = json.loads(e.read())
                    for det in err.get("error", {}).get("details", []):
                        if det.get("@type", "").endswith("RetryInfo"):
                            wait = int(str(det["retryDelay"]).rstrip("s")) + 2
                except Exception:
                    pass
                if e.code != 429:
                    tries += 1
                    if tries >= 5:
                        raise
                    print("    %s → %ds待って試し直します" % (e.code, wait), flush=True)
                time.sleep(wait)
            except Exception as e:
                tries += 1
                if tries >= 5:
                    raise
                print("    %s → 待って試し直します" % type(e).__name__, flush=True)
                time.sleep(10 * tries)
        for e in d["embeddings"]:
            v = np.asarray(e["values"], dtype="float32")
            n = float(np.linalg.norm(v))
            out.append(v / n if n > 0 else v)
        # 自分で抑える。向こうに429を出させてから待つより、出さずに進むほうが速い。
        time.sleep(max(0.0, BATCH * 60.0 / RPM - 1.0))
    return out


def _shard_path(k):
    return os.path.join(OUT_DIR, "shard_%05d.npz" % k)


def _done_shards(total):
    n = (total + SHARD - 1) // SHARD
    return [k for k in range(n) if os.path.exists(_shard_path(k))], n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="進み具合だけ見る")
    ap.add_argument("--limit", type=int, default=0, help="先頭N片だけ（試し用）")
    args = ap.parse_args()
    try:
        import numpy as np
    except ImportError:
        print("numpy が要ります"); return 1
    os.makedirs(OUT_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    rows = _rows(db)
    if args.limit:
        rows = rows[:args.limit]
    total = len(rows)
    done, n_shards = _done_shards(total)
    print("片 %d / 束 %d（%d片ずつ）／出来ている束 %d" % (total, n_shards, SHARD, len(done)))
    print("模型 %s ・ 保存する次元 %d ・ %d req/分" % (MODEL, DIM, RPM))
    if args.status:
        rest = (n_shards - len(done)) * SHARD
        print("残り約 %d片 ＝ %.1f時間" % (rest, rest / max(RPM, 1) / 60.0))
        return 0
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("GEMINI_API_KEY がありません"); return 1

    t0 = time.time()
    for k in range(n_shards):
        if os.path.exists(_shard_path(k)):
            continue
        part = rows[k * SHARD:(k + 1) * SHARD]
        print("[束 %d/%d] %d片" % (k + 1, n_shards, len(part)), flush=True)
        vecs = _embed([r[1] for r in part], key, TASK)
        # 書きかけを本物の名前で残さない（次に起こしたとき「出来ている」と誤解する）。
        tmp = _shard_path(k) + ".tmp.npz"
        np.savez(tmp, ids=np.array([r[0] for r in part]),
                 vecs=np.asarray(vecs, dtype="float32"))
        os.replace(tmp, _shard_path(k))
        done_n = (k + 1) * SHARD
        el = time.time() - t0
        rate = done_n / max(el, 1.0)
        print("  書きました。ここまで %d片・%.1f片/秒・残り約 %.1f時間"
              % (min(done_n, total), rate, max(0, total - done_n) / max(rate, 1e-6) / 3600.0),
              flush=True)
    print("ぜんぶ出来ました。次は scripts/pack_gemini_plate.py で一枚に詰めます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

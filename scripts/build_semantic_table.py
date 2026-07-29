# -*- coding: utf-8 -*-
"""意味の索引で使う語ベクトル表を作る（手元で一度だけ／本番では走らせない）。

    pip install model2vec numpy tokenizers
    python3 scripts/build_semantic_table.py

出来るもの（どちらも semantic/ に置き、リポジトリに入れる）:
    semantic/potion_ja.npz      語ベクトル表（fp16・行番号＝下のトークンid）  約12MB
    semantic/tokenizer.json.gz  刈り込んだ分かち書き辞書                      約0.3MB

【なぜこの作りなのか】
本番は Render の starter（512MB）。Transformer をそのまま載せると、実測で
e5-small の int8 ONNX でも RSS 411MB——アプリの50MBと合わせて載らなかった
（メモリアリーナを切っても変わらない）。

model2vec は、文の埋め込みモデルを「語 → ベクトル」の静的な表へ蒸留したもの。
推論は表を引いて平均するだけなので torch も onnxruntime も要らず、numpy だけで
0.07ms/件で動く。本番に置くのはこの表と辞書だけ。

【二段階で絞る】元は50万語×256次元（fp32で931MB）。たよりに要るのは日本語だけ。

  1. 語ベクトル表：日本語を含む語＋高頻度の先頭2000語＝25,134語（fp16で12MB）。
     絞る前との比較で 順位相関 0.997・上位5一致 100%＝実質ゼロ劣化。

  2. 分かち書き辞書：ここを絞らないと意味がない。50万語の辞書は Tokenizer を
     組み立てるだけで **+429MB** 掛かる（実測）——表(26MB)よりずっと重い。
     同じ25,134語へ刈り込むと JSON 16MB → 1.0MB、RSS も桁で落ちる。
     刈り込み前後で 順位相関 0.998・上位5一致 100%。日本語の語をすべて残して
     いるので、日本語の切れ目はほぼ変わらない（欧文だけが細かく割れる）。

刈り込みでトークンidが詰まるので、ベクトル表も新しいid順に並べ直す。
こうすると実行時は table[id] で引けて、対応表を持たずに済む。
"""
import gzip
import json
import os
import sys

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "semantic")
SOURCE = "minishlab/potion-multilingual-128M"
# 高頻度の先頭N語は言語によらず残す（数字・記号・英数の断片が混じるため）
KEEP_HEAD = 2000


def has_jp(s):
    s = s.replace("▁", "")
    return any((0x3040 <= ord(c) <= 0x30FF)      # ひらがな・カタカナ
               or (0x4E00 <= ord(c) <= 0x9FFF)   # 漢字
               or (0xFF66 <= ord(c) <= 0xFF9D)   # 半角カナ
               for c in s)


def main():
    try:
        import numpy as np
        from model2vec import StaticModel
    except ImportError:
        print("model2vec と numpy が要ります:  pip install model2vec numpy tokenizers")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"読み込み中: {SOURCE}（初回はダウンロードに数分かかります）")
    m = StaticModel.from_pretrained(SOURCE)
    vecs = np.asarray(m.embedding)
    tokens = [t if isinstance(t, str) else t.form for t in m.tokens]
    tok_json = json.loads(m.tokenizer.to_str())
    if tok_json["model"]["type"] != "Unigram":
        print(f"想定外の分かち書き方式です: {tok_json['model']['type']}")
        return 1
    print(f"  元: {vecs.shape[0]} 語 × {vecs.shape[1]} 次元 / 辞書 "
          f"{len(m.tokenizer.to_str())/1e6:.1f} MB")

    # [PAD]/[UNK] は added_tokens が id で指しているので必ず残す
    must = {0, tok_json["model"].get("unk_id", 1)}
    keep = sorted(must
                  | {i for i, t in enumerate(tokens) if has_jp(t)}
                  | set(range(min(KEEP_HEAD, len(tokens)))))
    old2new = {o: i for i, o in enumerate(keep)}

    # ── 辞書を刈り込む（idを詰め直す）
    pruned = dict(tok_json)
    pruned["model"] = dict(tok_json["model"])
    pruned["model"]["vocab"] = [tok_json["model"]["vocab"][o] for o in keep]
    pruned["model"]["unk_id"] = old2new[tok_json["model"].get("unk_id", 1)]
    pruned["added_tokens"] = [{**a, "id": old2new[a["id"]]}
                              for a in tok_json.get("added_tokens", [])
                              if a["id"] in old2new]
    raw = json.dumps(pruned, ensure_ascii=False)
    tok_path = os.path.join(OUT_DIR, "tokenizer.json.gz")
    with gzip.open(tok_path, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write(raw)

    # ── ベクトル表を新しいid順に並べ直す（行番号＝トークンid）
    table = np.zeros((len(keep), vecs.shape[1]), dtype=np.float16)
    for o in keep:
        table[old2new[o]] = vecs[o]
    npz = os.path.join(OUT_DIR, "potion_ja.npz")
    np.savez_compressed(npz, vecs=table, source=SOURCE, dim=table.shape[1],
                        unk_id=pruned["model"]["unk_id"])

    print(f"  絞り込み: {len(keep)} 語")
    print(f"    {npz}  {os.path.getsize(npz)/1e6:.1f} MB")
    print(f"    {tok_path}  {os.path.getsize(tok_path)/1e6:.2f} MB"
          f"（展開後 {len(raw)/1e6:.1f} MB）")
    print("\n出来ました。semantic/ ごとコミットしてください。")
    print("既存のことばへの付与は scripts/backfill_semantic.py で行います。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

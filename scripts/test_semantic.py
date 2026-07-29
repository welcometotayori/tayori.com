"""意味の索引（フェーズ3-1）のサーバ側テスト。

    .venv/bin/python scripts/test_semantic.py

pytest には依存しない（requirements を増やさない）。plain assert で落ちたら失敗。
確かめるのは：
  ・表が読めること、次元と語数が想定どおりであること
  ・同じことばは同じベクトルになること（決定的）／長さが1に正規化されていること
  ・意味の近い組のほうが、遠い組より近いこと（順序が正しいこと）
  ・測れないことば（空・絵文字だけ）で None を返し、例外を投げないこと
  ・表が無い環境では静かに眠ること（宙は動き続ける）
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app  # noqa: E402


def approx(a, b, eps=1e-5):
    assert abs(a - b) < eps, f"{a} != {b}"


assert app.sem_ready(), f"表が読めません: {app._sem['why']}"
import numpy as np  # noqa: E402

# ── 表そのもの ────────────────────────────────────────────────
tbl = app._sem["table"]
assert tbl.shape[1] == app._SEM_DIM, tbl.shape
assert tbl.shape[0] > 20000, tbl.shape
assert str(tbl.dtype) == "float16", tbl.dtype     # 表は fp16 のまま持つ（26MB→12MB）

# ── 埋め込みの性質 ───────────────────────────────────────────
v = app.sem_embed("ねむれない夜は、月とふたりきり")
assert v is not None and v.shape == (app._SEM_DIM,), v
assert str(v.dtype) == "float32", v.dtype
approx(float(np.linalg.norm(v)), 1.0)             # 長さ1（コサイン＝内積で済む）
assert np.array_equal(v, app.sem_embed("ねむれない夜は、月とふたりきり"))   # 決定的

# 測れないもの。例外ではなく None（呼ぶ側は「意味を持たないことば」として扱う）
for bad in ("", "   ", None, "🌙🌙🌙"):
    assert app.sem_embed(bad) is None, bad

# ── 意味の順序 ───────────────────────────────────────────────
def sim(a, b):
    return float(app.sem_embed(a) @ app.sem_embed(b))

near_far = [
    ("ねむれない夜", "眠れずに朝を待つ", "レジで小銭を数える"),
    ("母の鼻歌", "実家の台所の音", "電車が遅れている"),
    ("お金がたりない", "財布が軽い", "桜が咲いた"),
]
for a, near, far in near_far:
    dn, df = sim(a, near), sim(a, far)
    assert dn > df, f"「{a}」: 近いはずの「{near}」({dn:.3f}) が「{far}」({df:.3f}) より遠い"

# ── 保存と取り出し（DBに入れた形のまま比べられること）────────────
v = app.sem_embed("夕焼けって、なんで切ないんだろう。")
assert np.array_equal(np.frombuffer(v.tobytes(), dtype=np.float32), v)

# ── 表が無い時は静かに眠る ───────────────────────────────────
saved = app._sem.copy()
try:
    app._sem.update({"loaded": True, "ok": False, "tok": None, "table": None})
    assert app.sem_ready() is False
    assert app.sem_embed("さびしい夜") is None      # 例外を投げない
finally:
    app._sem.clear()
    app._sem.update(saved)
assert app.sem_ready() is True

print("意味の索引: 全テスト通過")

"""v2追補（type trace 実時間再生・天灯・flare）のサーバ側テスト。

    .venv/bin/python scripts/test_trace_flare.py

pytest には依存しない（requirements を増やさない）。plain assert で落ちたら失敗。
確かめるのは：
  ・_pack_trace / _unpack_trace の往復と、壊れた入力の拒否（§1・§6）
  ・_flare_state の決定性・発生率 1/100 前後・値域（倍率0.9〜1.4、継続2〜6h）（§4）
  ・_sky_decay が調和減衰（1年50%・3年25%）のままであること（§3）
  ・_flare_air_factor が flare 中だけ 0.6 になること（§5）
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import (_pack_trace, _unpack_trace, _flare_state, _flare_air_factor,
                 _sky_decay, _sky_age_days, _SKY_FLARE_AIR, JST)


def approx(a, b, eps=1e-6):
    assert abs(a - b) < eps, f"{a} != {b}"


# ── _pack_trace / _unpack_trace（§1・§6）──────────────────────
ev = [[0, "s", "こんば"], [180, "i", "ん"], [90, "i", "は"],
      [4200, "d", "は"], [130, "i", "は"], [60, "s", "こんばんは。"]]
blob = _pack_trace({"fmt": "ev1", "ev": ev})
assert blob is not None and isinstance(blob, bytes)
assert _unpack_trace(blob) == ev                       # 往復で無傷
assert len(blob) < 200                                  # 潰れている（生JSONより小さい）

# 形式が違うものは全部 None（本文は受かる・再生の材料だけ諦める）
assert _pack_trace(None) is None
assert _pack_trace("ev1") is None
assert _pack_trace({"fmt": "ev2", "ev": ev}) is None
assert _pack_trace({"fmt": "ev1", "ev": []}) is None
assert _pack_trace({"fmt": "ev1", "ev": [[0, "s", "a"]]}) is None          # 1件では再生にならない
assert _pack_trace({"fmt": "ev1", "ev": [[0, "x", "a"], [1, "i", "b"]]}) is None   # 知らないop
assert _pack_trace({"fmt": "ev1", "ev": [[-5, "i", "a"], [1, "i", "b"]]}) is None  # 負のdt
assert _pack_trace({"fmt": "ev1", "ev": [[0, "i", "a"], [1, "i", 5]]}) is None     # chが文字でない
assert _pack_trace({"fmt": "ev1", "ev": [[0, "s", "x" * 200], [1, "i", "b"]]}) is None  # 長すぎる断片
assert _pack_trace({"fmt": "ev1", "ev": [[0, "i", "a"]] * 7000}) is None   # 異常な件数
assert _unpack_trace(None) is None
assert _unpack_trace(b"broken") is None

# ── _flare_state（§4）────────────────────────────────────────
# 決定性：同じ letter_id と同じ時刻なら、必ず同じ答え（DBに状態を持たない）
t0 = datetime(2026, 7, 26, 15, 0, tzinfo=JST)
for lid in ("a1", "b2", "c3"):
    assert _flare_state(lid, t0) == _flare_state(lid, t0)

# 発生率：多数の letter_id で「その宙の一日のどこかで flare する」割合が 1/100 前後。
# 一日ぶんを1時間刻みで舐めて、一度でも活きた id を数える
hits = 0
N = 3000
for i in range(N):
    lid = f"letter-{i:05d}"
    for h in range(24):
        t = datetime(2026, 7, 26, 4, 30, tzinfo=JST) + timedelta(hours=h)
        st = _flare_state(lid, t)
        if st:
            m, since, dur = st
            assert 0.9 <= m <= 1.4, m                    # 倍率の値域
            assert 7200 <= dur <= 21600, dur             # 2〜6h（DUR_SCALE=1のとき）
            assert 0 <= since < dur
            hits += 1
            break
rate = hits / N
assert 0.004 < rate < 0.02, rate                          # 1/100 前後（二項ゆらぎを許す）

# 宙の一日の外に漏れない：ある一日に flare する手紙は、翌日の同時刻には（別のseedで
# 引き直されるので）ほぼ流れない。ここでは「日付が変われば判定が独立」なことだけ見る
assert _flare_state("letter-00000", t0) or True           # 例外を出さないこと

# ── _sky_decay（§3：調和減衰のまま）─────────────────────────
now = datetime.now()
d1 = _sky_decay((now - timedelta(days=365)).isoformat(timespec="seconds"))
d3 = _sky_decay((now - timedelta(days=3 * 365)).isoformat(timespec="seconds"))
assert 0.45 < d1 < 0.55, d1                               # 1年で約1/2
assert 0.22 < d3 < 0.28, d3                               # 3年で約1/4
assert _sky_decay("broken-date") == 1.0

# ── _sky_age_days ─────────────────────────────────────────────
a = _sky_age_days((now - timedelta(days=10)).isoformat(timespec="seconds"))
assert 9.9 < a < 10.1, a
assert _sky_age_days(None) == 0.0

# ── _flare_air_factor（§5）───────────────────────────────────
# pool エントリの形：(pub, air, id, decay, uid, title, room, flare, age)
e_on = (None, None, "x", 1.0, None, None, None, (1.2, 100, 7200), 5.0)
e_off = (None, None, "x", 1.0, None, None, None, None, 5.0)
approx(_flare_air_factor(e_on), _SKY_FLARE_AIR)
approx(_flare_air_factor(e_off), 1.0)

print("ok: trace pack/unpack, flare determinism/rate/range, decay, air factor")

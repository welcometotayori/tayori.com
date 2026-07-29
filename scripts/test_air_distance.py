"""air_distance（v2仕様書 §2）の単体テスト。

    python3 scripts/test_air_distance.py

pytest には依存しない（requirements を増やさない）。plain assert で落ちたら失敗。
ここが v2 の心臓なので、重み・円環・欠測の正規化・無彩色の分岐を全部確かめる。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import (air_distance, _parse_hsl, _hue_distance, _hour_band,
                 _ring_distance, _AIR_SEASONS, _AIR_BANDS)


def approx(a, b, eps=1e-9):
    assert abs(a - b) < eps, f"{a} != {b}"


# ── _parse_hsl ────────────────────────────────────────────────
assert _parse_hsl("hsl(200, 60%, 50%)") == (200.0, 60.0, 50.0)
assert _parse_hsl(" hsl(370, 120%, 50%) ") == (10.0, 100.0, 50.0)  # 角度は環に丸め、S/Lは100で頭打ち
assert _parse_hsl("#aabbcc") is None
assert _parse_hsl(None) is None
assert _parse_hsl("") is None

# ── _hue_distance（§2.2）─────────────────────────────────────
approx(_hue_distance("hsl(0, 60%, 50%)", "hsl(0, 60%, 50%)"), 0.0)      # 同じ色は距離0
approx(_hue_distance("hsl(0, 60%, 50%)", "hsl(180, 60%, 50%)"), 0.70)   # 補色＝色相項が最大
approx(_hue_distance("hsl(350, 60%, 50%)", "hsl(10, 60%, 50%)"),        # 色相環は円（350↔10は20度）
       0.70 * (20 / 180))
approx(_hue_distance("hsl(0, 5%, 20%)", "hsl(180, 5%, 80%)"),           # 無彩色同士＝色相無視でS/Lのみ
       (0.15 * 0.0 + 0.15 * 0.6) / 0.30)
approx(_hue_distance("hsl(0, 5%, 50%)", "hsl(180, 60%, 50%)"),          # 片方だけ無彩色＝色相は中立0.5
       0.70 * 0.5 + 0.15 * 0.55 + 0.15 * 0.0)
assert _hue_distance("hsl(0, 60%, 50%)", None) is None                  # 欠測は None（成分ごと外す）

# ── 季節・時刻帯の円環（§2.3）────────────────────────────────
approx(_ring_distance(_AIR_SEASONS, "spring", "spring"), 0.0)
approx(_ring_distance(_AIR_SEASONS, "spring", "summer"), 0.5)
approx(_ring_distance(_AIR_SEASONS, "spring", "winter"), 0.5)   # 春↔冬は隣接
approx(_ring_distance(_AIR_SEASONS, "spring", "autumn"), 1.0)   # 対
approx(_ring_distance(_AIR_BANDS, "night", "morning"), 0.5)     # 夜↔朝は隣接
approx(_ring_distance(_AIR_BANDS, "morning", "evening"), 1.0)   # 対
assert _ring_distance(_AIR_SEASONS, "spring", None) is None

# ── 時刻帯の切り方 ───────────────────────────────────────────
assert _hour_band(4.0) == "morning"
assert _hour_band(10.99) == "morning"
assert _hour_band(11.0) == "day"
assert _hour_band(16.0) == "evening"
assert _hour_band(19.0) == "night"
assert _hour_band(3.5) == "night"    # 深夜は夜
assert _hour_band(None) is None

# ── 地名（§2.3）は 2026-07-29 に成分ごと外した（地図を畳んで位置を書く経路が消えた）。
#    "area" を渡しても air_distance は見ない＝結果が変わらないことを、下で確かめる。

# ── air_distance 全体（§2.1）────────────────────────────────
FULL_A = {"color": "hsl(220, 60%, 40%)", "season": "winter",
          "hour": 2.0, "weather": "snow"}

# 同じ空気は距離0
approx(air_distance(FULL_A, dict(FULL_A)), 0.0)

# 全成分が最遠のとき。色の距離は補色でも 0.70（S/L が同じ分は近い）なので、
# 全体は (0.421*0.70 + 0.211 + 0.211 + 0.157) / 1.0 = 0.8737。
FAR = {"color": "hsl(40, 60%, 40%)", "season": "summer",
       "hour": 13.0, "weather": "clear"}
approx(air_distance(FULL_A, FAR), 0.8737)

# 地名は見ない：渡しても渡さなくても同じ距離になる（成分を外した証拠）
approx(air_distance(dict(FULL_A, area="東京都新宿区"),
                    dict(FAR, area="沖縄県那覇市")),
       air_distance(FULL_A, FAR))

# 閲覧者の「いま」（色なし）→ 残り3成分（0.211+0.211+0.157）で正規化
NOW = {"season": "winter", "hour": 2.0, "weather": "snow"}
approx(air_distance(NOW, FULL_A), 0.0)
approx(air_distance(NOW, FAR), 1.0)
NOW_HALF = {"season": "spring", "hour": 2.0, "weather": "snow"}   # 季節だけ隣
approx(air_distance(NOW_HALF, FULL_A), (0.211 * 0.5) / 0.579)

# 対称性：a→b と b→a は同じ
approx(air_distance(FULL_A, FAR), air_distance(FAR, FULL_A))

# 何も比べられなければ中立0.5
approx(air_distance({}, FULL_A), 0.5)

# 色（約42%）が主役：色だけ遠い vs 天気だけ遠い なら色の方が遠い
COLOR_FAR = dict(FULL_A, color="hsl(40, 60%, 40%)")
WEATHER_FAR = dict(FULL_A, weather="clear")
assert air_distance(FULL_A, COLOR_FAR) > air_distance(FULL_A, WEATHER_FAR)

# 値域は常に 0.0–1.0
import itertools
opts_color = [None, "hsl(0, 60%, 50%)", "hsl(200, 5%, 90%)"]
opts_season = [None, "spring", "autumn"]
opts_hour = [None, 5.0, 22.0]
opts_weather = [None, "clear", "snow"]
for c, s, h, w in itertools.product(opts_color, opts_season, opts_hour,
                                    opts_weather):
    b = {"color": c, "season": s, "hour": h, "weather": w}
    d = air_distance(FULL_A, b)
    assert 0.0 <= d <= 1.0, (b, d)

# ── 意味の成分（2026-07-29 フェーズ3-2）─────────────────────────
# 【いちばん大事な確認】既定の漂い(drift)は、意味を渡しても結果が変わらないこと。
SEM_A = dict(FULL_A, sem_d=0.0)
SEM_B = dict(FULL_A, sem_d=1.0)
approx(air_distance(FULL_A, SEM_A), air_distance(FULL_A, FULL_A))
approx(air_distance(FULL_A, SEM_B), air_distance(FULL_A, FULL_A))
approx(air_distance(FULL_A, SEM_B, mode="drift"), air_distance(FULL_A, FULL_A))

# search のときだけ効く。取り分は _AIR_SEM_SHARE（既定0.35）
from app import _AIR_SEM_SHARE
approx(air_distance(FULL_A, SEM_A, mode="search"), 0.0)          # 空気も意味も同じ
approx(air_distance(FULL_A, SEM_B, mode="search"), _AIR_SEM_SHARE)  # 意味だけ最遠
# 空気が最遠・意味が最近 → 空気の取り分だけが残る
approx(air_distance(FULL_A, dict(FAR, sem_d=0.0), mode="search"),
       air_distance(FULL_A, FAR) * (1.0 - _AIR_SEM_SHARE))
# sem_d が無ければ search でも成分ごと外れる（写真/声だけのことば）
approx(air_distance(FULL_A, FAR, mode="search"), air_distance(FULL_A, FAR))
# 値域は search でも 0.0–1.0
for sd in (0.0, 0.25, 0.5, 0.75, 1.0):
    for b in (FULL_A, FAR, NOW, {}):
        d = air_distance(FULL_A, dict(b, sem_d=sd), mode="search")
        assert 0.0 <= d <= 1.0, (b, sd, d)

# ── sem_rank_distance（順位を 0〜1 に均す）──────────────────────
import numpy as np
from app import sem_rank_distance
def unit(x):
    v = np.array(x, dtype=np.float32); return v / np.linalg.norm(v)
q = unit([1, 0, 0])
vs = [unit([1, 0, 0]), unit([0.6, 0.8, 0]), unit([0, 1, 0]), None]
d = sem_rank_distance(q, vs)
approx(d[0], 0.0); approx(d[1], 0.5); approx(d[2], 1.0)
assert d[3] is None                       # ベクトルなし＝成分ごと外れる
assert sem_rank_distance(None, vs) == [None] * 4      # クエリが測れない時
assert sem_rank_distance(q, [None, None]) == [None, None]
approx(sem_rank_distance(q, [unit([1, 0, 0])])[0], 0.0)   # 1件だけなら最近

print("air_distance: 全テスト通過")

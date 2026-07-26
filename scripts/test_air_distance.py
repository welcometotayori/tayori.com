"""air_distance（v2仕様書 §2）の単体テスト。

    python3 scripts/test_air_distance.py

pytest には依存しない（requirements を増やさない）。plain assert で落ちたら失敗。
ここが v2 の心臓なので、重み・円環・欠測の正規化・無彩色の分岐を全部確かめる。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import (air_distance, _parse_hsl, _hue_distance, _hour_band,
                 _ring_distance, _area_distance, _AIR_SEASONS, _AIR_BANDS)


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

# ── 地名（§2.3）──────────────────────────────────────────────
approx(_area_distance("東京都新宿区", "東京都新宿区"), 0.0)
approx(_area_distance("東京都新宿区", "東京都台東区"), 0.5)   # 同一都道府県
approx(_area_distance("東京都新宿区", "大阪府北区"), 1.0)
approx(_area_distance("北海道札幌市", "北海道函館市"), 0.5)
assert _area_distance(None, "東京都新宿区") is None

# ── air_distance 全体（§2.1）────────────────────────────────
FULL_A = {"color": "hsl(220, 60%, 40%)", "season": "winter",
          "hour": 2.0, "weather": "snow", "area": "東京都新宿区"}

# 同じ空気は距離0
approx(air_distance(FULL_A, dict(FULL_A)), 0.0)

# 全成分が最遠のとき。色の距離は補色でも 0.70（S/L が同じ分は近い）なので、
# 全体は 0.40*0.70 + 0.20 + 0.20 + 0.15 + 0.05 = 0.88 が上限近く。
FAR = {"color": "hsl(40, 60%, 40%)", "season": "summer",
       "hour": 13.0, "weather": "clear", "area": "沖縄県那覇市"}
approx(air_distance(FULL_A, FAR), 0.88)

# 閲覧者の「いま」（色・地名なし）→ 残り3成分（0.20+0.20+0.15）で正規化
NOW = {"season": "winter", "hour": 2.0, "weather": "snow"}
approx(air_distance(NOW, FULL_A), 0.0)
approx(air_distance(NOW, FAR), 1.0)
NOW_HALF = {"season": "spring", "hour": 2.0, "weather": "snow"}   # 季節だけ隣
approx(air_distance(NOW_HALF, FULL_A), (0.20 * 0.5) / 0.55)

# 対称性：a→b と b→a は同じ
approx(air_distance(FULL_A, FAR), air_distance(FAR, FULL_A))

# 何も比べられなければ中立0.5
approx(air_distance({}, FULL_A), 0.5)

# 色（40%）が主役：色だけ遠い vs 地名だけ遠い なら色の方が遠い
COLOR_FAR = dict(FULL_A, color="hsl(40, 60%, 40%)")
AREA_FAR = dict(FULL_A, area="沖縄県那覇市")
assert air_distance(FULL_A, COLOR_FAR) > air_distance(FULL_A, AREA_FAR)

# 値域は常に 0.0–1.0
import itertools
opts_color = [None, "hsl(0, 60%, 50%)", "hsl(200, 5%, 90%)"]
opts_season = [None, "spring", "autumn"]
opts_hour = [None, 5.0, 22.0]
opts_weather = [None, "clear", "snow"]
opts_area = [None, "東京都新宿区", "大阪府北区"]
for c, s, h, w, ar in itertools.product(opts_color, opts_season, opts_hour,
                                        opts_weather, opts_area):
    b = {"color": c, "season": s, "hour": h, "weather": w, "area": ar}
    d = air_distance(FULL_A, b)
    assert 0.0 <= d <= 1.0, (b, d)

print("air_distance: 全テスト通過")

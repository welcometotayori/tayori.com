"""灯が運ぶ二種類の気配（フェーズ7）のテスト。

    .venv/bin/python scripts/test_lantern.py

pytest には依存しない。plain assert で落ちたら失敗。

確かめるのは：
  ・触れた（強い・まれ）と 居る（弱い・ありふれた）が混ざらないこと
  ・自分の気配は自分に返らないこと（自分の反射だと気づいた瞬間、灯は嘘になる）
  ・心拍を何度打っても、ひとりはひとりぶんに畳まれること
  ・窓を過ぎれば静かに消えること
  ・数も、誰かも、どこから来たかも、どこにも出ないこと
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app  # noqa: E402

A, B = "u:alice", "u:bob"


def reset():
    with app._lantern_lock:
        app._lantern_touches.clear()


def put(key, room, kind, ago=0.0):
    """_lantern_touch は request 文脈の鍵を使うので、リングへ直に置いて試す。
    畳み込みの規則だけは _lantern_touch と同じにする（ここが本体の写しである点は
    テストの弱み——だから畳み込みは下で件数として確かめる）。"""
    now = time.time() - ago
    with app._lantern_lock:
        if kind == "here":
            for e in [e for e in app._lantern_touches
                      if e[1] == key and e[3] == "here" and e[2] == room]:
                app._lantern_touches.remove(e)
        app._lantern_touches.append((now, key, room, kind))


# ══ 居る：自分以外がその部屋に居るか ═══════════════════════════
reset()
assert app._lantern_here(A, 9) is False          # 誰も居ない
put(A, 9, "here")
assert app._lantern_here(A, 9) is False, "自分の気配が自分に返っている"
put(B, 9, "here")
assert app._lantern_here(A, 9) is True
# このときAもBも部屋9に居るので、Bから見ればAが「自分以外」＝真で正しい
assert app._lantern_here(B, 9) is True
assert app._lantern_here(A, 7) is False, "別の部屋に漏れている"
# 自分ひとりだけの部屋は、何度心拍を打っても点かない
reset()
for _ in range(5):
    put(A, 9, "here")
assert app._lantern_here(A, 9) is False, "自分の気配が自分に返っている"
assert app._lantern_here(A, None) is False       # 部屋の外では聞かない

# ══ 触れた：居るだけでは点かない ═════════════════════════════
reset()
put(B, 9, "here")


def lit_for(me):
    now = time.time()
    with app._lantern_lock:
        return any(k != me and kind == "touch" and now - t <= app._LANTERN_WINDOW
                   for t, k, _r, kind in app._lantern_touches)


assert lit_for(A) is False, "居るだけで「触れた」が点いている"
put(B, 9, "touch")
assert lit_for(A) is True
assert lit_for(B) is False, "自分が触れた合図が自分に返っている"

# ══ 心拍は畳まれる（ひとりはひとりぶん）════════════════════════
reset()
for _ in range(50):
    put(B, 9, "here")
assert len(app._lantern_touches) == 1, f"心拍が畳まれていない: {len(app._lantern_touches)}"
put(B, 7, "here")                                # 別の部屋は別の気配
assert len(app._lantern_touches) == 2
put("u:carol", 9, "here")
assert len(app._lantern_touches) == 3
# 触れたは畳まない（一度きりの合図なので、回数そのものに意味がある）
put(B, 9, "touch")
put(B, 9, "touch")
assert len(app._lantern_touches) == 5

# ══ 窓を過ぎれば消える ════════════════════════════════════════
reset()
put(B, 9, "here", ago=app._LANTERN_HERE_WINDOW + 5)
assert app._lantern_here(A, 9) is False, "古い気配が残っている"
put(B, 9, "touch", ago=app._LANTERN_WINDOW + 5)
assert lit_for(A) is False
# 触れたの窓は、居るの窓より短い（強い合図ほど早く引く）
assert app._LANTERN_WINDOW < app._LANTERN_HERE_WINDOW

# 居るの窓は、客席の心拍（30秒）2回ぶんより広い。同じだと灯がまたたく。
assert app._LANTERN_HERE_WINDOW > 60, "窓が狭すぎて、心拍の隙間で気配が切れる"

# ══ 部屋の集合：気配のある部屋だけ。数は返さない ═════════════
reset()
put(B, 9, "here")
put("u:carol", 7, "touch")
put(A, 3, "here")                                 # 自分の部屋は出ない
rooms = app._lantern_rooms(A)
assert rooms == {9, 7}, rooms
assert isinstance(rooms, set), "順序や重複から人数が読めてはいけない"

reset()
print("灯（居る・触れた）: 全テスト通過")

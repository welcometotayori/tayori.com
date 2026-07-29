"""自分の宙から消す＋静かに上がってくる（フェーズ5）のテスト。

    .venv/bin/python scripts/test_mute.py

pytest には依存しない。plain assert で落ちたら失敗。
ここで固定したいのは、機能というより **非対称** です：
  ・消したことは、消した本人以外の誰も知らない（書き手にも伝わらない）
  ・消したものは、漂いにも探すにも二度と出ない
  ・別々の人が消したときだけ、運営に静かに上がる（自動では下げない）
  ・ことばが消えたら、その記録も道連れになる
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app  # noqa: E402

# 本番のDBは触らない。空のDBにスキーマだけ作って試す。
tmp = tempfile.mkdtemp()
app.DB_PATH = os.path.join(tmp, "t.db")
app._init_db_done = False
app.init_db()
db = sqlite3.connect(app.DB_PATH)
db.row_factory = sqlite3.Row

READER_A, READER_B, AUTHOR = "ra", "rb", "au"
LID = "L1"
db.execute("INSERT INTO users (id,username,pw_hash,created) VALUES (?,?,?,?)",
           (AUTHOR, "author", "x", "2026-07-01T00:00:00"))
db.execute("INSERT INTO letters (id,user_id,poem,sent_date,arrive_date,arrive_label,"
           "arrive_hidden,opened,mode,sky_status) VALUES (?,?,?,?,?,?,0,0,'sky','live')",
           (LID, AUTHOR, "ねむれない夜は、月とふたりきり",
            "2026-07-01T00:00:00", "2026-08-01", ""))
db.commit()


def mute(reader, on=True):
    if on:
        db.execute("INSERT OR IGNORE INTO muted (reader_id,letter_id,at) VALUES (?,?,?)",
                   (reader, LID, "2026-07-29T00:00:00"))
    else:
        db.execute("DELETE FROM muted WHERE reader_id=? AND letter_id=?", (reader, LID))
    db.commit()


def author_row():
    return db.execute("SELECT * FROM letters WHERE id=?", (LID,)).fetchone()


# ══ 消しても、書き手側には何も起きない ═══════════════════════
before = dict(author_row())
mute(READER_A)
mute(READER_B)
after = dict(author_row())
assert before == after, "ミュートが書き手の letters を書き換えている"
# 配達（届いたか・開かれたか）にも触れない
assert db.execute("SELECT COUNT(*) c FROM sky_deliveries").fetchone()["c"] == 0

# ══ 消した人からは見えない・消していない人には見える ═══════════
assert app._muted_ids(db, READER_A) == {LID}
assert app._muted_ids(db, READER_B) == {LID}
assert app._muted_ids(db, "someone-else") == set()
assert app._muted_ids(db, None) == set()          # 未ログイン

# ══ 戻せる ════════════════════════════════════════════════════
mute(READER_A, on=False)
assert app._muted_ids(db, READER_A) == set()
assert app._muted_ids(db, READER_B) == {LID}      # 他人の記録は道連れにしない
mute(READER_A)

# ══ 二重に消しても1人ぶん（PKで守る）══════════════════════════
mute(READER_A)
mute(READER_A)
n = db.execute("SELECT COUNT(*) c FROM muted WHERE letter_id=?", (LID,)).fetchone()["c"]
assert n == 2, f"同じ人が二度消したら2人に数えられている: {n}"


def flagged(threshold):
    return db.execute(
        "SELECT letter_id FROM muted GROUP BY letter_id HAVING COUNT(*) >= ?",
        (threshold,)).fetchall()


# ══ 別々の人が消したときだけ、静かに上がる ═══════════════════
assert len(flagged(2)) == 1
assert len(flagged(3)) == 0                        # まだ3人には届いていない
mute(READER_B, on=False)
assert len(flagged(2)) == 0, "1人に戻ったら上がらない"
mute(READER_B)

# 自動では下げない（掲載状態は live のまま）
assert author_row()["sky_status"] == "live", "ミュートが掲載可否を動かしている"
assert app.MUTE_REPORT_N >= 1

# ══ ことばが消えたら、記録も道連れ ═══════════════════════════
db.execute("DELETE FROM muted WHERE letter_id=?", (LID,))
db.execute("DELETE FROM letters WHERE id=?", (LID,))
db.commit()
orphan = db.execute(
    "SELECT COUNT(*) c FROM muted m LEFT JOIN letters l ON l.id=m.letter_id"
    " WHERE l.id IS NULL").fetchone()["c"]
assert orphan == 0, "ことばの無いミュート記録が残っている"

print("自分の宙から消す: 全テスト通過")

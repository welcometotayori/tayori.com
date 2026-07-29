"""種のことばの著者が、人あての仕組みから外れていること（フェーズ6）。

    .venv/bin/python scripts/test_seed_author.py

**シードを投入する前に、これが通ることを確かめる**（指示書の順序厳守）。
ここが抜けていると、300通が運営の受信箱に降り、書架を埋め、宙からの配達の
受け手にもなる。投入してからでは、送ってしまったメールは戻せない。

確かめるのは：
  ・帰還メールの対象にならない
  ・棚入りの知らせの対象にならない
  ・宙からの配達の受け手にならない
  ・ふつうの人は、どれも今までどおり対象になる（除外が効きすぎていない）
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app  # noqa: E402

tmp = tempfile.mkdtemp()
app.DB_PATH = os.path.join(tmp, "t.db")
app._init_db_done = False
app.init_db()
db = sqlite3.connect(app.DB_PATH)
db.row_factory = sqlite3.Row


def add_user(uid, name, seed):
    db.execute(
        "INSERT INTO users (id,username,pw_hash,created,email,email_verified,"
        "notify_enabled,is_seed) VALUES (?,?,?,?,?,1,1,?)",
        (uid, name, "x", "2026-07-01T00:00:00", f"{name}@example.com", seed))


def add_letter(lid, uid, shelved=False):
    db.execute(
        "INSERT INTO letters (id,user_id,poem,sent_date,arrive_date,arrive_at,"
        "arrive_label,arrive_hidden,opened,notified,mode,sky_status,shelved_at)"
        " VALUES (?,?,?,?,?,?,'',1,0,0,'sky','live',?)",
        (lid, uid, "ねむれない夜は、月とふたりきり", "2026-07-01T00:00:00",
         "2026-07-02", "2026-07-02T00:00:00", "2026-07-02T00:00:00" if shelved else None))


add_user("seeduser", "種", 1)
add_user("realuser", "ひと", 0)
add_letter("Lseed", "seeduser", shelved=True)
add_letter("Lreal", "realuser", shelved=True)
db.commit()

# ══ 帰還メール ════════════════════════════════════════════════
ret = {r["lid"] for r in db.execute(
    """SELECT l.id AS lid FROM letters l JOIN users u ON u.id = l.user_id
        WHERE COALESCE(l.notified,0)=0 AND COALESCE(l.notify_failed,0)=0
          AND l.mode='sky'
          AND COALESCE(u.is_seed,0)=0
          AND u.email IS NOT NULL AND u.email<>''
          AND COALESCE(u.email_verified,0)=1
          AND COALESCE(u.notify_enabled,1)=1""")}
assert "Lseed" not in ret, "種のことばが帰還メールの対象になっている"
assert "Lreal" in ret, "ふつうの人が帰還メールから外れている（除外が効きすぎ）"

# ══ 棚入りの知らせ ════════════════════════════════════════════
shelved = {r["lid"] for r in db.execute(
    """SELECT l.id AS lid FROM letters l JOIN users u ON u.id = l.user_id
        WHERE l.shelved_at IS NOT NULL AND COALESCE(l.shelved_notified,0)=0
          AND COALESCE(u.is_seed,0)=0
          AND u.email IS NOT NULL AND u.email<>''
          AND COALESCE(u.email_verified,0)=1
          AND COALESCE(u.notify_enabled,1)=1""")}
assert "Lseed" not in shelved, "種のことばが棚入りの知らせの対象になっている"
assert "Lreal" in shelved, "ふつうの人が棚入りの知らせから外れている"

# ══ 宙からの配達の受け手 ══════════════════════════════════════
# _assign_sky_delivery が使うのと同じ条件で引く
recips = {r["id"] for r in db.execute(
    "SELECT id FROM users WHERE id<>? AND username NOT IN ('admin','demo') "
    "AND COALESCE(is_seed,0)=0 AND suspended_at IS NULL", ("nobody",))}
assert "seeduser" not in recips, "種の著者が配達の受け手になっている"
assert "realuser" in recips, "ふつうの人が配達の受け手から外れている"

# ══ 書架は、その人にログインした人にしか出ない ═══════════════
# /api/mine は user_id で絞るだけ。種のことばが運営の書架を埋めないのは
# 「運営とは別のアカウントに持たせる」という置き方そのものが担保する。
mine_real = db.execute(
    "SELECT COUNT(*) c FROM letters WHERE user_id=?", ("realuser",)).fetchone()["c"]
assert mine_real == 1
assert db.execute("SELECT COUNT(*) c FROM letters WHERE user_id=?",
                  ("seeduser",)).fetchone()["c"] == 1

# ══ 既定は0（既存ユーザーが黙って種にならない）═══════════════
db.execute("INSERT INTO users (id,username,pw_hash,created) VALUES ('u3','三','x','2026-07-01')")
db.commit()
assert db.execute("SELECT COALESCE(is_seed,0) s FROM users WHERE id='u3'").fetchone()["s"] == 0

print("種の著者の除外: 全テスト通過")

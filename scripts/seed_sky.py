# -*- coding: utf-8 -*-
"""種のことばを宙へ置く（フェーズ6・冪等）。

    python3 scripts/seed_sky.py            # 下見（何も書かない）
    python3 scripts/seed_sky.py --apply    # 実行
    python3 scripts/seed_sky.py --remove   # 取り消し（置いたものだけ消す）

読むもの:
    seed/tayori_seed_300.csv     本文とメタ（部屋・季節・時刻帯・天気・色相・トーン・題）
    seed/tayori_seed_traces.csv  no と筆跡（base64）の対応表

【なぜ「放つ」フローを通さないのか】
放つフローは、いまの時刻・いまの天気・いまの部屋で書く人のためのもの。種のことばは
一年ぶんの過去に散らばっていて、書く人がいない。created_at を現在時刻で上書きしない
ことが要なので、投函APIではなくここから直接入れる。

【それでも通すもの】
・掲載の門番（_moderate）… 種だから素通り、はやらない。同じ門をくぐらせる
・意味の索引（sem_store）… 探すの対象になる。ここを飛ばすと種だけ探せない宙になる
・80字の上限 … 超えたら切らずに落とす（切ると、書いた人のいない文が生まれる）

【やらないこと】
・_assign_sky_delivery を呼ばない。300通が実在の人の受信の棚へ降ると、宙ではなく
  配布物になる。種のことばは、漂いと探しの中でだけ出会う。
・帰還メールを立てない（notified=1）。著者は人ではないので、帰る先がない。

冪等性は letters.id で担保する。id は CSV の no から決まる（seed:<no> のハッシュ）ので、
二度走らせても同じ行に当たって INSERT OR IGNORE が効く。筆跡の乱数も種が固定なので、
作り直しても同じものが出る。
"""
import base64
import csv
import hashlib
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import (DB_PATH, _connect, init_db, _moderate, _hour_band,  # noqa: E402
                 sem_ready, sem_store, sem_forget, _WRITE_LOCK, _sky_cache_bust)

HERE = os.path.dirname(os.path.abspath(__file__))
SEED_DIR = os.path.join(HERE, "..", "seed")
GRID = os.path.join(SEED_DIR, "tayori_seed_300.csv")
TRACES = os.path.join(SEED_DIR, "tayori_seed_traces.csv")

SEED_USERNAME = "種"          # 人ではない。is_seed=1 で人あての仕組みから全部外れる
POEM_MAX = 80
WEATHER = {"晴": "clear", "曇": "cloud", "雨": "rain", "雪": "snow"}


def letter_id(no):
    """CSV の no から決まる id。二度走らせても同じ行に当たる＝冪等の要。"""
    return "seed" + hashlib.sha256(f"tayori-seed:{no}".encode()).hexdigest()[:12]


def tone_color(hue, tone):
    """色相＋トーン → hsl(H,S%,L%)。

    画面の色帯（mood.html の toneColor）と同じ式にする。ここがずれると、
    種のことばだけ、人が選べない色を持つことになる。
      p<0.5 : 淡い→鮮やか   s 24→78 / l 88→64
      p>=0.5: 鮮やか→深い   s 78→66 / l 64→46
    """
    p = max(0.0, min(1.0, tone / 100.0))
    if p < 0.5:
        k = p / 0.5
        s, l = 24 + (78 - 24) * k, 88 + (64 - 88) * k
    else:
        k = (p - 0.5) / 0.5
        s, l = 78 + (66 - 78) * k, 64 + (46 - 64) * k
    return f"hsl({int(hue) % 360}, {round(s)}%, {round(l)}%)"


def parse_dt(raw):
    for f in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(raw.strip(), f)
        except ValueError:
            pass
    return None


def load():
    rows = list(csv.DictReader(open(GRID, encoding="utf-8-sig")))
    traces = {r["no"]: r["trace_z_base64"]
              for r in csv.DictReader(open(TRACES, encoding="utf-8-sig"))}
    out, dropped = [], []
    for r in rows:
        no = r["no"]
        poem = (r["本文"] or "").strip()
        dt = parse_dt(r["created_at"])
        if not poem:
            dropped.append((no, "本文が空")); continue
        if len(poem) > POEM_MAX:
            dropped.append((no, f"{len(poem)}字（上限{POEM_MAX}）")); continue
        if dt is None:
            dropped.append((no, f"日時が読めない: {r['created_at']}")); continue
        b64 = traces.get(no) or ""
        try:
            trace_z = base64.b64decode(b64) if b64 else None
        except Exception:
            dropped.append((no, "筆跡が壊れている")); continue
        out.append({
            "no": no, "id": letter_id(no), "poem": poem,
            "title": (r["題"] or "").strip()[:10] or None,
            "room": (r["部屋"] or "").strip(),
            "sent": dt.isoformat(timespec="seconds"),
            "bucket": _hour_band(dt.hour + dt.minute / 60.0),
            "weather": WEATHER.get((r["天気"] or "").strip()),
            "color": tone_color(int(r["色相"]), int(r["トーン"])),
            "trace_z": trace_z,
        })
    return out, dropped


def main():
    apply = "--apply" in sys.argv
    remove = "--remove" in sys.argv
    init_db()
    db = _connect()
    try:
        if remove:
            n = db.execute(
                "SELECT COUNT(*) c FROM letters WHERE id LIKE 'seed%'").fetchone()["c"]
            print(f"DB: {DB_PATH}\n置かれている種のことば: {n} 通")
            if not apply:
                print("\n下見です。実際に消すには --apply も付けてください。")
                return 0
            ids = [r["id"] for r in db.execute(
                "SELECT id FROM letters WHERE id LIKE 'seed%'")]
            with _WRITE_LOCK:
                sem_forget(db, ids)
                for lid in ids:
                    db.execute("DELETE FROM muted WHERE letter_id=?", (lid,))
                    db.execute("DELETE FROM sky_seen WHERE letter_id=?", (lid,))
                    db.execute("DELETE FROM sky_cycle_seen WHERE letter_id=?", (lid,))
                db.execute("DELETE FROM letters WHERE id LIKE 'seed%'")
                db.commit()
            _sky_cache_bust()
            print(f"{len(ids)} 通を取り下げました（種のアカウントは残します）。")
            return 0

        rows, dropped = load()
        rooms = {r["name"]: r["id"] for r in db.execute(
            "SELECT id, name FROM rooms WHERE deleted_at IS NULL")}
        missing = sorted({r["room"] for r in rows if r["room"] not in rooms})
        already = {r["id"] for r in db.execute(
            "SELECT id FROM letters WHERE id LIKE 'seed%'")}
        todo = [r for r in rows if r["id"] not in already]

        print(f"DB: {DB_PATH}")
        print(f"読めた: {len(rows)} 通 / 置き済み: {len(already)} / これから: {len(todo)}")
        if dropped:
            print(f"落とした（切らずに落とす）: {len(dropped)} 通")
            for no, why in dropped:
                print(f"   no.{no}  {why}")
        if missing:
            print(f"\n無い部屋: {missing}")
            print("先に部屋を用意してください（既定14室は起動時に自動で作られます）。")
            return 1
        if not sem_ready():
            print("\n語ベクトル表が読めません。意味の索引なしで置くと、種のことばだけ"
                  "探せない宙になります。先に semantic/ を用意してください。")
            return 1
        if not todo:
            print("\n置くものはありません（このスクリプトは冪等です）。")
            return 0

        # 門番を通す（種だから素通り、はやらない）
        gate = {}
        for r in todo:
            gate[r["id"]] = _moderate(r["poem"])[0]
        held = [r for r in todo if gate[r["id"]] != "live"]
        if held:
            print(f"\n門番が止めたもの: {len(held)} 通（宙には出ません）")
            for r in held:
                print(f"   {gate[r['id']]}  no.{r['no']}  {r['poem'][:30]}")

        if not apply:
            print("\n下見です。実行するには --apply を付けてください。")
            return 0

        now = datetime.now().isoformat(timespec="seconds")
        made = 0
        with _WRITE_LOCK:
            u = db.execute("SELECT id FROM users WHERE username=?",
                           (SEED_USERNAME,)).fetchone()
            if u:
                author = u["id"]
                db.execute("UPDATE users SET is_seed=1 WHERE id=?", (author,))
            else:
                author = "seedauthor" + hashlib.sha256(b"tayori-seed-author").hexdigest()[:6]
                # パスワードは入れない（ログインできない＝人が使うアカウントではない）。
                # メールも持たない＝帰還メールの条件で二重に落ちる。
                db.execute(
                    "INSERT INTO users (id,username,pw_hash,created,is_seed,notify_enabled)"
                    " VALUES (?,?,'',?,1,0)", (author, SEED_USERNAME, now))
            for r in todo:
                seal_env = ('{"condition": "%s"}' % r["weather"]) if r["weather"] else None
                db.execute(
                    """INSERT OR IGNORE INTO letters
                       (id,user_id,poem,title,sent_date,arrive_date,arrive_at,arrive_label,
                        arrive_hidden,opened,notified,emos,from_reply,seal_env,trace_z,
                        seal_color,seal_color_chosen,time_bucket,vertical,mode,sky_status,room_id)
                       VALUES (?,?,?,?,?,?,?,'',1,0,1,'[]',0,?,?,?,1,?,1,'sky',?,?)""",
                    (r["id"], author, r["poem"], r["title"], r["sent"],
                     r["sent"][:10], r["sent"], seal_env, r["trace_z"],
                     r["color"], r["bucket"], gate[r["id"]], rooms[r["room"]]))
                sem_store(db, r["id"], r["poem"])
                made += 1
            db.commit()
        _sky_cache_bust()
        print(f"\n{made} 通を宙へ置きました（著者は「{SEED_USERNAME}」・is_seed=1）。")
        print("取り消すには: python3 scripts/seed_sky.py --remove --apply")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

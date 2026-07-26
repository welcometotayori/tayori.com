# -*- coding: utf-8 -*-
"""空っぽの部屋に、ことばを 3〜8 ずつ置く（2026-07-26）。

部屋の仕組み（B-7/B-8）を入れた直後は、どの部屋も静かなままで
「漂う」も「辿る」も見て確かめられない。この本は、その最初のことばを置くためのもの。

作法（ここを崩さないこと）
  ・本文は 80字以内。渡された詩片が超えていたら**切らずに置かない**
    （切ることは書いたものを損なうこと。落としたものは実行時に名前を出す）
  ・掲載の門番（_moderate）と ケアの判定（_needs_care）は**必ず通す**。
    引っかかったことばは宙に出さない——素通りさせる抜け道をここに作らない。
  ・sky_deliveries は作らない。置いたことばは漂うだけで、
    だれかの「ことばが、降りてきました」にはならない（本物の縁だけがそれをする）。
  ・notified=1。帰還メールは飛ばさない。
  ・作者は一人に寄せる。/api/sky は「自分のことば」を読み手から外すので、
    実在の人のアカウントで置くと、その人の宙にだけ映らなくなる。

使い方:
    python scripts/seed_room_words.py --as demo            # 置く
    python scripts/seed_room_words.py --as demo --dry-run  # 何がどこへ行くか見るだけ
本番（Render）では Shell から同じコマンド。DBの場所は app.py の解決をそのまま使う。
"""
import argparse
import json
import os
import random
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import (DB_PATH, init_db, _moderate, _needs_care,  # noqa: E402
                 _mood_words_from_poem, _sky_arrive_at)

POEM_MAX = 80   # 固定仕様。クライアントの maxlength と対（[[tayori-v22-policy]]）
TITLE_MAX = 10

# ── 色 ──────────────────────────────────────────────────────
# 気分の色は書き手ひとりひとりのものなので、部屋ごとに色を揃えない
# （揃えると「部屋の色」という凡例が生まれる。宙に凡例は置かない）。
# 一つずつ違う色にするために、色相を **黄金角（137.5°）ずつ回す**。
# 乱数で振ると必ずどこかで近い色が隣り合うが、この回し方なら円周上に
# いちばん均等に散る＝同じ部屋の中でも、隣の部屋との間でも、色がかぶらない。
GOLDEN = 137.508

# トーン（淡い⇄鮮やか⇄深い）も一つずつ変える。こちらは別の無理数で回して、
# 色相との組み合わせが周期的に繰り返さないようにする。
TONE_STEP = 0.6180339887
TONE_LO, TONE_HI = 0.22, 0.72   # 宙の闇で読める範囲（明度46〜88%に収まる）


def tone_color(h, p):
    """書く柱の toneColor と同じ式（淡い→鮮やか→深い）。宙の闇でも読める明度に収める。"""
    if p < 0.5:
        k = p / 0.5
        s = 24 + (78 - 24) * k
        l = 88 + (64 - 88) * k
    else:
        k = (p - 0.5) / 0.5
        s = 78 + (66 - 78) * k
        l = 64 + (46 - 64) * k
    return "hsl(%d, %d%%, %d%%)" % (h % 360, round(s), round(l))


def word_color(i, rnd):
    """i 番目のことばの色。黄金角で回すので、どの二つも同じ色にならない。
    揺らぎは色相に ±6 だけ（大きくすると均等さが崩れて、近い色が隣り合いはじめる）。"""
    h = (i * GOLDEN + rnd.uniform(-6, 6)) % 360
    p = TONE_LO + (TONE_HI - TONE_LO) * ((i * TONE_STEP) % 1.0)
    return tone_color(h, p)


# ── 置くことば ────────────────────────────────────────────────
# (部屋, 題 or None, 本文)。題は10字以内・任意（無題のまま漂うほうが多い）。
# Kosei から渡された詩片は、そのままの字で置く（表記も改行も変えない）。
# 渡された分で 3 に届かない部屋には、同じ声で書き足した。
WORDS = [
    # ── 恋愛 ──
    ("恋愛", None, "伝えたくて、また言葉選び"),
    ("恋愛", None, "僕が死んだら\nここに来て泣いてくれる？\n答えの代わりに\n涙を浮かべたふたつの目を　\n僕に向けた"),
    ("恋愛", "春", "もう一度だけ　愛して\nもう一度だけ　罪を犯し\nもう一度だけ　赦しを得よう\nそして春だ"),
    ("恋愛", "東京", "いたるところで同じ映画をやっている\nその東京でもういちど会えたなら"),
    ("恋愛", None, "愛はカルト\n一生モノの思い出に\nプラモデルみたいに触ってほしい"),

    # ── 家族 ──
    ("家族", None, "大人は泣かないと思ってた"),
    ("家族", None, "母の鼻歌の調子で、その日の家の天気がわかる"),
    ("家族", "四段目", "実家の階段は四段目が鳴る。夜中に帰ると、必ずそこで見つかる"),
    ("家族", None, "電話を切ったあと、まだ何か言いたかった気がして、しばらく画面を見ている"),

    # ── 友達 ──
    ("友達", None, "生きてくれればそれでいいよと言った\n本当はそうじゃないけど\nそう言いました\nそう言ってしまいました"),
    ("友達", "二駅", "駅で別れてから、言いそこねたことを二駅ぶん考える"),
    ("友達", None, "久しぶりに会って、変わってないねと言い合って、それで終わった"),
    ("友達", None, "三人でいると、いつも誰かがすこし黙っている"),
    ("友達", None, "返信を打って、消して、けっきょくスタンプだけ送った"),

    # ── 学校 ──
    ("学校", None, "儚ければ儚いほど完璧な青春だ"),
    ("学校", None, "青春至上主義"),
    ("学校", "四時間目", "四時間目の窓、外の音のほうが大きかった"),
    ("学校", None, "体育館の床の冷たさだけ、いまでも足の裏にある"),
    ("学校", None, "卒業式のあと、誰の名前も呼ばずに帰った"),

    # ── 仕事 ──
    ("仕事", None, "充実はしていても納得はしてない"),
    ("仕事", None, "安物の缶チューハイ\n胸の中の弱虫を殺せぬまま\nため息は深く"),
    ("仕事", None, "会議のあいだ、窓の桟の埃をずっと見ていた"),
    ("仕事", None, "よろしくお願いしますと打つ手が、いつも少しだけ止まる"),
    ("仕事", "終電", "終電で、その日いちばん静かな時間が来る"),

    # ── お金 ──
    ("お金", None, "レジで小銭を数える手が、いつのまにか速くなった"),
    ("お金", None, "安いほうを選んだ理由を、あとで自分に説明している"),
    ("お金", None, "残高を見る前に、すこし息を吸う"),
    ("お金", None, "贅沢と呼ぶ額が、年ごとに小さくなっていく"),

    # ── 生活 ──
    ("生活", "お皿", "毎日をお皿のように積み重ねて\n割らないように工夫してる"),
    ("生活", None, "洗濯物のにおいで、きょうが晴れだったと知る"),
    ("生活", None, "米を研ぐ音がいちばん、自分の家の音だ"),
    ("生活", None, "ゴミの日を数えて、一週間の速さを知る"),

    # ── 人生 ──
    ("人生", None, "ここには何かありそうだから\n帰れない\nまだ自分のこと諦めたくない"),
    ("人生", None, "時間が砂みたいに流れていく"),
    ("人生", None, "大人になれない私たち"),
    ("人生", "星", "どんな星も太陽には勝てない\n星を光らせるための星"),
    ("人生", "籠", "籠の中の鳥は\n大空に憧れを抱いて生きているのだろうか\nそれとも空があることすら気がつかず死ぬのだろうか"),
    ("人生", None, "希望的観測は人が生きていくための必需品"),
    ("人生", None, "自らの\n影だけを頼りに\n道渡る"),

    # ── 心 ──
    ("心", None, "本心とはいつも不協和音"),
    ("心", None, "泣いた後みたいな笑い方になっていた、雨が、こう、線になっていた"),
    ("心", None, "生きて負う苦をキミは疑う"),
    ("心", "渡り鳥", "発された言葉が渡り鳥にしては遅すぎて気が狂いそうだよ"),
    ("心", None, "命の不始末"),
    ("心", None, "被傷性について"),

    # ── 世界 ──
    ("世界", None, "夏には、影さえも青く染まる\n\nそれもきっとしあわせ"),
    ("世界", None, "自由の奴隷"),
    ("世界", None, "どこにでもいるバカが力を持ってしまった、、"),
    ("世界", "絶唱", "夜に枯れる朝顔\nすごく世界っぽいこの世界で\n絶唱してみせて"),
    ("世界", None, "運命は流体　街を巡ってときどき夏の頬を濡らす"),
    ("世界", "花壇", "ブルーシートにその花壇は覆われていて\n何か悪いことをしたかのように"),
    ("世界", "この青", "この青は\n夏のすべてを\n許しおり"),

    # ── 音楽 ──
    ("音楽", None, "すべての芸術は音楽の状態に憧れる"),
    ("音楽", None, "全ての芸術は音楽に嫉妬する"),
    ("音楽", "ワンシーン", "音は失われた映画のワンシーン\n大袈裟に動いてみようか"),

    # ── アート ──
    ("アート", None, "身体を貫くようなまばゆい閃光"),
    ("アート", None, "透明な林檎"),
    ("アート", "蜂と蝶", "光の方を君は見ている\n蜂と蝶\n光の方を君は見ている"),
    ("アート", None, "パターン化されたエモ"),
    ("アート", None, "遅い疾走感"),
    ("アート", "桜へ", "逆流性の時間の中で花びらが吸い込まれるようにして桜へ"),

    # ── 趣味 ──
    ("趣味", None, "記憶が手に取れる場所にあるのは、媒体の中に眠らせておくより愛おしい。"),
    ("趣味", None, "途中まで編んだものが、箱の中で三年待っている"),
    ("趣味", None, "うまくならなくていい時間が、一日にひとつだけある"),
    ("趣味", None, "同じ曲を、同じところで巻き戻している"),

    # ── いじめ ──
    # 声を荒げない・助言をしない・出口を指さない。置いてあるだけにする。
    ("いじめ", None, "上履きの中の砂を、毎朝ひとりで出していた"),
    ("いじめ", None, "あの教室の匂いだけ、いまでも避けて通る"),
    ("いじめ", None, "笑っておけばいいと覚えたのは、たぶんあの年だった"),
    ("いじめ", None, "名前を呼ばれない日が続くと、自分の名前を忘れる"),

    # ── 2026-07-26 追加：字数超過・門番の対象で不採用だった詩片の意訳 ──
    # 原文のままでは80字超・またはケア判定/門番に触れるため置けなかったものを、
    # 意味を保ったまま短く言い換えた（Kosei了承・原文はやり取りのログに残る）。
    ("世界", None, "群れて咲く花より、たった一輪で咲く花のほうが、堂々として美しく見える時がある。"),
    ("恋愛", None, "恋はバラのように華やかで棘があり、愛は桜のように儚くも静かに心を満たす。"),
    ("人生", None, "大衆とは、自分に特別な理由を求めず、みんなと同じであることに満足している人たちだ。"),
    ("友達", None, "一番波長が合う人には、もっと合う人がいる。誰かの一番だと思うのは驕りだと知る。"),
    ("心", None, "連絡だけ残して、消えた。すぐにでも見つけてほしかったから。"),
]


def recolor(username, dry_run=False, seed_value=20260726):
    """置いたあとから色だけを引き直す。本文・日付・部屋には触らない。
    色の付け方を変えた時に、置き直さずに（＝ことばを消さずに）追いつかせるための口。"""
    init_db()
    rnd = random.Random(seed_value)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    user = db.execute("SELECT id, username FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        print("ユーザー「%s」が見つかりません。" % username)
        return 1
    n = 0
    for wi, (_room, _title, poem) in enumerate(WORDS):
        color = word_color(wi, rnd)
        if dry_run:
            print("  %-22s %s" % (poem.replace("\n", "／")[:20], color))
            n += 1
            continue
        cur = db.execute("UPDATE letters SET seal_color=? WHERE user_id=? AND poem=?",
                         (color, user["id"], poem))
        n += cur.rowcount
    if not dry_run:
        db.commit()
    print("\n%d 件の色を引き直しました。" % n)
    return 0


def seed(username, dry_run=False, seed_value=20260726):
    init_db()
    rnd = random.Random(seed_value)   # 同じDBに二度流しても同じ色・同じ日付になる
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    user = db.execute("SELECT id, username FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        names = [r["username"] for r in db.execute("SELECT username FROM users").fetchall()]
        print("ユーザー「%s」が見つかりません。存在するユーザー: %s" % (username, ", ".join(names)))
        return 1

    rooms = {r["name"]: r["id"] for r in
             db.execute("SELECT id, name FROM rooms WHERE deleted_at IS NULL").fetchall()}
    have = {r["poem"] for r in
            db.execute("SELECT poem FROM letters WHERE COALESCE(poem,'')<>''").fetchall()}

    now = datetime.now()
    placed, skipped, per_room = [], [], {}

    # 色は「部屋の中の並び」ではなく WORDS 全体の通し番号で回す。部屋をまたいでも
    # 色相が均等に散るので、どの部屋の中を見ても隣り合う色がかぶらない。
    for wi, (room_name, title, poem) in enumerate(WORDS):
        if room_name not in rooms:
            skipped.append((room_name, poem, "その部屋がない"))
            continue
        if len(poem) > POEM_MAX:
            skipped.append((room_name, poem, "80字を超える（%d字）" % len(poem)))
            continue
        if title and len(title) > TITLE_MAX:
            skipped.append((room_name, poem, "題が10字を超える"))
            continue
        if poem in have:
            skipped.append((room_name, poem, "もう置いてある"))
            continue
        # 門番とケアの判定は素通りさせない。宙に出ないことばは、置く意味がない。
        if _needs_care(poem):
            skipped.append((room_name, poem, "ケアの判定に触れる（宙には出ない）"))
            continue
        status, _care = _moderate(poem)
        if status != "live":
            skipped.append((room_name, poem, "門番が %s にした（宙には出ない）" % status))
            continue

        color = word_color(wi, rnd)
        # 置いた日を 3〜96日前へ散らす。沈降（_sky_decay）と空気の近さ（季節・時刻）に
        # 幅が出るので、「いまと響き合う一通」の抽選が実際に働くようになる。
        sent = now - timedelta(days=rnd.randint(3, 96),
                               hours=rnd.randint(0, 23), minutes=rnd.randint(0, 59))
        arrive = _sky_arrive_at(sent)
        lid = secrets.token_hex(8)
        row = (lid, user["id"], poem, title, sent.isoformat(timespec="seconds"),
               arrive.date().isoformat(), arrive.isoformat(timespec="seconds"),
               json.dumps(_mood_words_from_poem(poem), ensure_ascii=False),
               color, rooms[room_name])
        placed.append((room_name, poem, color, row))
        per_room[room_name] = per_room.get(room_name, 0) + 1

    for name in sorted(rooms):
        n = per_room.get(name, 0)
        mark = "" if 3 <= n <= 8 else ("  ← 3〜8の外" if n else "  ← 一つも入らない")
        print("  %-5s %d%s" % (name, n, mark))
    if skipped:
        print("\n置かなかったことば（%d）:" % len(skipped))
        for room_name, poem, why in skipped:
            head = poem.replace("\n", "／")
            print("  [%s] %s … %s" % (room_name, head[:28] + ("…" if len(head) > 28 else ""), why))

    if dry_run:
        print("\n--dry-run なので何も書いていません（%d 件を置く予定）" % len(placed))
        return 0

    for _room_name, _poem, _color, row in placed:
        db.execute(
            """INSERT INTO letters
               (id,user_id,poem,title,photo,voice,sent_date,arrive_date,arrive_at,
                arrive_label,arrive_hidden,opened,notified,emos,from_reply,
                seal_color,vertical,mode,sky_status,room_id,demo_mode)
               VALUES (?,?,?,?,NULL,NULL,?,?,?,'',1,0,1,?,0,?,1,'sky','live',?,0)""",
            row)
    db.commit()
    print("\n%d 件のことばを置きました（作者=%s）。" % (len(placed), user["username"]))
    print("宙のキャッシュは15秒で切れるので、/mood を開き直せば漂いはじめます。")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--as", dest="username", required=True,
                    help="置いたことばの作者にするアカウント名（実在の人は選ばない）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recolor", action="store_true",
                    help="本文は置き直さず、色だけを引き直す")
    a = ap.parse_args()
    sys.exit((recolor if a.recolor else seed)(a.username, a.dry_run))

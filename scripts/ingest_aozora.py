# -*- coding: utf-8 -*-
"""青空文庫から、宙に漂う一節を拾う（v3 §4.4・冪等）。

    python3 scripts/ingest_aozora.py --src ~/aozorabunko            # 下見（何も書かない）
    python3 scripts/ingest_aozora.py --src ~/aozorabunko --apply    # 実行
    python3 scripts/ingest_aozora.py --csv seed/tayori_pd.csv --apply  # 抽出済みCSVから入れる
    python3 scripts/ingest_aozora.py --remove --apply               # 置いたものだけ取り下げる

--src には青空文庫のリポジトリ（github.com/aozorabunko/aozorabunko）を丸ごと置いた
場所を渡す。数十GBあるので本番には置かない：ここで一度だけ抽出して CSV に落とし、
本番は --csv でその CSV を読む。抽出は決定的なので、同じ入力から同じ CSV が出る。

【何をAIがやり、何をやらないか（§2.2）】
拾うかどうかを決めるのは、ふるい（決定的な規則）と、意味ベクトルによる採点の二段。
ベクトルは semantic/potion_ja.npz——アプリが「探す」で使っているのと同じ静的な表で、
外部のAI事業者へ本文を送る処理は無い（そもそも著作権の切れた本なので送っても
構わないが、経路を作らないほうが説明が一行で済む）。
生成はしない。要約もしない。**本にある文を、そのまま切り出すだけ**。

【なぜ「そのまま」なのか】
言い換えた瞬間、それは漱石の文ではなく機械の文になる。出典を刻む資格が消える。

【ふるいの考え方】
・新字新仮名の作品だけを見る（索引の『文字遣い種別』）。旧字旧仮名は、いまの人が
  ふと目にして読める字ではない——宙は読解の場ではない。
・句点で終わる、18〜48字の一文だけ。長すぎるものは**切らずに落とす**（切ると、
  誰も書かなかった文が生まれる）。80字いっぱいの地の文は「物語の途中」に見える。
・指示語で始まる文と、誰のことか分からない人物への言及は落とす。前後が無い場所へ
  出すのだから、一片で立っていないものは置けない。
・鉤括弧を含む文は落とす。会話は前後が無いと宙で迷子になる。
・現代語の語尾で終わる文だけを残す。文語は、静かではなく、遠い。
・注記・ルビ・見出しは本文ではないので、読む前に落とす。

【それでも通すもの】
・掲載の門番（_moderate）… 本だから素通り、はやらない。人のことばと同じ門をくぐらせる。
・意味の索引（sem_store, source_type='public_domain'）… 「探す」の対象になる。
  ここを飛ばすと、漂流物だけ探せない宙になる。

【やらないこと】
・letters には入れない。external_texts は letters の外にあり、書架・取り消し・
  棚の報せ・季節の返却・宙からの配達は letters.user_id で人を引いている＝
  漂流物には最初から届かない（フェーズ1のコミットメッセージ参照）。
・打鍵（trace_z）は作らない。書いた人が居ないものに、ためらいの間は無い。
・外部リンクは持たない。出典は文字として刻むだけで、外へ出る導線は作らない。
"""
import argparse
import csv
import hashlib
import html
import io
import os
import re
import sys
import zipfile
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import (DB_PATH, _connect, init_db, _moderate,  # noqa: E402
                 sem_ready, sem_embed, sem_store, _WRITE_LOCK, _sky_cache_bust)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(HERE, "..", "seed", "tayori_pd.csv")
INDEX_REL = os.path.join("index_pages", "list_person_all_extended_utf8")

# 人のことばの上限は80字だが、拾うのはもっと短い一文だけにする。80字いっぱいの
# 地の文は「物語の途中」に見え、宙に漂うと前後を探させてしまう——漂流物は、
# 前後が無くても一片で立っていなければならない。
BODY_MIN, BODY_MAX = 18, 48
PER_WORK = 2                    # 一冊から拾う上限。多く採ると、その本の色が宙に出る
PER_AUTHOR = 10                 # 一人から拾う上限。ここが無いと、多作な人の宙になる
TOTAL_CAP = 4000                # 全体の上限。漂流物は、流れ着くものであって在庫ではない
SCORE_MIN = 0.40                # 情景の錨との近さの下限。これ未満は「ただの文」


# ── 情景と余白の錨 ────────────────────────────────────────────────
# 「情景や余白を持つ一節」を測るための、こちらから差し出す物差し。
# 生成に使うのではなく、候補の一文がこれらにどれだけ近いかを測るだけ。
ANCHORS = (
    "空が高く、風の音だけが遠くから聞こえてくる。",
    "雨が降っていて、窓の外はしずかに暗い。",
    "光が差して、影がゆっくりと伸びていった。",
    "夜のなかで、遠くの音に耳をすませている。",
    "何も言わずに、ただそこに立っていた。",
    "ふと気がつくと、季節が変わっていた。",
    "海と山のあいだに、白い花が咲いている。",
    "こころのどこかが、しずかに動いた気がする。",
)

# ── ふるい ────────────────────────────────────────────────────────
_SENT_END = re.compile(r"[。？！]")
# 現代語の語尾。文語（けり・なりき・べし）を静かに落とすための正の条件。
_MODERN_TAIL = re.compile(
    r"(た|る|い|う|く|ない|だ|です|ます|ました|でした|か|の|よ|ね|さ|ろ|え|る)。$")
_BAD_CHARS = re.compile(r"[「」『』（）\(\)［］\[\]｛｝〔〕＃#※＊*／＼/\\｜|＜＞<>]")
_DIGITS = re.compile(r"[0-9０-９]")
_KANA = re.compile(r"[ぁ-んァ-ヶ]")
# 前後が無いと宙で迷子になる文。指示語で始まる文（「それは」「そこへ」）と、
# 誰のことか分からない人物への言及（彼・おじさん）は、一片で立てない。
_DEIXIS_HEAD = re.compile(r"^(それ|そこ|そう|これ|ここ|あれ|あそこ|あの|その|この|彼)")
_NEEDS_CAST = re.compile(r"彼女|彼|おじさん|おばさん|おじいさん|おばあさん|"
                         r"お父さん|お母さん|主人|旦那|奥さん|さんは|君は|ちゃんは")
# 青空文庫の注記とルビ（テキスト版・XHTML版のどちらにも出る形）
_ANNOT = re.compile(r"［＃[^］]*］")
_RUBY_PAREN = re.compile(r"《[^》]*》")
_RUBY_MARK = re.compile(r"｜")
_TAG = re.compile(r"<[^>]+>")
_RT = re.compile(r"<rp>.*?</rp>|<rt>.*?</rt>", re.S)
_NOTES = re.compile(r'<span class="notes">.*?</span>', re.S)
_MAIN = re.compile(r'<div class="main_text">(.*?)</div>', re.S)
_BR = re.compile(r"<br\s*/?>", re.I)


def strip_markup(raw):
    """青空文庫のXHTML → 本文だけの平文。ルビの読み・注記・見出しは本文ではない。"""
    m = _MAIN.search(raw)
    body = m.group(1) if m else raw
    body = _NOTES.sub("", body)
    body = _RT.sub("", body)          # ルビの読み（rb＝親字だけ残る）
    body = _BR.sub("\n", body)
    body = _TAG.sub("", body)
    body = html.unescape(body)
    body = _ANNOT.sub("", body)       # ［＃「〜」に傍点］の類
    body = _RUBY_PAREN.sub("", body)  # 《よみ》
    body = _RUBY_MARK.sub("", body)   # ルビの始まりを示す｜
    return body


def sentences(text):
    """句点で切る。切れ目そのものは残す（文として読める形で宙へ出すため）。"""
    out, buf = [], []
    for ch in text:
        buf.append(ch)
        if _SENT_END.match(ch):
            out.append("".join(buf).strip())
            buf = []
    return out


def acceptable(s):
    """宙へ出してよい一文か。迷ったら落とす——拾い漏らしは誰も困らない。"""
    s = s.strip()
    if not (BODY_MIN <= len(s) <= BODY_MAX):
        return False                      # 80字超は切らずに落とす
    if not s.endswith("。"):
        return False                      # 言い切っていない文は、宙で宙ぶらりんになる
    if not _MODERN_TAIL.search(s):
        return False                      # 文語は、静かではなく遠い
    if _BAD_CHARS.search(s) or _DIGITS.search(s):
        return False                      # 会話・注記の残り・数字
    if s.count("、") > 2:
        return False                      # 息が長い文は、漂う一片には向かない
    if _DEIXIS_HEAD.match(s) or _NEEDS_CAST.search(s):
        return False                      # 前後が無いと立てない文（指示語・登場人物）
    kana = len(_KANA.findall(s))
    if kana < len(s) * 0.3:
        return False                      # 漢字ばかりの文は、目が滑る
    if "\n" in s:
        return False
    return True


def open_index(src):
    """索引を開く。リポジトリでは zip で置かれているので、そのまま中を読む
    （展開させると、手順が一つ増えるぶんだけ再現しにくくなる）。"""
    zpath = os.path.join(src, INDEX_REL + ".zip")
    cpath = os.path.join(src, INDEX_REL + ".csv")
    if os.path.exists(cpath):
        return open(cpath, encoding="utf-8-sig", newline="")
    if os.path.exists(zpath):
        z = zipfile.ZipFile(zpath)
        inner = next((n for n in z.namelist() if n.lower().endswith(".csv")), None)
        if inner:
            return io.TextIOWrapper(z.open(inner), encoding="utf-8-sig", newline="")
    sys.exit(f"索引が見つかりません: {zpath}")


def load_index(src):
    """青空文庫の索引。著作権の切れた新字新仮名の作品だけを返す。"""
    works = []
    with open_index(src) as f:
        for r in csv.DictReader(f):
            if r.get("作品著作権フラグ") != "なし":
                continue                  # 著作権の切れたものだけ（§4.4）
            if r.get("人物著作権フラグ") != "なし":
                continue
            if r.get("文字遣い種別") != "新字新仮名":
                continue
            if not _NDC_OK.search(r.get("分類番号") or ""):
                continue                  # 小説・詩・戯曲だけ（評論と随筆は外す）
            url = (r.get("XHTML/HTMLファイルURL") or "").strip()
            if not url:
                continue
            rel = url.split("/cards/", 1)[-1]
            rel = os.path.join("cards", *rel.split("/"))
            author = author_name(r.get("姓"), r.get("名"))
            title = (r.get("作品名") or "").strip()
            if not (author and title):
                continue
            works.append((os.path.join(src, rel), author, title))
    return works


# 日本十進分類。9＝文学、真ん中が言語（1=日本語 3=英語 4=ドイツ語 …）、
# 末尾が種類で 1=詩歌 / 2=戯曲 / 3=小説 / 4=評論・随筆 / 5=日記・書簡。
# K は児童書。ここで採るのは末尾 1・2・3 だけ——随筆の一文は「これから述べる」の
# 途中であることが多く、切り出すと論の断面になる（実際、漱石『無題』の
# 「しからばまるで無茶なものかというと」が拾えてしまった）。
_NDC_OK = re.compile(r"NDC\s*K?9\d[123]\b")
_KATAKANA_NAME = re.compile(r"^[ァ-ヶー・]+$")


def author_name(sei, mei):
    """索引の姓・名から、刻印に出す名前を組む。

    日本語の名前は姓＋名をそのまま繋ぐ（夏目＋漱石）。西洋の名前は姓＝アミーチス／
    名＝エドモンド・デ のように分かれて入っているので、繋ぐ順を逆にして中黒で結ぶ
    ——そうしないと「アミーチスエドモンド・デ」と刻むことになる。
    出典は、その人の名前をその人の名前として書けないなら、刻む資格がない。"""
    sei = (sei or "").strip()
    mei = (mei or "").strip()
    if not sei:
        return mei
    if not mei:
        return sei
    if _KATAKANA_NAME.match(sei) and _KATAKANA_NAME.match(mei):
        return f"{mei}・{sei}".replace("・・", "・")
    return f"{sei}{mei}"


def score_all(cands):
    """情景の錨との近さ。候補ごとに、いちばん近い錨との内積を返す。"""
    import numpy as np
    anchors = [sem_embed(a) for a in ANCHORS]
    anchors = np.stack([a for a in anchors if a is not None])
    out = []
    for body in cands:
        v = sem_embed(body)
        out.append(float((anchors @ v).max()) if v is not None else -1.0)
    return out


def pd_id(author, title, body):
    """出典と本文から決まる id。二度走らせても同じ行に当たる＝冪等の要。"""
    key = f"tayori-pd:{author}:{title}:{body}"
    return "pd" + hashlib.sha256(key.encode()).hexdigest()[:14]


def extract(src, limit_works=0, sample=0):
    """青空文庫の丸ごとから、拾った一節の一覧を作る（決定的）。"""
    works = load_index(src)
    works.sort(key=lambda w: (w[1], w[2], w[0]))     # 決定的な順に
    if sample and sample < len(works):
        # 下見用：頭から取ると著者名の五十音順で偏るので、全体から等間隔に抜く
        step = len(works) / float(sample)
        works = [works[int(i * step)] for i in range(sample)]
    if limit_works:
        works = works[:limit_works]
    print(f"[たより] 著作権の切れた新字新仮名の作品: {len(works)}")
    seen_bodies, rows = set(), []
    read_err = 0
    for i, (path, author, title) in enumerate(works):
        if i and i % 500 == 0:
            print(f"  … {i}/{len(works)} 冊　拾った一節 {len(rows)}", flush=True)
        try:
            with open(path, encoding="shift_jis", errors="ignore") as f:
                raw = f.read()
        except OSError:
            read_err += 1
            continue
        cands = [s for s in sentences(strip_markup(raw)) if acceptable(s)]
        cands = [s for s in dict.fromkeys(cands) if s not in seen_bodies]
        if not cands:
            continue
        scored = sorted(zip(score_all(cands), cands), key=lambda t: (-t[0], t[1]))
        for sc, body in scored[:PER_WORK]:
            if sc < SCORE_MIN:
                break
            seen_bodies.add(body)
            rows.append({"id": pd_id(author, title, body), "body": body,
                         "source_author": author, "source_title": title,
                         "score": round(sc, 4)})
    if read_err:
        print(f"[たより] 読めなかった本: {read_err}")
    return balance(rows)


def balance(rows):
    """著者ごとに均してから、全体の上限まで採る。

    均さずに上から採ると、多作な人の宙になる（実測：小川未明ひとりで4001片中884片）。
    しかも本を著者順に見ているので、上限に当たった時点で五十音の後ろ半分が
    一片も入らないまま終わっていた。
    まず一人あたりの上限で切り、そのうえで著者を順に一片ずつ拾っていく。
    こうすると上限に当たっても、全員から均等に欠ける。"""
    by_author = {}
    for r in sorted(rows, key=lambda r: (-r["score"], r["id"])):
        got = by_author.setdefault(r["source_author"], [])
        if len(got) < PER_AUTHOR:
            got.append(r)
    order = sorted(by_author)
    out = []
    for i in range(PER_AUTHOR):
        for name in order:
            if i < len(by_author[name]):
                out.append(by_author[name][i])
            if len(out) >= TOTAL_CAP:
                return out
    return out


def write_csv(rows, path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "body", "source_author",
                                          "source_title", "score"])
        w.writeheader()
        w.writerows(rows)
    print(f"[たより] 書き出し: {path}（{len(rows)}片）")


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def place(rows, apply_):
    """門番を通して、external_texts と意味の索引へ入れる。

    部屋は決めない（room_id は NULL のまま）。本から拾った一節は「何について書かれた
    か」を持たない。ためしに意味で寄せてみたら、雨の庭の一文が『いじめ』へ行った
    ——部屋を当てられないものに部屋を当てるのは、この宙でいちばんやってはいけない
    ことのひとつ（app.py の _classify_room の注釈）。持たないものは、どこにでも在れる。
    アプリ側（_in_room）が、部屋を持たない一片をどの部屋にも流れ着かせる。"""
    db = _connect()
    init_db()
    if not sem_ready():
        sys.exit("意味の索引の表が読めません（semantic/ を確認してください）。")

    stopped, keep = Counter(), []
    for r in rows:
        status, _ = _moderate(r["body"])
        if status != "live":
            stopped[status] += 1
            continue                       # 本だから素通り、はやらない
        keep.append(r)

    print(f"[たより] 門番が止めた: {dict(stopped) or 'なし'}")
    if not apply_:
        print(f"[たより] 下見（--apply で書き込みます）。置くもの {len(keep)}片")
        for r in keep[:5]:
            print(f"    {r['source_author']}『{r['source_title']}』　{r['body']}")
        return

    now = datetime.now().isoformat(timespec="seconds")
    put = 0
    with _WRITE_LOCK:
        for r in keep:
            cur = db.execute(
                "INSERT OR IGNORE INTO external_texts"
                " (id, body, source_author, source_title, license, room_id,"
                "  sky_status, created_at) VALUES (?,?,?,?,'public_domain',NULL,'live',?)",
                (r["id"], r["body"], r["source_author"], r["source_title"], now))
            if cur.rowcount:
                put += 1
            sem_store(db, r["id"], r["body"], source_type="public_domain")
        db.commit()
    _sky_cache_bust()
    print(f"[たより] 宙へ置きました: 新規 {put}片（既にあったもの {len(keep) - put}片）")


def remove(apply_):
    db = _connect()
    n = db.execute("SELECT COUNT(*) c FROM external_texts").fetchone()["c"]
    if not apply_:
        print(f"[たより] 下見：取り下げる漂流物 {n}片（--apply で実行）")
        return
    with _WRITE_LOCK:
        for t in ("DELETE FROM letter_vectors WHERE letter_id IN"
                  " (SELECT id FROM external_texts)",
                  "DELETE FROM muted WHERE letter_id IN (SELECT id FROM external_texts)",
                  "DELETE FROM sky_seen WHERE letter_id IN (SELECT id FROM external_texts)",
                  "DELETE FROM sky_cycle_seen WHERE letter_id IN"
                  " (SELECT id FROM external_texts)",
                  "DELETE FROM external_texts"):
            db.execute(t)
        db.commit()
    _sky_cache_bust()
    print(f"[たより] 取り下げました: {n}片")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--src", help="青空文庫のリポジトリを置いた場所")
    p.add_argument("--csv", default=DEFAULT_CSV, help="抽出結果の置き場")
    p.add_argument("--limit-works", type=int, default=0, help="下見用：見る本の数を絞る")
    p.add_argument("--sample", type=int, default=0, help="下見用：全体から等間隔に抜く冊数")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--remove", action="store_true")
    a = p.parse_args()
    print(f"[たより] DB: {DB_PATH}")
    if a.remove:
        remove(a.apply)
        return
    if a.src:
        rows = extract(a.src, a.limit_works, a.sample)
        write_csv(rows, a.csv)
    else:
        if not os.path.exists(a.csv):
            sys.exit(f"CSV がありません: {a.csv}（--src で抽出してください）")
        rows = read_csv(a.csv)
        print(f"[たより] CSV から {len(rows)}片")
    place(rows, a.apply)


if __name__ == "__main__":
    main()

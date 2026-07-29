# -*- coding: utf-8 -*-
"""
tayori TypeTrace 生成スクリプト

入力: tayori_seed_grid_300.csv （本文が埋まっている前提。空の行はスキップ）
出力: tayori_seed_traces.csv （no, trace_z(base64) を付与）

思想:
  - 日本語IMEを再現する。1文字ずつではなく「かたまり」で挿入する。
  - イベント列は [dt, op, ch] （app.py と同じ形）
      dt : 直前イベントからの経過ミリ秒（int）
      op : "i"=挿入 / "d"=削除 / "s"=全文スナップショット
      ch : 文字（挿入時）/ "" （削除時）/ 全文（スナップショット時）
  - 記録は「3文字目が入った時点から」。最初の2文字ぶんの迷いは残さない。
  - traceパターン(1〜6)で打ち方を変える。
  - 上限3000イベント。まず当たらないが安全弁を入れる。
  - 出力は zlib 圧縮 → base64。DBには圧縮済みバイト列を trace_z に入れる想定。
    （このスクリプトはCSV確認用にbase64で出す。DB投入時はbase64をデコードして bytea へ）
（2026-07-29：たより本体のリポジトリへ取り込んだ。入出力の場所だけ seed/ へ向けた。
  乱数の種は固定なので、何度走らせても同じ筆跡が出る＝投入も冪等になる）
"""
import csv, json, zlib, base64, random, re, os

HERE = os.path.dirname(os.path.abspath(__file__))

random.seed(20260729)

MAX_EVENTS = 3000
SNAPSHOT_EVERY = 40  # 何イベントかごとに全文スナップショットを挟む（再生の自己修復用）

# ---------------------------------------------------------------- かたまり分割
def chunk_text(text):
    """
    本文を IME の変換確定単位っぽいかたまりに割る。
    - ひらがな連続 / カタカナ連続 / 漢字連続 / 英数連続 / 記号 で切り、
      さらに2〜5文字に刻む。句読点は直前のかたまりに付ける。
    """
    if not text:
        return []
    # 文字種でまず粗く分割
    runs = re.findall(
        r'[ぁ-ん]+|[ァ-ヴー]+|[一-龥々]+|[a-zA-Z0-9]+|[、。！？…「」（）\s]|.',
        text
    )
    chunks = []
    for run in runs:
        if run in "、。！？…「」（）" or run.strip() == "":
            # 句読点・空白は直前に吸収（先頭なら単独）
            if chunks:
                chunks[-1] += run
            else:
                chunks.append(run)
            continue
        # 2〜5文字に刻む
        i = 0
        while i < len(run):
            size = random.randint(2, 5)
            chunks.append(run[i:i+size])
            i += size
    return chunks

# ---------------------------------------------------------------- 基本タイピング
def emit_chunk(events, chunk, first_gap):
    """かたまりを1つ打つ。かたまり内は速く、先頭に変換の間。"""
    gap = first_gap
    for ch in chunk:
        events.append([gap, "i", ch])
        gap = random.randint(30, 80)  # かたまり内は速い

def type_plain(text, pause_hook=None):
    """
    text を素直に（IMEかたまりで）打つ。
    pause_hook(events, chunk_index) を差し込めるようにしてある。
    """
    events = []
    chunks = chunk_text(text)
    for ci, chunk in enumerate(chunks):
        # かたまり間 = 変換の間
        first_gap = random.randint(150, 600) if ci > 0 else 0
        if pause_hook:
            first_gap = pause_hook(events, ci, first_gap)
        emit_chunk(events, chunk, first_gap)
    return events, chunks

def current_text(events):
    """イベント列を再生して現在の全文を得る（削除対応）。"""
    buf = []
    for dt, op, ch in events:
        if op == "i":
            buf.append(ch)
        elif op == "d":
            if buf:
                buf.pop()
        elif op == "s":
            buf = list(ch)
    return "".join(buf)

def delete_n(events, n, fast=False):
    """末尾からn文字消す。"""
    for k in range(n):
        gap = random.randint(40, 90) if fast else random.randint(60, 160)
        events.append([gap, "d", ""])

# ---------------------------------------------------------------- 6パターン
def pattern_1(text):
    """一気に書いて終わり（迷いなし）。"""
    events, _ = type_plain(text)
    return events

def pattern_2(text):
    """途中で3〜8秒止まる（にじみ）。1〜2箇所に長い停止。"""
    chunks = chunk_text(text)
    stop_at = set()
    if len(chunks) > 3:
        n_stops = random.randint(1, 2)
        stop_at = set(random.sample(range(1, len(chunks)), min(n_stops, len(chunks)-1)))
    def hook(events, ci, first_gap):
        if ci in stop_at:
            return random.randint(3000, 8000)
        return first_gap
    events, _ = type_plain(text, pause_hook=hook)
    return events

def pattern_3(text):
    """書いて全部消して、もう一度。最初は途中まで書いて全消し。"""
    events = []
    # 最初の試み：全体の40〜70%まで書く
    chunks = chunk_text(text)
    cut = max(1, int(len(chunks) * random.uniform(0.4, 0.7)))
    first_try = "".join(chunks[:cut])
    ev1, _ = type_plain(first_try)
    events += ev1
    # 少し止まる
    if events:
        events[-1][0] = events[-1][0]  # noop
    pause = random.randint(1200, 4000)
    # 全消し（まとめて速く）
    n = len(current_text(events))
    delete_n(events, n, fast=True)
    if events:
        events.append([pause, "i", ""])  # ダミーを避けるため直後の間で表現
        events.pop()  # 上のダミーは消す
    # 2回目：本番を書く（消した直後なので少し間を置いて）
    ev2, _ = type_plain(text)
    if ev2:
        ev2[0][0] = pause  # 書き直しの前の間
    events += ev2
    return events

def pattern_4(text):
    """語尾を何度も直す。末尾の1〜3文字を付けては消す、を2〜3回。"""
    events, chunks = type_plain(text)
    tails = ["。", "…", "な", "ね", "かな", "、", "！"]
    n_edits = random.randint(2, 3)
    for _ in range(n_edits):
        t = random.choice(tails)
        # 付ける
        gap = random.randint(400, 1500)
        for ch in t:
            events.append([gap, "i", ch])
            gap = random.randint(40, 90)
        # 少し眺めて消す（付けた語尾は必ず消し、本文どおりで終える）
        delete_n(events, len(t), fast=False)
    return events

# 誤変換ペア：正しい語 -> よくある誤変換
MISCONVERT = [
    ("今日", "京"), ("私", "渡し"), ("聞く", "効く"), ("変える", "帰る"),
    ("会う", "合う"), ("見る", "診る"), ("時", "とき"), ("好き", "隙"),
    ("以外", "意外"), ("最後", "最期"), ("夜", "世"), ("空", "から"),
    ("雨", "飴"), ("声", "肥"), ("暑い", "熱い"), ("形", "肩"),
]
def pattern_5(text):
    """誤変換を1回直す。本文中に対象語があればそこで、無ければ末尾で1語ミスる。"""
    hit = None
    for correct, wrong in MISCONVERT:
        pos = text.find(correct)
        if pos != -1:
            hit = (pos, correct, wrong)
            break
    if hit is None:
        # 対象語が無い：普通に打って、最後の1かたまりを一度ミスって直す風味
        events, chunks = type_plain(text)
        if len(chunks) >= 1 and len(text) >= 2:
            # 末尾1文字を消して打ち直す（誤変換を直した体）
            delete_n(events, 1, fast=True)
            gap = random.randint(500, 1500)
            events.append([gap, "i", text[-1]])
        return events

    pos, correct, wrong = hit
    before = text[:pos]
    after = text[pos+len(correct):]
    events = []
    # before を打つ
    ev0, _ = type_plain(before) if before else ([], [])
    events += ev0
    # 誤変換語を打つ
    gap = random.randint(150, 500)
    for ch in wrong:
        events.append([gap, "i", ch])
        gap = random.randint(30, 80)
    # 気づいて消す
    look = random.randint(600, 2500)
    events.append([look, "d", ""])
    for _ in range(len(wrong) - 1):
        events.append([random.randint(40, 90), "d", ""])
    # 正しい語を打つ
    gap = random.randint(300, 900)
    for ch in correct:
        events.append([gap, "i", ch])
        gap = random.randint(30, 80)
    # after を打つ
    ev1, _ = type_plain(after) if after else ([], [])
    if ev1:
        ev1[0][0] = random.randint(150, 500)
    events += ev1
    return events

def pattern_6(text):
    """最後の一語だけ書き換えて放つ。最後のかたまりを別語で打ってから本命に。"""
    chunks = chunk_text(text)
    if len(chunks) < 2:
        return pattern_1(text)
    body = "".join(chunks[:-1])
    last = chunks[-1]
    alt_pool = ["けど", "みたい", "たぶん", "かも", "だけ", "なのに", "でも"]
    alt = random.choice(alt_pool)
    events, _ = type_plain(body)
    # 仮の一語
    gap = random.randint(300, 800)
    for ch in alt:
        events.append([gap, "i", ch])
        gap = random.randint(30, 80)
    # 眺めて消す
    look = random.randint(800, 3000)
    delete_n(events, len(alt), fast=False)
    if events:
        events[-len(alt)][0] = look
    # 本命を打つ
    gap = random.randint(400, 1200)
    for ch in last:
        events.append([gap, "i", ch])
        gap = random.randint(30, 80)
    return events

PATTERN_FN = {1: pattern_1, 2: pattern_2, 3: pattern_3,
              4: pattern_4, 5: pattern_5, 6: pattern_6}

# ---------------------------------------------------------------- 記録開始点の再現
def trim_to_third_char(events):
    """
    「3文字目が入った時点から記録」を再現する。
    先頭の挿入イベントを数え、3個目の挿入より前の挿入/削除を落とす。
    ただし3個目の挿入の dt は 0 に丸める（記録開始＝相対時刻ゼロ）。
    """
    ins = 0
    start = None
    for i, (dt, op, ch) in enumerate(events):
        if op == "i":
            ins += 1
            if ins == 3:
                start = i
                break
    if start is None:
        return events  # 3文字未満：そのまま（本来は記録されないが安全側）
    # start より前に確定していた全文（最初の2文字ぶん等）を復元し、
    # 記録開始時点のスナップショットとして先頭に置く。
    # これがないと再生時に頭の数文字が欠ける。
    prefix_state = current_text(events[:start])
    trimmed = events[start:]
    if trimmed:
        trimmed[0] = [0, trimmed[0][1], trimmed[0][2]]
    if prefix_state:
        trimmed = [[0, "s", prefix_state]] + trimmed
    return trimmed

def insert_snapshots(events):
    """一定間隔で全文スナップショットを挟む（壊れ耐性）。"""
    out = []
    buf = []
    for i, (dt, op, ch) in enumerate(events):
        out.append([dt, op, ch])
        if op == "i":
            buf.append(ch)
        elif op == "d" and buf:
            buf.pop()
        elif op == "s":
            buf = list(ch)
        if (i + 1) % SNAPSHOT_EVERY == 0:
            out.append([0, "s", "".join(buf)])
    return out

def build_trace(text, pattern):
    fn = PATTERN_FN.get(int(pattern), pattern_1)
    events = fn(text)
    # 記録開始点を再現
    events = trim_to_third_char(events)
    # スナップショット挿入
    events = insert_snapshots(events)
    # 上限
    if len(events) > MAX_EVENTS:
        events = events[:MAX_EVENTS]
    # 最終全文が本文と一致するか検算（削除で崩れてないか）
    final = current_text(events)
    return events, final

# ---------------------------------------------------------------- 圧縮
def pack(events):
    raw = json.dumps(events, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    comp = zlib.compress(raw, 9)
    return comp, base64.b64encode(comp).decode("ascii"), len(raw), len(comp)

# ---------------------------------------------------------------- main
def main():
    src = os.path.join(HERE, "..", "seed", "tayori_seed_300.csv")
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig")))

    out_rows = []
    stats = {"traced": 0, "skipped_no_trace": 0, "skipped_empty": 0,
             "mismatch": 0, "max_events": 0, "max_b64": 0}
    ev_counts = []

    for r in rows:
        no = r["no"]
        text = (r.get("本文") or "").strip()
        want = r.get("trace") == "あり"
        pattern = r.get("traceパターン") or ""

        if not want:
            stats["skipped_no_trace"] += 1
            out_rows.append({"no": no, "trace_z_base64": "", "n_events": 0,
                             "raw_bytes": 0, "comp_bytes": 0, "final_ok": ""})
            continue
        if not text:
            stats["skipped_empty"] += 1
            out_rows.append({"no": no, "trace_z_base64": "", "n_events": 0,
                             "raw_bytes": 0, "comp_bytes": 0, "final_ok": "本文未記入"})
            continue

        events, final = build_trace(text, pattern)
        ok = (final == text)
        if not ok:
            stats["mismatch"] += 1
        comp, b64, raw_len, comp_len = pack(events)
        stats["traced"] += 1
        stats["max_events"] = max(stats["max_events"], len(events))
        stats["max_b64"] = max(stats["max_b64"], len(b64))
        ev_counts.append(len(events))
        out_rows.append({"no": no, "trace_z_base64": b64, "n_events": len(events),
                         "raw_bytes": raw_len, "comp_bytes": comp_len,
                         "final_ok": "OK" if ok else "NG"})

    out = os.path.join(HERE, "..", "seed", "tayori_seed_traces.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["no", "trace_z_base64", "n_events",
                                          "raw_bytes", "comp_bytes", "final_ok"])
        w.writeheader()
        w.writerows(out_rows)

    print("wrote", out)
    print("stats", stats)
    if ev_counts:
        print("events min/median/max:",
              min(ev_counts),
              sorted(ev_counts)[len(ev_counts)//2],
              max(ev_counts))
    return stats["traced"], stats["skipped_empty"]

if __name__ == "__main__":
    main()

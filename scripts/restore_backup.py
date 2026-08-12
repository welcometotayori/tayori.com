# -*- coding: utf-8 -*-
"""控えから戻す（2026-08-12）。

8/6 の点検で残った宿題はこれ一つだった——**控えがあることと、戻せることは別**。
日次で Cloudflare R2 へ上げてはいるが（app.py の `_run_backup_to_s3`）、
落として開いてみたことが一度も無い。人が増えてから初めて試すのが、いちばんまずい。

ここでやること（どれも live DB には指一本触れない）:

    list    R2 に何本あるか。いつのが最新か。
    fetch   一本落として、ほどいて、開いて、中身を数える。
    verify  手元にある控え（.db.gz でも .db でも）を、ほどいて開いて数える。
    drill   R2 を使わずに稽古する。いまの DB から控えを一本作り、それを verify に
            かける＝「詰めて・ほどいて・開く」の全経路を、鍵が無い手元でも試せる。

使い方:
    python scripts/restore_backup.py drill                     # 鍵が要らない稽古
    python scripts/restore_backup.py list                      # R2 の4変数が要る
    python scripts/restore_backup.py fetch latest --to /tmp/r  # 最新を落として検める
    python scripts/restore_backup.py verify /tmp/r/xxx.db.gz

R2 の4変数（TAYORI_BACKUP_S3_ENDPOINT / _BUCKET / _KEY / _SECRET）は、本番の
Render にだけ入っている。**手元へ写さなくていい**——`render ssh` で本番の箱に入り、
そこでこのまま走らせるのが早い（boto3 も env も既に在る）。

戻し方そのもの（どの順で何を置き換えるか）は docs/restore.md に書いた。
このスクリプトは**検めるだけ**で、DB を置き換えることはしない。
"""
import argparse
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime

# 数えるところ。青空文庫（external_texts / letter_vectors の大半）は作り直せる。
# 戻せたかどうかを決めるのは上の4つ——**人のことば**が入っているかどうか。
COUNT_TABLES = [
    ("users", "人"),
    ("letters", "ことば"),
    ("shelf_items", "棚に収めたもの"),
    ("sky_deliveries", "宙から配ったもの"),
    ("saved_tags", "付箋"),
    ("sky_marks", "しるし"),
    ("external_texts", "青空文庫（作り直せる）"),
]

CHUNK = 1024 * 1024   # 1MB ずつ。丸ごと載せない理由は app.py の `_run_backup_to_s3` に。


def _human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.1f}{u}" if u != "B" else f"{n}B"
        n /= 1024.0


def _s3():
    ep = os.environ.get("TAYORI_BACKUP_S3_ENDPOINT")
    bk = os.environ.get("TAYORI_BACKUP_S3_BUCKET")
    ak = os.environ.get("TAYORI_BACKUP_S3_KEY")
    sk = os.environ.get("TAYORI_BACKUP_S3_SECRET")
    if not (ep and bk and ak and sk):
        missing = [k for k, v in (("ENDPOINT", ep), ("BUCKET", bk), ("KEY", ak), ("SECRET", sk)) if not v]
        sys.exit("R2 の変数が足りません（TAYORI_BACKUP_S3_" + " / _".join(missing) + "）。"
                 "\n本番の箱の中でなら入っています： render ssh → python scripts/restore_backup.py list"
                 "\n鍵の要らない稽古だけなら： python scripts/restore_backup.py drill")
    try:
        import boto3
    except ImportError:
        sys.exit("boto3 がありません： pip install boto3")
    return boto3.client("s3", endpoint_url=ep, aws_access_key_id=ak, aws_secret_access_key=sk), bk


def _list_objects():
    s3, bucket = _s3()
    objs = s3.list_objects_v2(Bucket=bucket, Prefix="backups/").get("Contents", [])
    objs.sort(key=lambda o: o["Key"])
    return s3, bucket, objs


def cmd_list(args):
    _, bucket, objs = _list_objects()
    if not objs:
        print("控えが一本もありません。起動ログの「オフサイトBK有効」を確かめること。")
        return 1
    print(f"{bucket}/backups/ に {len(objs)} 本（世代は TAYORI_BACKUP_KEEP、既定14）")
    for o in objs:
        print(f"  {o['Key']}  {_human(o['Size']):>8}  {o['LastModified']:%Y-%m-%d %H:%M}")
    newest = objs[-1]
    age = (datetime.now(newest["LastModified"].tzinfo) - newest["LastModified"]).total_seconds() / 3600.0
    print(f"\n最新 {newest['Key']}（{age:.1f}時間前）")
    # 日次＋起動5分後なので、48時間も空いていたら上がっていない疑いがある。
    if age > 48:
        print("！ 最後の一本が2日より古い。バックアップが止まっている可能性があります。")
        return 1
    return 0


def cmd_fetch(args):
    s3, bucket, objs = _list_objects()
    if not objs:
        return 1
    key = objs[-1]["Key"] if args.key in ("latest", "最新") else args.key
    os.makedirs(args.to, exist_ok=True)
    dest = os.path.join(args.to, os.path.basename(key))
    if os.path.exists(dest) and not args.force:
        sys.exit(f"すでに在ります: {dest}（上書きするなら --force）")
    t0 = time.monotonic()
    s3.download_file(bucket, key, dest)
    print(f"落とした: {key} → {dest}（{_human(os.path.getsize(dest))} / {time.monotonic() - t0:.1f}秒）")
    return _verify(dest, keep_db=args.keep_db)


def cmd_verify(args):
    return _verify(args.path, keep_db=args.keep_db)


def _guard(path):
    """live DB を的にしない。控えを検める道具が、走っている DB を触りに行くことは無い。"""
    live = os.environ.get("TAYORI_DB_PATH") or ""
    if live and os.path.abspath(path) == os.path.abspath(live):
        sys.exit(f"それは動いている DB です（{live}）。控えの側を指してください。")


def _unpack(gz_path, keep_db):
    """流しながらほどく。ほどいた先は既定で一時ファイル（検め終わったら消す）。"""
    out = gz_path[:-3] if gz_path.endswith(".gz") else gz_path + ".db"
    if not keep_db:
        fd, out = tempfile.mkstemp(suffix=".db", prefix="tayori-restore-")
        os.close(fd)
    elif os.path.exists(out):
        sys.exit(f"ほどいた先がすでに在ります: {out}")
    _guard(out)
    t0 = time.monotonic()
    with gzip.open(gz_path, "rb") as fh, open(out, "wb") as w:
        shutil.copyfileobj(fh, w, CHUNK)
    print(f"ほどいた: {_human(os.path.getsize(out))}（{time.monotonic() - t0:.1f}秒）→ {out}")
    return out


def _verify(path, keep_db=False):
    if not os.path.exists(path):
        sys.exit(f"ありません: {path}")
    _guard(path)
    tmp = None
    if path.endswith(".gz"):
        db_path = _unpack(path, keep_db)
        tmp = None if keep_db else db_path
    else:
        db_path = path

    ok = True
    try:
        # 読むだけで開く（file: の ro）。検めている最中に何かを書いてしまわないため。
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        t0 = time.monotonic()
        res = conn.execute("PRAGMA integrity_check").fetchone()[0]
        print(f"integrity_check: {res}（{time.monotonic() - t0:.1f}秒）")
        if res != "ok":
            ok = False
        print("中身:")
        for t, label in COUNT_TABLES:
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error as e:
                print(f"  {t:<16} — {e}")
                # 人のことばの表が無いのは、控えとして成立していない。
                if t in ("users", "letters"):
                    ok = False
                continue
            print(f"  {t:<16} {n:>8}  {label}")
            if t in ("users", "letters") and n == 0:
                print(f"  ！ {t} が空です。控えとして成立していません。")
                ok = False
        # この控えは「いつまで」を持っているか。落ちた時刻との差が、失った窓の大きさ。
        try:
            last = conn.execute("SELECT MAX(sent_date) FROM letters").fetchone()[0]
            print(f"最後のことば: {last}")
        except sqlite3.Error:
            pass
        conn.close()
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass

    print("\n" + ("戻せます。" if ok else "！ この控えからは戻せません。もう一本古いのを検めること。"))
    return 0 if ok else 1


def cmd_drill(args):
    """鍵の要らない稽古。いまの DB から、本番と同じ手順で控えを一本作って検める。
    作るところ（sqlite の backup API → 流しながら詰める）は app.py の
    `_make_db_snapshot` / `_run_backup_to_s3` と同じ形にしてある。"""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src = args.db or os.environ.get("TAYORI_DB_PATH") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tayori.db")
    if not os.path.exists(src):
        sys.exit(f"元の DB がありません: {src}")
    out_dir = args.to or tempfile.mkdtemp(prefix="tayori-drill-")
    os.makedirs(out_dir, exist_ok=True)
    snap = os.path.join(out_dir, "drill.db")
    gz = snap + ".gz"
    print(f"元: {src}（{_human(os.path.getsize(src))}）")
    t0 = time.monotonic()
    s, d = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30), sqlite3.connect(snap)
    try:
        with d:
            s.backup(d, pages=64, sleep=0.01)
    finally:
        d.close()
        s.close()
    print(f"控えを作った: {_human(os.path.getsize(snap))}（{time.monotonic() - t0:.1f}秒）")
    t0 = time.monotonic()
    with open(snap, "rb") as fh, gzip.open(gz, "wb", compresslevel=6) as out:
        shutil.copyfileobj(fh, out, CHUNK)
    print(f"詰めた: {_human(os.path.getsize(gz))}（{time.monotonic() - t0:.1f}秒）")
    os.remove(snap)
    rc = _verify(gz)
    if not args.to:
        shutil.rmtree(out_dir, ignore_errors=True)
    return rc


def main():
    p = argparse.ArgumentParser(description="控えを検める（live DB には触らない）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="R2 の控えを並べる").set_defaults(func=cmd_list)

    f = sub.add_parser("fetch", help="一本落として検める")
    f.add_argument("key", nargs="?", default="latest", help="backups/... のキー、または latest")
    f.add_argument("--to", default=tempfile.gettempdir(), help="落とす先のフォルダ")
    f.add_argument("--keep-db", action="store_true", help="ほどいた .db を残す（戻すときはこれ）")
    f.add_argument("--force", action="store_true")
    f.set_defaults(func=cmd_fetch)

    v = sub.add_parser("verify", help="手元の控えを検める")
    v.add_argument("path")
    v.add_argument("--keep-db", action="store_true")
    v.set_defaults(func=cmd_verify)

    d = sub.add_parser("drill", help="鍵無しの稽古（いまの DB で全経路を試す）")
    d.add_argument("--db", help="元にする DB（既定は TAYORI_DB_PATH かリポジトリの tayori.db）")
    d.add_argument("--to", help="作った控えを残すフォルダ（既定は一時／終わったら消す）")
    d.set_defaults(func=cmd_drill)

    args = p.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()

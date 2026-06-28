"""
たより — tayori
自分宛ての遅延郵便。投げる → 封をする → 届く頃が来たら受信に現れる。
アカウントごとに、自分だけの便りを持てる。

起動:
    python run.py
    → 空きポートで自動起動します
"""

import os
import re
import ssl
import gzip
import json
import time
import random
import atexit
import shutil
import signal
import smtplib
import sqlite3
import secrets
import tempfile
import threading
import urllib.request   # 関数内で遅延importすると、複数スレッドが同時に初回importを走らせた際
import urllib.error     # 「cannot access submodule 'request'（循環import）」で失敗する。
                        # 起動時にモジュールレベルで1回だけimportして競合を防ぐ。
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr, make_msgid, formatdate
from functools import wraps
from datetime import datetime, date, timedelta

from flask import Flask, request, jsonify, render_template, g, session, Response
from werkzeug.security import generate_password_hash, check_password_hash

# サーバーのタイムゾーンを日本時間に固定する。
# Render などは既定が UTC のため、放っておくと datetime.now() が UTC になり
# 「届く時刻」がフロント（ブラウザ＝JST）と9時間ずれる。ここで一度 JST に固定すれば
# datetime.now() / date.today() がまとめて日本時間になる。
os.environ["TZ"] = os.environ.get("TAYORI_TZ", "Asia/Tokyo")
try:
    time.tzset()
except AttributeError:
    pass  # Windows には tzset が無い（その場合は OS の TZ 設定に従う）

APP_DIR = os.path.dirname(os.path.abspath(__file__))
# DBの保存先。デプロイ時はコンテナのファイルシステムが揮発するため、
# Render等では「永続ディスク」をマウントし、その場所を TAYORI_DB_PATH で指す。
# 例: Renderで /var/data に永続ディスクを付け、TAYORI_DB_PATH=/var/data/tayori.db
# これでデプロイ（再ビルド）してもユーザーが消えない。未設定ならローカルの tayori.db。
_DB_DESIRED = os.environ.get("TAYORI_DB_PATH") or os.path.join(APP_DIR, "tayori.db")


def _resolve_db_path(desired):
    """書き込み可能なDB保存先を返す。
    指定先（例：Renderで未マウントの /var/data）が使えない場合でも、アプリが
    クラッシュループせず必ず起動できるよう、書ける場所へ静かにフォールバックする。
    フォールバック時は『データが永続しない』ことを大きく警告する（落ち続けるより、まず立ち上げる）。"""
    candidates = [desired,
                  os.path.join(APP_DIR, "tayori.db"),
                  os.path.join(tempfile.gettempdir(), "tayori.db")]
    for i, p in enumerate(candidates):
        d = os.path.dirname(os.path.abspath(p))
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        if os.access(d, os.W_OK):
            if i > 0:
                print(f"[たより] ⚠️ 指定のDB保存先 {desired} に書き込めません。"
                      f"一時的に {p} を使って起動します。"
                      "【このままだと再デプロイでユーザーが消えます】"
                      "Renderの永続ディスクのMount Pathが TAYORI_DB_PATH と一致しているか確認してください。",
                      flush=True)
            return p
    return desired  # 通常ここには来ない（最後の保険）


DB_PATH = _resolve_db_path(_DB_DESIRED)

# ---- ライブDBを高速ローカルに置く（永続ディスクの遅い fsync を実行パスから外す）----
# Renderの永続ディスク(/var/data)はネットワーク接続で fsync が遅く、SQLiteの読み書きが
# 数秒～十数秒ハングして1ワーカーを食い潰す（loginの読み取りまで固まる）。そこで：
#   ・実行時は高速なローカル(/tmp)の「ライブDB」を使う＝読み書きが即時。
#   ・起動時に永続ディスク→ライブへ復元、定期＋終了時にライブ→永続へスナップショット保存。
# TAYORI_DB_PATH（=/var/data上）が指定されている本番でのみ既定ON。ローカル開発はOFF。
# 無効化したいときは TAYORI_DB_LOCAL_CACHE=0。
_PERSIST_DB_PATH = DB_PATH
_LOCAL_CACHE = (os.environ.get("TAYORI_DB_LOCAL_CACHE", "1") == "1"
                and bool(os.environ.get("TAYORI_DB_PATH")))
if _LOCAL_CACHE:
    DB_PATH = os.environ.get("TAYORI_LIVE_DB_PATH") or os.path.join(tempfile.gettempdir(), "tayori-live.db")
try:
    _PERSIST_SECONDS = int(os.environ.get("TAYORI_PERSIST_SECONDS", "30"))
except ValueError:
    _PERSIST_SECONDS = 30

_db_dir = os.path.dirname(os.path.abspath(DB_PATH))
# デプロイ時の切り分け用：実際に使うDBパスと、そのフォルダが書き込み可能かをログに出す。
print(f"[たより] DB_PATH = {DB_PATH} / フォルダ書込可={os.access(_db_dir, os.W_OK)} "
      f"（TAYORI_DB_PATH={'未設定' if not os.environ.get('TAYORI_DB_PATH') else '設定済'}）", flush=True)
if _LOCAL_CACHE:
    print(f"[たより] ローカルキャッシュDB有効：実行={DB_PATH} ／ 永続={_PERSIST_DB_PATH}"
          f"（{_PERSIST_SECONDS}秒ごと＋終了時に保存）", flush=True)


def _restore_from_durable():
    """起動時：ライブDBが無ければ（＝新しいコンテナ）永続ディスクから復元する。
    ライブが既に在る場合は中身が新しいとみなして上書きしない（取りこぼし防止）。"""
    if not _LOCAL_CACHE:
        return
    try:
        if os.path.exists(_PERSIST_DB_PATH) and not os.path.exists(DB_PATH):
            # 本体に加え、WAL残骸(-wal/-shm)もあれば一緒に運ぶ（過去のWAL実験で永続側がWAL
            # 状態でも、未チェックポイントのコミットを失わないように）。-journalも同様。
            shutil.copy2(_PERSIST_DB_PATH, DB_PATH)
            for ext in ("-wal", "-shm", "-journal"):
                if os.path.exists(_PERSIST_DB_PATH + ext):
                    shutil.copy2(_PERSIST_DB_PATH + ext, DB_PATH + ext)
            print(f"[たより] 起動復元：{_PERSIST_DB_PATH} → {DB_PATH}", flush=True)
    except Exception as e:
        print(f"[たより] 起動復元に失敗（新規DBで起動）: {e}", flush=True)


_persist_lock = threading.Lock()
def _persist_to_durable():
    """ライブDB→永続ディスクへ整合性スナップショット。
    【重要】遅い永続ディスクへの書き込みを、ライブDBのロック保持から完全に切り離す：
      ① ライブ → 高速な /tmp 上の「ステージ」へ整合性コピー（チャンク化＝ライブの読みロックは
         一瞬しか持たない。foregroundのDBアクセスを止めない）。
      ② ステージ(/tmp) → 永続(/var/data) は“ただのファイルコピー＋アトミックrename”。
         これは SQLite のロックを一切介さないので、/var/data が遅く/詰まっても
         ライブDB（＝実行中の読み書き）には一切影響しない。
    （以前は src.backup(dst=/var/data) を一括実行し、遅い書き込み中ずっとライブの読みロックを
      保持→全DBアクセスが固まっていた。その回帰の根治。）"""
    if not _LOCAL_CACHE:
        return False
    if not _persist_lock.acquire(blocking=False):
        return False   # 既に保存中なら重ねない
    stage = DB_PATH + ".persist.tmp"        # 高速ローカル上の中間ファイル
    durtmp = _PERSIST_DB_PATH + ".tmp"      # 永続側の一時ファイル（最後にrename）
    try:
        # ① ライブ → /tmp ステージ（チャンク化でロックを小刻みに解放）
        src = sqlite3.connect(DB_PATH, timeout=30)
        dst = sqlite3.connect(stage, timeout=30)
        try:
            dst.execute("PRAGMA synchronous=OFF")
            with dst:
                src.backup(dst, pages=256, sleep=0.002)
        finally:
            dst.close()
            src.close()
        # ② /tmp ステージ → /var/data（ただのファイルコピー。SQLiteロックを介さない）
        shutil.copyfile(stage, durtmp)
        os.replace(durtmp, _PERSIST_DB_PATH)   # アトミック差し替え
        return True
    except Exception as e:
        for p in (durtmp,):
            try:
                os.remove(p)
            except OSError:
                pass
        print(f"[たより] 永続化に失敗（次回再試行）: {e}", flush=True)
        return False
    finally:
        try:
            os.remove(stage)
        except OSError:
            pass
        _persist_lock.release()


# 終了時（gunicornのSIGTERM・通常終了）に必ず最後の状態を永続化する。
if _LOCAL_CACHE:
    atexit.register(_persist_to_durable)

    def _persist_on_signal(signum, frame):
        _persist_to_durable()
        # 既定の終了動作へ戻して、gunicornのgraceful停止を妨げない
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    try:
        signal.signal(signal.SIGTERM, _persist_on_signal)
    except (ValueError, OSError):
        pass  # メインスレッド以外で読み込まれた場合は無視（atexitと定期保存で担保）


def _load_dotenv():
    """同じフォルダの .env を読み込んで os.environ に流し込む（外部ライブラリ不要）。
    これが無いと、鍵は『export したのと同じターミナルで起動』しないと効かず、
    ダブルクリック起動や別ウィンドウ起動だと毎回スルーされて定型文に落ちる。
    すでに環境変数で設定済みのキーは上書きしない（明示の export を尊重）。"""
    path = os.path.join(APP_DIR, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                # 空行・コメント・KEY=VALUE 以外は無視
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                # `export KEY=...` 形式も許容する
                if key.startswith("export "):
                    key = key[len("export "):].strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


_load_dotenv()

app = Flask(__name__)


def _load_secret():
    env = os.environ.get("TAYORI_SECRET")
    if env:
        return env
    key_path = os.path.join(APP_DIR, ".secret_key")
    if os.path.exists(key_path):
        with open(key_path) as fh:
            return fh.read().strip()
    key = secrets.token_hex(32)
    try:
        with open(key_path, "w") as fh:
            fh.write(key)
    except OSError:
        pass
    return key


app.secret_key = _load_secret()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("TAYORI_PRODUCTION")),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    MAX_CONTENT_LENGTH=16 * 1024 * 1024,
)


# ------------------------------------------------ パフォーマンス計測 & 応答最適化
# 「急に重い」を後から特定できるよう各リクエストの処理時間を測る。combined アクセス
# ログにはレスポンス時間が無いため、ここで自前に出す（Renderのログで grep できる）。
@app.before_request
def _perf_start():
    g._t0 = time.monotonic()
    # POST等の書き込み系は「到達した瞬間」も出す。これで『送信中のまま固まる』が
    #  ・[recv] が出ない → リクエストがアプリに届いていない（経路/プロキシ or ワーカー詰まり）
    #  ・[recv] は出るが [slow]/応答が出ない → ハンドラ内（commitのfsync等）で停止
    # と一発で切り分けられる。
    if request.method != "GET":
        print(f"[たより][recv] {request.method} {request.path}", flush=True)


# gzip で縮むテキスト系の Content-Type だけ圧縮する。
_COMPRESSIBLE = ("text/html", "text/css", "text/plain", "text/javascript",
                 "application/javascript", "application/json", "image/svg+xml")
_GZIP_MIN_BYTES = 1024  # これ未満は圧縮しても割に合わない


@app.after_request
def _finalize_response(resp):
    try:
        ctype = (resp.content_type or "").split(";")[0].strip()
        # send_file 等のストリーミング応答は触らない（get_data で壊れる）
        if not resp.direct_passthrough and request.method in ("GET", "HEAD"):
            # 1) 大きなHTML（index.html は約95KB）に ETag を付け、変わらなければ 304 で
            #    本文ゼロで返す。毎回95KBをフル送出していたのを再訪問では止める。
            if ctype == "text/html" and resp.status_code == 200:
                resp.add_etag()
                resp.headers.setdefault("Cache-Control", "no-cache")
                resp.make_conditional(request)  # If-None-Match 一致なら 304 化

            # 2) gzip 圧縮（クライアント対応・圧縮で得する・未圧縮のときだけ）
            if (resp.status_code == 200
                    and ctype in _COMPRESSIBLE
                    and "gzip" in (request.headers.get("Accept-Encoding") or "")
                    and "Content-Encoding" not in resp.headers):
                data = resp.get_data()
                if len(data) >= _GZIP_MIN_BYTES:
                    resp.set_data(gzip.compress(data, compresslevel=6))
                    resp.headers["Content-Encoding"] = "gzip"
            resp.headers["Vary"] = "Accept-Encoding"
    except Exception as e:
        # 最適化が失敗しても本来の応答は壊さない
        print(f"[たより] 応答最適化スキップ: {e}", flush=True)

    # 処理時間ログ（重い応答だけ＝200ms超を出す。常時出すとログが膨らむため）
    try:
        dt = (time.monotonic() - getattr(g, "_t0", time.monotonic())) * 1000.0
        if dt >= 200:
            print(f"[たより][slow] {dt:6.0f}ms {request.method} {request.path}"
                  f" -> {resp.status_code}", flush=True)
    except Exception:
        pass
    return resp


# 天気・メール送信などの外部通信を使うか。
# PythonAnywhere の無料プランは外部通信が遮断されるため、既定で OFF にしておく。
# 有料プラン等で天気/メールを使いたいときは環境変数 TAYORI_ENABLE_NETWORK=1 を設定する。
NETWORK_ENABLED = bool(os.environ.get("TAYORI_ENABLE_NETWORK"))

# メールに載せる開封リンクの基準URL。
# 公開時は環境変数で自分の公開URLを設定する（末尾のスラッシュは付けない）:
#   export TAYORI_BASE_URL="https://ユーザー名.pythonanywhere.com"
# 未設定ならローカル用にしておく（リンクは届くがローカルでしか開けない）。
BASE_URL = (os.environ.get("TAYORI_BASE_URL") or "http://127.0.0.1:5000").rstrip("/")


# ---------------------------------------------------------------- DB
_wal_ready = False  # WALモードはDBにつき一度設定すれば永続する
# WALは「読み書きが互いをブロックしない」点で理想的だが、共有メモリ(-shm)を使うため
# overlayfs 等の一部ファイルシステムでは PRAGMA journal_mode=WAL がハングする危険がある
# （Renderのフォールバック先など）。安全のため既定はオフ。永続ディスク等でだけ
# TAYORI_SQLITE_WAL=1 で明示的に有効化する。ロック対策の主役は busy_timeout＋下の直列化。
_USE_WAL = os.environ.get("TAYORI_SQLITE_WAL") == "1"

# ロック対策は busy_timeout に一本化する。アプリ側で execute/commit をリトライする方式は、
# busy_timeout(待ち) × リトライ回数 で待ち時間が掛け算になり、ロック保持者が長いと
# 書き込みが何十秒もハングしてワーカーを食い潰すため採用しない（保持型ロックはデッドロックの
# 危険があり、これも不可）。並行書き込みの主因だった「オンボ保存の多重送信」はフロント側の
# 多重送信ガードで断つ。
# busy_timeout は 15秒。Renderの永続ディスクは fsync が遅く、通知スレッド(30秒ごとの
# UPDATE)とリクエストの書き込みが少し重なるだけで、5秒では待ちきれず 'database is locked'
# →500 になっていた（register の db.commit() が代表例）。リトライではなく「単一の待ち上限」
# なので、競合は最大15秒の短い待ちに変わるだけで、待ちが掛け算で膨らむ過去の回帰は起きない。
_BUSY_TIMEOUT_MS = int(os.environ.get("TAYORI_BUSY_TIMEOUT_MS", "15000"))

# fsync の強さ。既定は OFF。
#  理由：ライブDBは高速ローカル(/tmp)に置き、耐久性は定期スナップショット(persist)が担保する
#  設計なので、commitごとの fsync は不要。OFF にすることで「遅いディスクの fsync 待ちで
#  書き込みが詰まる→全read/writeをブロック→送信中で固まる」を根本から断つ。
#  失うのは最大「最後の persist 以降ぶん（既定30秒）」で、これは fsync を切らなくても
#  ローカルキャッシュ構成では同じ。必要なら TAYORI_SQLITE_SYNC=NORMAL / FULL に変更可。
# 接続ごとの設定なので _connect で毎回適用する。
_SYNC_MODE = (os.environ.get("TAYORI_SQLITE_SYNC", "OFF") or "OFF").upper()
if _SYNC_MODE not in ("OFF", "NORMAL", "FULL"):
    _SYNC_MODE = "OFF"

# プロセス内グローバル書き込みロック。worker は1個なので、SQLite に書くスレッドを
# 「常に1つだけ」に直列化すれば、別接続どうしの衝突＝'database is locked' は原理的に起きない
# （待つのは Python のロックで、SQLite のロックではない）。通知スレッドとリクエストの
# 書き込み（register/onboarding 等）で同じロックを取り、競合を根本から断つ。リトライではなく
# 「短時間だけ握って離す」ので、過去にハングを招いた保持型ロックとは別物（書き込みは一瞬）。
_WRITE_LOCK = threading.RLock()


def _connect():
    """SQLite接続を統一設定で開く。
    ・timeout / busy_timeout=15秒：瞬間〜数秒のロック待ちを吸収する（Renderの遅いfsyncと
      通知スレッドの書き込みが重なっても 'database is locked' で即死しないように）。
    ・WALは TAYORI_SQLITE_WAL=1 のときだけ（FS非対応でのハングを避けるため既定オフ）。"""
    global _wal_ready
    _t0 = time.monotonic()
    conn = sqlite3.connect(DB_PATH, timeout=_BUSY_TIMEOUT_MS / 1000.0)
    _dt = (time.monotonic() - _t0) * 1000.0
    if _dt > 300:  # 接続自体が遅い＝ディスク/ロックの問題を可視化（一時診断）
        print(f"[たより][connect] sqlite3.connect が {_dt:.0f}ms（path={DB_PATH}）", flush=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        # synchronous は「接続ごと」の設定なので毎回適用する（既定 OFF＝fsync遅延を回避）。
        conn.execute(f"PRAGMA synchronous={_SYNC_MODE}")
        if _USE_WAL:
            # journal_mode=WAL はDBファイルに永続するので一度設定すれば足りる。
            # ※ただしRenderの永続ディスクでは -shm の mmap で接続がハングする既知問題あり。
            if not _wal_ready:
                conn.execute("PRAGMA journal_mode=WAL")
                _wal_ready = True
    except sqlite3.Error as e:
        print(f"[たより] SQLite PRAGMA設定に失敗（続行します）: {e}", flush=True)
    return conn


def get_db():
    if "db" not in g:
        g.db = _connect()
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# パスワードハッシュは pbkdf2 を使う。Werkzeug 3.x の既定 scrypt は1回につき約32MBを
# 確保するメモリハード方式で、Renderのような小メモリ(512MB)・1ワーカー環境では
# ログイン/登録のたびにメモリスパイク→ワーカーがOOMで落ち、全リクエスト無応答→再起動の
# フラッピングを起こす。pbkdf2 はメモリをほぼ使わずCPUのみなのでスパイクしない。
# 旧scryptハッシュも check_password_hash 側が方式を自動判別するため、そのまま検証可能。
# イテレーションは env で調整可（既定10万＝低価格インスタンスでログインが詰まらない妥協値）。
# 公開規模が見えてきたら 200000 へ戻す/argon2化を推奨（TAYORI_PBKDF2_ITERS で即変更可）。
try:
    _PBKDF2_ITERS = int(os.environ.get("TAYORI_PBKDF2_ITERS", "100000"))
except ValueError:
    _PBKDF2_ITERS = 100000
_PW_METHOD = f"pbkdf2:sha256:{_PBKDF2_ITERS}"


def _hash_pw(pw):
    return generate_password_hash(pw, method=_PW_METHOD)


def _normalize_journal_mode():
    """ジャーナルモードを起動時に意図した状態へ寄せる。
    ・既定（WAL未指定）：rollback(DELETE) に戻す。書込不可FSへフォールバックした際の
      -shm mmap 失敗による『disk I/O error』を避けるため。
    ・TAYORI_SQLITE_WAL=1：WAL のままにする（DELETEへ戻さない）。永続ディスク上では
      WALが読み書きを互いにブロックせず、commitごとのfsync停止も避けられる＝本命の対策。
    モードはDBファイルに永続記録されるため、ここで明示的に揃える必要がある。
    それでも壊れている場合のみ、最終手段として -wal/-shm を除去して本体だけで起動する。"""
    try:
        c = sqlite3.connect(DB_PATH, timeout=15)
        try:
            mode = (c.execute("PRAGMA journal_mode").fetchone() or [""])[0]
            if _USE_WAL and str(mode).lower() != "wal":
                newmode = (c.execute("PRAGMA journal_mode=WAL").fetchone() or [""])[0]
                c.execute("PRAGMA synchronous=NORMAL")
                print(f"[たより] DBを{newmode}へ切替（読書ブロック解消＋fsync停止対策・TAYORI_SQLITE_WAL=1）", flush=True)
            elif not _USE_WAL and str(mode).lower() == "wal":
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # WAL→本体へ統合
                newmode = (c.execute("PRAGMA journal_mode=DELETE").fetchone() or [""])[0]
                print(f"[たより] DBをWAL→{newmode}へ戻しました（永続ディスクのdisk I/O error対策）", flush=True)
            # 読めることを実検証
            c.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        finally:
            c.close()
    except sqlite3.Error as e:
        print(f"[たより] journal_mode正規化に失敗: {e} → -wal/-shm の除去を試みます", flush=True)
        for ext in ("-wal", "-shm"):
            try:
                os.remove(DB_PATH + ext)
                print(f"[たより] 残存ファイル {os.path.basename(DB_PATH)}{ext} を除去しました", flush=True)
            except OSError:
                pass


def init_db():
    _restore_from_durable()    # ライブDBが無ければ永続ディスクから復元（高速ローカルへ）
    _normalize_journal_mode()  # 壊れたWAL状態を先に復旧してから通常のスキーマ初期化へ
    db = _connect()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id        TEXT PRIMARY KEY,
            username  TEXT UNIQUE NOT NULL,
            pw_hash   TEXT NOT NULL,
            created   TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS letters (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            poem         TEXT,
            photo        TEXT,
            voice        TEXT,
            sent_date    TEXT NOT NULL,
            arrive_date  TEXT NOT NULL,
            arrive_label TEXT,
            arrive_hidden INTEGER DEFAULT 0,
            opened       INTEGER DEFAULT 0,
            emos         TEXT DEFAULT '[]',
            from_reply   INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS thread (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            letter_id  TEXT NOT NULL,
            who        TEXT NOT NULL,
            text       TEXT NOT NULL,
            created    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS drafts (
            id      TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            poem    TEXT,
            photo   TEXT,
            voice   TEXT,
            created TEXT NOT NULL
        );
        """
    )
    # --- 新機能用カラムのマイグレーション ---
    # ALTER は1文ずつ try する（途中で1つ既存だと残りがスキップされるのを防ぐ）
    for stmt in (
        "ALTER TABLE letters ADD COLUMN arrive_at TEXT",
        "ALTER TABLE letters ADD COLUMN weather_lock TEXT",
        "ALTER TABLE letters ADD COLUMN seal_env TEXT",
        "ALTER TABLE letters ADD COLUMN open_env TEXT",
        "ALTER TABLE letters ADD COLUMN notified INTEGER DEFAULT 0",   # 届いたメール通知を送ったか
        "ALTER TABLE letters ADD COLUMN weather_event TEXT",           # 天気の出来事待ち伏せ(snow/rain/hot/cold)
        "ALTER TABLE letters ADD COLUMN weather_met_at TEXT",          # 天気条件が満たされて「届いた」時刻
        "ALTER TABLE users ADD COLUMN email TEXT",                      # リマインド送信先
        "ALTER TABLE users ADD COLUMN last_lat TEXT",                   # 最後にいた緯度（天気待ち伏せ用）
        "ALTER TABLE users ADD COLUMN last_lon TEXT",                   # 最後にいた経度
        "ALTER TABLE letters ADD COLUMN opened_at TEXT",                # 初めて開封した日時（届いた時の自分の記録）
        "ALTER TABLE letters ADD COLUMN open_mood TEXT",                # 開封時の気分タグ
        "ALTER TABLE letters ADD COLUMN reflect_count INTEGER DEFAULT 0", # 何度この便りと向き合ったか
        "ALTER TABLE letters ADD COLUMN stamp TEXT",                    # 封をする時に選んだ切手（儀式の記録）
        "ALTER TABLE thread ADD COLUMN created_at TEXT",                # スレッド発言の正確な日時
        "ALTER TABLE thread ADD COLUMN kind TEXT",                      # 発言種別: reply/question/ai
        # --- メール確認・配信停止・再試行制御 ---
        "ALTER TABLE users ADD COLUMN email_token TEXT",                # 確認リンク用トークン
        "ALTER TABLE users ADD COLUMN email_token_at TEXT",             # トークン発行時刻（有効期限判定）
        "ALTER TABLE users ADD COLUMN unsub_token TEXT",                # 配信停止用の安定トークン
        "ALTER TABLE users ADD COLUMN notify_enabled INTEGER DEFAULT 1",# 通知メールを受け取るか
        "ALTER TABLE letters ADD COLUMN notify_attempts INTEGER DEFAULT 0", # 通知の送信試行回数
        "ALTER TABLE letters ADD COLUMN notify_failed INTEGER DEFAULT 0",   # 規定回数失敗して諦めたか
        # --- 30の質問オンボーディング ---
        "ALTER TABLE users ADD COLUMN onboarding TEXT",                 # 30の質問への回答(JSON: {qid: answer})
    ):
        try:
            db.execute(stmt)
        except sqlite3.OperationalError:
            pass  # 既に追加されていれば無視

    # email_verified は特別扱い：初回追加時だけ、既存のメール登録済みユーザーを
    # 「確認済み」として引き継ぐ（後方互換。毎起動で上書きしないよう例外で初回判定）。
    try:
        db.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0")
        db.execute("UPDATE users SET email_verified=1 WHERE email IS NOT NULL AND email<>''")
    except sqlite3.OperationalError:
        pass  # 既に追加済み

    # onboarded も同様に「初回追加時だけ既存ユーザーを完了扱い」で引き継ぐ。
    # 既存ユーザーに突然30の質問を出さないための後方互換。新規登録は register が 0 を入れる。
    try:
        db.execute("ALTER TABLE users ADD COLUMN onboarded INTEGER DEFAULT 0")
        db.execute("UPDATE users SET onboarded=1")
    except sqlite3.OperationalError:
        pass  # 既に追加済み

    if db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 0:
        # 管理ダッシュボードで「保護対象」として扱う管理者アカウント
        db.execute(
            "INSERT INTO users (id,username,pw_hash,created,email) VALUES (?,?,?,?,?)",
            (secrets.token_hex(8), "admin", _hash_pw("admin.welcometotayori"),
             datetime.now().isoformat(), None),
        )
        demo_id = secrets.token_hex(8)
        db.execute(
            "INSERT INTO users (id,username,pw_hash,created) VALUES (?,?,?,?)",
            (demo_id, "demo", _hash_pw("demo1234"), datetime.now().isoformat()),
        )
        today = date.today()
        # 過去のデータにも仮の時間を付与
        s1_arrive = (today - timedelta(days=5)).isoformat() + "T09:00:00"
        s2_arrive = (today - timedelta(days=30)).isoformat() + "T09:00:00"
        s3_arrive = (today + timedelta(days=88)).isoformat() + "T09:00:00"
        
        env_seal_demo = json.dumps({"temp": 12.5, "condition": "rain", "tag": "cold"})
        env_open_demo = json.dumps({"temp": 28.0, "condition": "clear", "tag": "hot"})

        seed = [
            dict(id=secrets.token_hex(8), user_id=demo_id, poem="儚ければ儚いほど、\n完璧な青春だ。", photo=None, voice=None,
                 sent_date=(today - timedelta(days=210)).isoformat(), arrive_date=s1_arrive[:10], arrive_at=s1_arrive,
                 arrive_label="半年後", arrive_hidden=0, opened=0, emos=json.dumps(["静か"], ensure_ascii=False), from_reply=0,
                 weather_event=None, seal_env=env_seal_demo, open_env=None),
            dict(id=secrets.token_hex(8), user_id=demo_id, poem="毎日をお皿のように積み重ねて、\n割らないように工夫してる。", photo=None, voice=None,
                 sent_date=(today - timedelta(days=400)).isoformat(), arrive_date=s2_arrive[:10], arrive_at=s2_arrive,
                 arrive_label="1年後", arrive_hidden=0, opened=1, emos=json.dumps(["懐かしい", "誇らしい"], ensure_ascii=False), from_reply=0,
                 weather_event=None, seal_env=env_seal_demo, open_env=env_open_demo),
            dict(id=secrets.token_hex(8), user_id=demo_id, poem="（次に雪が降る日に、開きます）", photo=None, voice=None,
                 sent_date=(today - timedelta(days=3)).isoformat(), arrive_date=(today - timedelta(days=3)).isoformat(),
                 arrive_at=(today - timedelta(days=3)).isoformat() + "T09:00:00",
                 arrive_label="次の雪の日に", arrive_hidden=0, opened=0, emos=json.dumps([], ensure_ascii=False), from_reply=0,
                 weather_event="snow", seal_env=json.dumps({"temp": 5.0, "condition": "snow", "tag": "cold"}), open_env=None),
        ]
        for s in seed:
            db.execute(
                """INSERT INTO letters
                   (id,user_id,poem,photo,voice,sent_date,arrive_date,arrive_at,arrive_label,arrive_hidden,opened,emos,from_reply,weather_event,seal_env,open_env)
                   VALUES (:id,:user_id,:poem,:photo,:voice,:sent_date,:arrive_date,:arrive_at,:arrive_label,:arrive_hidden,:opened,:emos,:from_reply,:weather_event,:seal_env,:open_env)""",
                s,
            )
    # --- admin アカウントの担保（既存DBにも反映） ---
    # 環境変数 TAYORI_ADMIN_PASSWORD があればそれを、無ければ既定値を使う。
    admin_pw = os.environ.get("TAYORI_ADMIN_PASSWORD", "admin.welcometotayori")
    admin_row = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    if admin_row is None:
        db.execute(
            "INSERT INTO users (id,username,pw_hash,created,email) VALUES (?,?,?,?,?)",
            (secrets.token_hex(8), "admin", _hash_pw(admin_pw),
             datetime.now().isoformat(), None),
        )
    else:
        # 既にいる場合はパスワードを希望のものに揃える
        db.execute("UPDATE users SET pw_hash=? WHERE username='admin'",
                   (_hash_pw(admin_pw),))

    # 配信停止トークンを持たないユーザーに発行（seed/admin 含む全員ぶん埋め戻す）
    for r in db.execute("SELECT id FROM users WHERE unsub_token IS NULL OR unsub_token=''").fetchall():
        db.execute("UPDATE users SET unsub_token=? WHERE id=?", (secrets.token_urlsafe(16), r["id"]))

    db.commit()
    db.close()
def current_user():
    u = session.get("uid")
    if not u:
        return None
    return get_db().execute(
        "SELECT id,username,email,email_verified,onboarded FROM users WHERE id=?", (u,)
    ).fetchone()


ONBOARDING_QUESTIONS = [
    "あなたが生まれ育った町は、どんな場所でしたか。よく覚えている風景をひとつ。",
    "子どもの頃、いちばん長い時間を過ごした場所はどこですか。",
    "今でも鮮明に思い出せる、いちばん古い記憶は何ですか。",
    "これまでで一番大きな決断は何でしたか。なぜ、そうしたのですか。",
    "人生が変わったと感じる「転機」は、いつ、何でしたか。",
    "いちばん影響を受けた人は誰ですか。その人から学んだことは。",
    "今、いちばん大切な人は誰ですか。その人との、忘れられない場面を。",
    "これまでで一番つらかった時期は、いつ、どんな状況でしたか。",
    "その時期を、あなたはどうやって乗り越えましたか。",
    "心から誇れる、自分が成し遂げたことは何ですか。",
    "いちばん後悔している選択は何ですか。",
    "ある匂いで、ふいに思い出す記憶はありますか。",
    "何度も聴いた音楽、繰り返し読んだ本はありますか。",
    "遠く離れた場所や、ふだんと違う環境で過ごした時間はありますか。そこで何を感じましたか。",
    "今、打ち込んでいること・学んでいることは何ですか。",
    "今の仕事や役割を、どんな経緯で選びましたか。",
    "毎日の中で、欠かさず続けている習慣はありますか。",
    "最近、心が大きく動いた出来事を、具体的に教えてください。",
    "誰かを支えたり教えたりした経験で、逆に自分が学んだことは。",
    "あなたの言葉や行いが、確かに誰かに届いたと感じた瞬間は。",
    "今、ひそかに抱えている悩みや迷いはありますか。",
    "これだけは譲れない、と思うものは何ですか。それはなぜ。",
    "手元にある、思い出の品はありますか。その由来を。",
    "もう一度行きたい場所、もう一度会いたい人はいますか。",
    "5年前の今ごろ、あなたは何をしていましたか。",
    "これから挑戦したいこと、叶えたい夢は何ですか。",
    "怖いと感じることは何ですか。その怖さは、どこから来ていますか。",
    "自分の性格を、具体的なエピソードとともに表すとしたら。",
    "誰かの体験や記憶を、あなた自身の言葉で残すとしたら、どんな形にしますか。",
    "今日のあなたから、未来のあなたへ、1行だけ。",
]


def _load_onboarding(raw):
    """users.onboarding(JSON文字列) を {qid(int): answer} の dict に復元。"""
    try:
        data = json.loads(raw) if raw else {}
        # キーが文字列で来るので int に寄せる（壊れた値は捨てる）
        return {int(k): v for k, v in data.items() if str(v).strip()}
    except (ValueError, TypeError, AttributeError):
        return {}


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("uid"):
            return jsonify(error="ログインしてください。", auth=False), 401
        return f(*a, **kw)
    return wrapper

def uid():
    return session["uid"]


@app.route("/api/onboarding", methods=["GET"])
@login_required
def api_get_onboarding():
    """30の質問と、自分のこれまでの回答・完了状態を返す。"""
    row = get_db().execute(
        "SELECT onboarding,onboarded FROM users WHERE id=?", (uid(),)
    ).fetchone()
    answers = _load_onboarding(row["onboarding"] if row else None)
    return jsonify(
        questions=[{"id": i, "text": q} for i, q in enumerate(ONBOARDING_QUESTIONS)],
        answers={str(k): v for k, v in answers.items()},
        onboarded=bool(row["onboarded"]) if row else False,
    )


@app.route("/api/onboarding", methods=["POST"])
@login_required
def api_save_onboarding():
    """30の質問への回答を保存する。partial（一部だけ）でも可。
    done=true なら「完了/スキップ済み」にして、以後オンボーディング画面を出さない。"""
    data = request.get_json(force=True)
    incoming = data.get("answers") or {}
    db = get_db()
    row = db.execute("SELECT onboarding FROM users WHERE id=?", (uid(),)).fetchone()
    answers = _load_onboarding(row["onboarding"] if row else None)
    # 既存回答にマージ（空文字で送られた項目は削除＝消したい意図とみなす）
    for k, v in incoming.items():
        try:
            qid = int(k)
        except (ValueError, TypeError):
            continue
        if not (0 <= qid < len(ONBOARDING_QUESTIONS)):
            continue
        text = (str(v) if v is not None else "").strip()[:300]  # 1問300字まで
        if text:
            answers[qid] = text
        else:
            answers.pop(qid, None)
    done = 1 if data.get("done") else 0
    try:
        with _WRITE_LOCK:  # 書き込みを直列化（通知スレッド等と衝突させない）
            db.execute(
                "UPDATE users SET onboarding=?, onboarded=CASE WHEN ?=1 THEN 1 ELSE onboarded END WHERE id=?",
                (json.dumps(answers, ensure_ascii=False), done, uid()),
            )
            db.commit()
    except sqlite3.OperationalError as e:
        print(f"[たより] onboarding 書き込み失敗（再試行可）: {e}", flush=True)
        return jsonify(error="いま少し混み合っています。数秒おいて、もう一度お試しください。"), 503
    now_onboarded = db.execute("SELECT onboarded FROM users WHERE id=?", (uid(),)).fetchone()["onboarded"]
    return jsonify(ok=True, answered=len(answers), onboarded=bool(now_onboarded))

# userid: 英数記号(_ . -) と 日本語(ひらがな・カタカナ・漢字・長音符) を許可
# \u3005々 \u30fcー \u3040-\u30ff かな \u3400-\u9fff CJK統合漢字(拡張A含む) \uff66-\uff9f 半角カナ
USERNAME_RE = re.compile(
    r"^[A-Za-z0-9_.\-"
    r"\u3005\u30fc\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f"
    r"]{2,24}$"
)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

@app.route("/api/register", methods=["POST"])
def api_register():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    email = (data.get("email") or "").strip()
    if not USERNAME_RE.match(username):
        return jsonify(error="名前は2〜24文字で。漢字・かな・英数字と _ . - が使えます。"), 400
    if len(password) < 8:
        return jsonify(error="パスワードは8文字以上にしてください。"), 400
    if email and not EMAIL_RE.match(email):
        return jsonify(error="メールアドレスの形式が正しくありません。"), 400
    # ▼一時診断：どの行で固まるかを特定するパンくず（time付き）。原因確定後に撤去する。
    _t = time.monotonic()
    def _bc(msg):
        print(f"[たより][reg] {(time.monotonic()-_t)*1000:6.0f}ms {msg}", flush=True)
    _bc("start")
    db = get_db()
    _bc("connected")
    if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        return jsonify(error="その名前はもう使われています。"), 409
    _bc("select ok")
    new_id = secrets.token_hex(8)
    pw_hash = _hash_pw(password)  # ハッシュ計算は重いのでロックの外で済ませておく
    _bc("hashed")
    # ロックは無限待ちにしない（保持者がfsync等で固まっても、ここで諦めて503にする）
    got = _WRITE_LOCK.acquire(timeout=20)
    if not got:
        _bc("LOCK TIMEOUT（別の書き込みが20秒以上ロックを保持＝fsync停止の疑い）")
        return jsonify(error="いま混み合っています。数秒おいて、もう一度お試しください。"), 503
    _bc("lock acquired")
    try:
        db.execute(
            "INSERT INTO users (id,username,pw_hash,created,email,unsub_token) VALUES (?,?,?,?,?,?)",
            (new_id, username, pw_hash, datetime.now().isoformat(),
             email or None, secrets.token_urlsafe(16)),
        )
        _bc("insert ok（commit前＝ここから先が長いなら fsync 停止）")
        db.commit()
        _bc("committed")
    except sqlite3.OperationalError as e:
        print(f"[たより] register 書き込み失敗（再試行可）: {e}", flush=True)
        return jsonify(error="いま少し混み合っています。数秒おいて、もう一度お試しください。"), 503
    finally:
        _WRITE_LOCK.release()
    email_pending = False
    if email:
        # 確認メールを送り、確認が済むまでは通知しない
        _issue_email_verification(db, new_id, email, username)
        email_pending = True
    session.permanent = True
    session["uid"] = new_id
    return jsonify(ok=True, username=username, email=email or None,
                   email_verified=False, email_pending=email_pending,
                   onboarded=False)  # 新規登録直後は必ずオンボーディングへ

@app.route("/api/login", methods=["POST"])
def api_login():
    _t = time.monotonic()
    def _bc(msg):
        print(f"[たより][login] {(time.monotonic()-_t)*1000:6.0f}ms {msg}", flush=True)
    _bc("start")
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    db = get_db()
    _bc("connected")
    row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    _bc("selected")
    if not row or not check_password_hash(row["pw_hash"], password):
        return jsonify(error="名前かパスワードが違います。"), 401
    _bc("verified")
    session.permanent = True
    session["uid"] = row["id"]
    keys = row.keys()
    return jsonify(ok=True, username=row["username"],
                   is_admin=(row["username"] == "admin"),  # admin はログイン後に管理画面へ誘導
                   email=row["email"] if "email" in keys else None,
                   email_verified=bool(row["email_verified"]) if "email_verified" in keys else False,
                   onboarded=bool(row["onboarded"]) if "onboarded" in keys else True)

@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("uid", None)
    return jsonify(ok=True)

@app.route("/api/me")
def api_me():
    u = current_user()
    if not u:
        return jsonify(auth=False, weather_enabled=NETWORK_ENABLED)
    return jsonify(auth=True, username=u["username"],
                   email=u["email"] if "email" in u.keys() else None,
                   email_verified=bool(u["email_verified"]) if "email_verified" in u.keys() else False,
                   onboarded=bool(u["onboarded"]) if "onboarded" in u.keys() else True,
                   weather_enabled=NETWORK_ENABLED)


@app.route("/api/email", methods=["POST"])
@login_required
def api_set_email():
    """初期画面でスキップした人が後からメアドを登録／変更／解除できる。
    新しいアドレスを設定したら確認メールを送り、確認が済むまでは通知しない。"""
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip()
    if email and not EMAIL_RE.match(email):
        return jsonify(error="メールアドレスの形式が正しくありません。"), 400
    db = get_db()
    if email:
        _issue_email_verification(db, uid(), email, current_user()["username"])
        # アドレスを変えたので、過去に諦めた便りも新アドレスへ再挑戦できるようにする
        db.execute("UPDATE letters SET notify_attempts=0, notify_failed=0 WHERE user_id=?", (uid(),))
        db.commit()
        return jsonify(ok=True, email=email, email_verified=False, email_pending=True)
    # 空＝通知オフ
    db.execute("UPDATE users SET email=NULL, email_verified=0, email_token=NULL, email_token_at=NULL WHERE id=?", (uid(),))
    db.commit()
    return jsonify(ok=True, email=None, email_verified=False)


# ---------------------------------------------------------------- helpers
def _is_arrived(row):
    keys = row.keys() if hasattr(row, "keys") else []
    # 天気の出来事待ち伏せ便り：weather_met_at が入っていればその時刻に「届いた」
    if "weather_event" in keys and row["weather_event"]:
        met = row["weather_met_at"] if "weather_met_at" in keys else None
        if met:
            return datetime.fromisoformat(met) <= datetime.now()
        return False  # まだ天気が来ていない＝届いていない
    arrive_at = row["arrive_at"] or (row["arrive_date"] + "T00:00:00")
    return datetime.fromisoformat(arrive_at) <= datetime.now()

def letter_to_dict(row, include_thread=True):
    d = dict(row)
    d.pop("user_id", None)
    d["emos"] = json.loads(d.get("emos") or "[]")
    d["arrive_hidden"] = bool(d["arrive_hidden"])
    d["opened"] = bool(d["opened"])
    d["from_reply"] = bool(d["from_reply"])
    d["arrived"] = _is_arrived(row)
    
    # 差分演出のための環境データを復元
    if d.get("seal_env"): d["seal_env"] = json.loads(d["seal_env"])
    if d.get("open_env"): d["open_env"] = json.loads(d["open_env"])
    
    if include_thread:
        rows = get_db().execute(
            "SELECT who,text,created,created_at,kind FROM thread WHERE letter_id=? ORDER BY id",
            (d["id"],)).fetchall()
        d["thread"] = [dict(r) for r in rows]
    return d

def own_letter(lid):
    return get_db().execute("SELECT * FROM letters WHERE id=? AND user_id=?", (lid, uid())).fetchone()

def sealed_meta(row):
    """封の中のメタデータ。中身(詩・写真・声)は絶対に出さない。"""
    keys = row.keys()
    arrive_at = row["arrive_at"] or (row["arrive_date"] + "T00:00:00")
    dt = datetime.fromisoformat(arrive_at)
    wevent = row["weather_event"] if "weather_event" in keys else None
    return {
        "id": row["id"],
        "sent_date": row["sent_date"],
        "arrive_date": row["arrive_date"],
        "arrive_label": row["arrive_label"],
        "arrive_hidden": bool(row["arrive_hidden"]),
        "seconds_left": int((dt - datetime.now()).total_seconds()),
        "weather_event": wevent,                 # 天気待ち伏せ中なら snow/rain/hot/cold
        "waiting_weather": bool(wevent),         # 天気を待っている便りか
        "has_photo": bool(row["photo"]),
        "has_voice": bool(row["voice"]),
        "from_reply": bool(row["from_reply"]),
    }

# ---------------------------------------------------------------- メール通知
def _smtp_config():
    """環境変数からSMTP設定を読む。未設定なら None（→コンソール出力にフォールバック）。

    Gmailの例:
        export TAYORI_SMTP_HOST=smtp.gmail.com
        export TAYORI_SMTP_PORT=587
        export TAYORI_SMTP_USER="あなた@gmail.com"
        export TAYORI_SMTP_PASS="アプリパスワード16桁"
        export TAYORI_MAIL_FROM="たより <あなた@gmail.com>"   # 省略可
    """
    user = os.environ.get("TAYORI_SMTP_USER")
    pw = os.environ.get("TAYORI_SMTP_PASS")
    if not NETWORK_ENABLED or not user or not pw:
        return None  # 外部通信オフ、または未設定 → コンソール出力にフォールバック
    return {
        "host": os.environ.get("TAYORI_SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("TAYORI_SMTP_PORT", "587")),
        "user": user,
        "pw": pw,
        "from": os.environ.get("TAYORI_MAIL_FROM") or formataddr(("たより", user)),
    }


def send_email(to_addr, subject, body):
    """1通送る。SMTP未設定ならコンソールに内容を出す（ローカル開発用フォールバック）。"""
    cfg = _smtp_config()
    if not cfg:
        print("\n―― [メール通知・擬似送信] ――――――――――――")
        print(f"  宛先: {to_addr}")
        print(f"  件名: {subject}")
        print(f"  本文: {body}")
        print("  （SMTPを設定すると実際に送信されます。READMEを参照）")
        print("――――――――――――――――――――――――\n")
        return True
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        # From の表示名（例「たより」）が日本語だと、生文字列のままでは
        # RFC2047エンコードされず、Resend等に拒否されたり文字化けする。
        # parseaddr で「表示名」「アドレス」に分け、formataddr で正しく再構成する。
        from_name, from_addr = parseaddr(cfg["from"])
        msg["From"] = formataddr((from_name, from_addr)) if from_addr else cfg["from"]
        msg["To"] = to_addr
        # 到達率を上げる標準ヘッダ（迷惑メール判定の軽減に効く）。
        # ・Date/Message-ID が無いメールはスパム扱いされやすい。
        # ・Reply-To を差出ドメインに。Message-ID も差出ドメインで採番する。
        msg["Date"] = formatdate(localtime=True)
        _dom = from_addr.split("@")[-1] if from_addr and "@" in from_addr else None
        msg["Message-ID"] = make_msgid(domain=_dom) if _dom else make_msgid()
        if from_addr:
            msg["Reply-To"] = from_addr
        ctx = ssl.create_default_context()
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as s:
            s.starttls(context=ctx)
            s.login(cfg["user"], cfg["pw"])
            s.send_message(msg)
        print(f"[メール送信成功] {to_addr} ← {subject}")
        return True
    except Exception as e:
        print(f"[メール送信失敗] {to_addr}: {e}")
        return False


# ---------------------------------------------------------------- メール確認・配信停止
EMAIL_TOKEN_TTL = timedelta(days=7)   # 確認リンクの有効期限
MAX_NOTIFY_ATTEMPTS = 5               # この回数まで失敗したら、その便りの通知は諦める


def _issue_email_verification(db, user_id, email, username):
    """確認トークンを発行して確認メールを送る。verified は 0 に戻す。
    送信できたかに関わらず、トークン自体は保存する（リンクは有効）。"""
    token = secrets.token_urlsafe(24)
    with _WRITE_LOCK:  # 書き込みを直列化
        db.execute(
            "UPDATE users SET email=?, email_verified=0, email_token=?, email_token_at=?, notify_enabled=1 WHERE id=?",
            (email, token, datetime.now().isoformat(timespec="seconds"), user_id),
        )
        db.commit()
    verify_url = f"{BASE_URL}/verify/{token}"
    subject = "たより — メールアドレスの確認"
    body = (
        f"{username} さんへ。\n"
        "たより の通知メールを、このアドレスで受け取る設定をしました。\n"
        "下のリンクを開いて、確認を完了してください（7日間有効）。\n"
        "確認が済むまで、たよりが届いてもお知らせは送られません。\n"
        f"{verify_url}\n"
        "心当たりがなければ、このメールは無視してください。\n"
        "tayoriー たより\n"
    )
    # 送信はバックグラウンドで行う。SMTP(最大15秒)を登録レスポンスの中で待つと、
    # メール付き登録が遅くなり、Resendが詰まると登録自体が失敗（500）に見えてしまう。
    # トークンは既に保存済みなのでリンクは有効。送信成否はログに出る。
    threading.Thread(target=send_email, args=(email, subject, body), daemon=True).start()
    return True


def _landing_page(title, message, ok=True):
    """確認・配信停止リンクの着地ページ（簡素なHTML）。"""
    color = "#6B8478" if ok else "#B5543A"
    return (
        "<!doctype html><html lang=ja><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{title} — たより</title><style>"
        "body{background:#F2EBDD;color:#3A2E25;font-family:'Hiragino Mincho ProN',serif;"
        "display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:24px}"
        ".card{max-width:380px;text-align:center;background:#EDE3D1;border:1px solid #CBBBA0;"
        "border-radius:4px;padding:36px 28px;box-shadow:0 10px 30px -18px rgba(58,46,37,.5)}"
        "h1{font-size:34px;letter-spacing:.18em;margin:0 0 6px}"
        f".m{{color:{color};font-size:15px;letter-spacing:.05em;line-height:1.95;margin-top:14px}}"
        "a{color:#B5543A}</style></head><body><div class=card><h1>たより</h1>"
        f"<div class=m>{message}</div>"
        f"<p style='margin-top:22px'><a href='{BASE_URL}/'>戻る →</a></p>"
        "</div></body></html>"
    )


@app.route("/verify/<token>")
def verify_email(token):
    """確認リンクの着地点。トークンが有効ならメールアドレスを確認済みにする。"""
    if not re.fullmatch(r"[A-Za-z0-9_\-]{10,80}", token or ""):
        return _landing_page("確認", "リンクが正しくありません。", ok=False), 400
    db = get_db()
    row = db.execute(
        "SELECT id,email_token_at FROM users WHERE email_token=?", (token,)
    ).fetchone()
    if not row:
        return _landing_page("確認", "このリンクは無効か、すでに使われています。", ok=False), 404
    try:
        issued = datetime.fromisoformat(row["email_token_at"]) if row["email_token_at"] else None
    except (TypeError, ValueError):
        issued = None
    if issued and datetime.now() - issued > EMAIL_TOKEN_TTL:
        return _landing_page("確認", "確認リンクの有効期限が切れています。<br>アプリの📧設定からメールを登録し直してください。", ok=False), 410
    db.execute("UPDATE users SET email_verified=1, email_token=NULL, email_token_at=NULL WHERE id=?", (row["id"],))
    db.commit()
    return _landing_page("確認完了", "メールアドレスを確認しました。<br>便りが届く頃に、そっとお知らせが届きます。")


@app.route("/unsubscribe/<token>")
def unsubscribe(token):
    """通知メール内の配信停止リンク。クリックで以後の通知を止める。"""
    if not re.fullmatch(r"[A-Za-z0-9_\-]{8,80}", token or ""):
        return _landing_page("配信停止", "リンクが正しくありません。", ok=False), 400
    db = get_db()
    row = db.execute("SELECT id FROM users WHERE unsub_token=?", (token,)).fetchone()
    if not row:
        return _landing_page("配信停止", "このリンクは無効です。", ok=False), 404
    db.execute("UPDATE users SET notify_enabled=0 WHERE id=?", (row["id"],))
    db.commit()
    return _landing_page("配信停止", "通知メールの配信を停止しました。<br>再開したいときは、アプリの📧設定からメールを登録し直してください。")


def _temp_tag(temp):
    """気温を hot / cold / normal の体感タグに。"""
    return "hot" if temp >= 28 else ("cold" if temp <= 13 else "normal")


def _fetch_weather_open_meteo(lat, lon):
    """Open-Meteo（無料・APIキー不要）で現在天気を取得。"""
    import urllib.request
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
    req = urllib.request.Request(url, headers={"User-Agent": "tayori/1.0"})
    # timeout は余裕をもって8秒。Render等ではワーカー起動直後のコールド初回接続
    # （DNS＋TLSハンドシェイク）が3秒に間に合わず失敗していたため緩和。
    with urllib.request.urlopen(req, timeout=4) as response:
        data = json.loads(response.read().decode())
    cw = data.get("current_weather", {})
    code = cw.get("weathercode", 0)
    temp = cw.get("temperature", 20.0)
    condition = "clear"
    if code in [71, 73, 75, 77, 85, 86]:
        condition = "snow"
    elif code in [51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99]:
        condition = "rain"
    elif code in [45, 48]:
        condition = "fog"
    elif code in [1, 2, 3]:
        condition = "cloud"
    return {"condition": condition, "temp": temp, "tag": _temp_tag(temp)}


def _fetch_weather_owm(lat, lon, api_key):
    """OpenWeatherMap で現在天気を取得。TAYORI_OWM_KEY が設定されている時に使う。
    天候は OWM の id（2xx雷,3xx/5xx雨,6xx雪,7xx霧/もや,800快晴,80x雲）で分類する。"""
    import urllib.request
    url = (f"https://api.openweathermap.org/data/2.5/weather"
           f"?lat={lat}&lon={lon}&units=metric&appid={api_key}")
    req = urllib.request.Request(url, headers={"User-Agent": "tayori/1.0"})
    with urllib.request.urlopen(req, timeout=4) as response:
        data = json.loads(response.read().decode())
    temp = (data.get("main") or {}).get("temp", 20.0)
    wid = ((data.get("weather") or [{}])[0]).get("id", 800)
    if 600 <= wid < 700:            # 6xx 雪
        condition = "snow"
    elif 200 <= wid < 600:         # 2xx雷雨 / 3xx霧雨 / 5xx雨
        condition = "rain"
    elif 700 <= wid < 800:         # 7xx もや・霧・煙など
        condition = "fog"
    elif 801 <= wid < 810:         # 80x 雲
        condition = "cloud"
    else:                          # 800 快晴 ほか
        condition = "clear"
    return {"condition": condition, "temp": temp, "tag": _temp_tag(temp)}


def fetch_weather(lat, lon):
    """緯度経度から現在の天気を取得して (condition, temp, tag) を返す。失敗時 None。
    condition: clear/cloud/rain/snow/fog, tag: hot/cold/normal
    外部通信が無効（無料プラン等）なら即 None を返してハングを防ぐ。

    既定は Open-Meteo（APIキー不要）。環境変数 TAYORI_OWM_KEY を設定すると
    OpenWeatherMap を優先して使う:
        export TAYORI_OWM_KEY="あなたのAPIキー"
    OWM 側で失敗したら Open-Meteo に自動フォールバックする。"""
    if not NETWORK_ENABLED:
        return None
    owm_key = os.environ.get("TAYORI_OWM_KEY")
    if owm_key:
        try:
            return _fetch_weather_owm(lat, lon, owm_key)
        except Exception as e:
            print(f"[天気取得失敗:OWM→Open-Meteoへ] {e}")
    # ワーカー起動直後はネットワークスタックが冷えており、初回の外部接続だけ
    # 即失敗することがある。1回だけ短い間を置いて再試行すると、まず成功する。
    last = None
    for attempt in range(2):
        try:
            return _fetch_weather_open_meteo(lat, lon)
        except Exception as e:
            last = e
            if attempt == 0:
                time.sleep(0.6)
    print(f"[天気取得失敗] {last}")
    return None



def _client_ip():
    """利用者の実IPを取る。Render等のリバースプロキシ下では remote_addr はプロキシのIPに
    なるため、X-Forwarded-For の先頭（＝最初に到達した利用者のIP）を優先する。"""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        # "client, proxy1, proxy2" の形。先頭が本来の利用者IP。
        ip = xff.split(",")[0].strip()
        if ip:
            return ip
    return request.remote_addr or ""


def _ip_geolocate(client_ip=None):
    """ブラウザの位置情報が使えない時の保険。利用者のIPからおおよその緯度経度を得る。
    無料・キー不要の ip-api.com を使う。client_ip を渡すとそのIPの地域を、渡さなければ
    リクエスト元（=サーバ自身）の地域を返す。本番はサーバがSingapore等のデータセンターに
    なるため、必ず利用者IPを渡すこと。精度は市区町村レベル。失敗時 None。"""
    if not NETWORK_ENABLED:
        return None
    import urllib.request
    # プライベートIP/ループバックは ip-api で引けない（ローカル開発時）。その場合は
    # IP無指定でフォールバック＝外向きIP＝開発者の地域、で従来通り動く。
    def _is_public(ip):
        return ip and not (ip.startswith(("10.", "127.", "192.168.", "172.16.",
                                          "172.17.", "172.18.", "172.19.", "172.2",
                                          "172.30.", "172.31.", "::1", "fc", "fd"))
                           or ip == "localhost")
    target = client_ip if _is_public(client_ip) else ""
    try:
        url = f"http://ip-api.com/json/{target}?fields=status,lat,lon,city"
        with urllib.request.urlopen(url, timeout=4) as r:
            d = json.loads(r.read().decode())
        if d.get("status") == "success" and d.get("lat") is not None:
            return d["lat"], d["lon"], d.get("city")
    except Exception as e:
        print(f"[IP位置推定失敗] {e}", flush=True)
    return None


def _weather_matches(event, wx):
    """便りの待ち伏せ条件 event を、今の天気 wx が満たすか。"""
    if not wx:
        return False
    if event == "snow":
        return wx["condition"] == "snow"
    if event == "rain":
        return wx["condition"] == "rain"
    if event == "hot":
        return wx["tag"] == "hot"
    if event == "cold":
        return wx["tag"] == "cold"
    return False


def _check_weather_events():
    """天気待ち伏せ中の便りを、ユーザーの最終位置の今の天気で判定。
    条件が合致したら weather_met_at を打って「届いた」状態にする。"""
    if not NETWORK_ENABLED:
        return  # 外部通信オフなら天気判定はできないのでスキップ
    db = _connect()
    try:
        rows = db.execute(
            """SELECT l.id AS lid, l.weather_event AS event, l.arrive_at AS arrive_at,
                      u.last_lat AS lat, u.last_lon AS lon
               FROM letters l JOIN users u ON u.id = l.user_id
               WHERE l.weather_event IS NOT NULL AND l.weather_event<>''
                 AND (l.weather_met_at IS NULL OR l.weather_met_at='')"""
        ).fetchall()
        # 位置ごとに天気を1回だけ取得（同じ場所の便りで無駄打ちしない）
        wx_cache = {}
        now = datetime.now()
        for r in rows:
            # 封じてすぐは待ち伏せ開始前（arrive_at＝待ち伏せ開始時刻として使う）
            try:
                if r["arrive_at"] and datetime.fromisoformat(r["arrive_at"]) > now:
                    continue
            except ValueError:
                pass
            if not r["lat"] or not r["lon"]:
                continue  # 位置不明なら判定できない
            key = (r["lat"], r["lon"])
            if key not in wx_cache:
                wx_cache[key] = fetch_weather(r["lat"], r["lon"])
            wx = wx_cache[key]
            if _weather_matches(r["event"], wx):
                with _WRITE_LOCK:  # リクエストの書き込みと直列化
                    db.execute("UPDATE letters SET weather_met_at=? WHERE id=?",
                               (now.isoformat(timespec="seconds"), r["lid"]))
                    db.commit()
                print(f"[天気待ち伏せ成立] {r['event']} → 便り {r['lid']} が届きました")
    except Exception as e:
        print(f"[天気待ち伏せチェックでエラー] {e}")
    finally:
        db.close()


def _check_and_notify():
    """届いたばかりで、まだ通知していない便りを探してメールを送る。
    バックグラウンドスレッドから定期的に呼ばれる。"""
    db = _connect()
    try:
        now = datetime.now()
        # 送る条件：未通知・諦めてない便り × 確認済みで通知ONのメール持ちユーザー
        rows = db.execute(
            """SELECT l.id AS lid, l.arrive_at, l.arrive_date, l.arrive_label,
                      l.weather_event AS wevent, l.weather_met_at AS wmet,
                      COALESCE(l.notify_attempts,0) AS attempts,
                      u.email AS email, u.username AS username, u.unsub_token AS unsub
               FROM letters l JOIN users u ON u.id = l.user_id
               WHERE COALESCE(l.notified,0)=0
                 AND COALESCE(l.notify_failed,0)=0
                 AND u.email IS NOT NULL AND u.email<>''
                 AND COALESCE(u.email_verified,0)=1
                 AND COALESCE(u.notify_enabled,1)=1"""
        ).fetchall()
        for r in rows:
            # 天気待ち伏せ便りは weather_met_at が入って初めて「届いた」
            if r["wevent"]:
                if not r["wmet"]:
                    continue
                try:
                    if datetime.fromisoformat(r["wmet"]) > now:
                        continue
                except ValueError:
                    continue
            else:
                arrive_at = r["arrive_at"] or (r["arrive_date"] + "T00:00:00")
                try:
                    if datetime.fromisoformat(arrive_at) > now:
                        continue  # まだ届いてない
                except ValueError:
                    continue
            open_url = f"{BASE_URL}/open/{r['lid']}"
            subject = "たより — 便りが、届きました"
            body = (
                f"{r['username']} さんへ。\n"
                "過去のあなたが封をしたたよりが、いま届きました。\n"
                "封の中身は、まだあなたも見ていません。\n"
                "下のリンクをひらいて、封蝋をそっとほどいてください。\n"
                f"{open_url}\n\n"
                "tayori ーたより\n"
            )
            if send_email(r["email"], subject, body):
                with _WRITE_LOCK:  # リクエストの書き込みと直列化
                    db.execute("UPDATE letters SET notified=1 WHERE id=?", (r["lid"],))
                    db.commit()
            else:
                # 失敗したら試行回数を増やし、上限に達したら諦める（無限リトライ防止）
                attempts = r["attempts"] + 1
                failed = 1 if attempts >= MAX_NOTIFY_ATTEMPTS else 0
                with _WRITE_LOCK:  # リクエストの書き込みと直列化
                    db.execute("UPDATE letters SET notify_attempts=?, notify_failed=? WHERE id=?",
                               (attempts, failed, r["lid"]))
                    db.commit()
                if failed:
                    print(f"[通知あきらめ] 便り {r['lid']} は {attempts} 回失敗したため停止しました")
    except Exception as e:
        print(f"[通知チェックでエラー] {e}")
    finally:
        db.close()


_notify_started = False
def start_notifier(interval=None):
    """一定間隔で「天気待ち伏せ判定」と「届いた便りのメール通知」を回す常駐スレッド。
    間隔は環境変数 TAYORI_CHECK_INTERVAL（秒）で変更可。デモなら 10 などにすると反応が速い。"""
    global _notify_started
    # キルスイッチ：背景処理（天気判定・通知・定期persist）を即停止できる安全弁。
    # 万一バックグラウンドが暴れたら TAYORI_DISABLE_NOTIFIER=1 を設定して再デプロイすれば止まる。
    if os.environ.get("TAYORI_DISABLE_NOTIFIER") == "1":
        print("[たより] 通知ループは TAYORI_DISABLE_NOTIFIER=1 のため停止中", flush=True)
        return
    # モジュールが2つの文脈で読み込まれる等で start_notifier が二重に走っても、
    # プロセス内に通知スレッドが1本しか立たないようにする（背景の書き込み手を増やさない）。
    # _notify_started（グローバル）に加え、実際に生きているスレッド名でも二重起動を防ぐ。
    if _notify_started or any(t.name == "tayori-notifier" and t.is_alive()
                              for t in threading.enumerate()):
        return
    _notify_started = True

    if interval is None:
        try:
            interval = int(os.environ.get("TAYORI_CHECK_INTERVAL", "30"))
        except ValueError:
            interval = 30

    try:
        backup_hours = float(os.environ.get("TAYORI_BACKUP_INTERVAL_HOURS", "24"))
    except ValueError:
        backup_hours = 24.0

    def loop():
        last_backup = 0.0  # 0 のまま＝設定があれば起動後ほどなく1回目を取る
        last_persist = 0.0
        while True:
            _check_weather_events()   # 天気待ち伏せ便りの判定を先に
            _check_and_notify()       # 届いた便りのメール通知
            # ライブDB→永続ディスクへの定期スナップショット（高速ローカル運用の保存）
            try:
                if _LOCAL_CACHE and (time.time() - last_persist) >= _PERSIST_SECONDS:
                    if _persist_to_durable():
                        last_persist = time.time()
            except Exception as e:
                print(f"[たより] 永続化判定でエラー（継続）: {e}", flush=True)
            # オフサイト自動バックアップ（R2/S3設定があるときだけ・日次）
            try:
                if _backup_s3_config() and (time.time() - last_backup) >= backup_hours * 3600:
                    ok = _run_backup_to_s3()
                    # 成功なら次は backup_hours 後。失敗しても最低1時間は空ける
                    #（30秒ごとに再試行するとスナップショットのロックで書き込みを妨げるため）。
                    last_backup = time.time() if ok else (time.time() - backup_hours * 3600 + 3600)
            except Exception as e:
                print(f"[たより] バックアップ判定でエラー（継続）: {e}", flush=True)
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True, name="tayori-notifier")
    t.start()
    _bk = "・オフサイトBK有効" if _backup_s3_config() else ""
    print(f"[たより] 便りのチェックを開始しました（{interval}秒ごと · 天気待ち伏せ＋メール通知{_bk}）")


# ---------------------------------------------------------------- 天気 API
@app.route("/api/weather")
def api_weather():
    """現在地の緯度経度から Open-Meteo で天気を取得（無料・APIキー不要）。
    ログイン中なら、天気待ち伏せ便りの判定用に位置を記録する。"""
    lat, lon = request.args.get("lat"), request.args.get("lon")
    approx, city = False, None

    # 座標が無い（＝ブラウザの位置情報が使えない）ときは、サーバの公開IPから推定する。
    if not lat or not lon:
        if not NETWORK_ENABLED:
            return jsonify(ok=False, disabled=True, error="天気機能は現在オフです")
        ip = _ip_geolocate(_client_ip())
        if not ip:
            return jsonify(ok=False, error="位置を推定できませんでした")
        lat, lon, city = str(ip[0]), str(ip[1]), ip[2]
        approx = True

    # ログイン中ユーザーの最終位置を保存（天気待ち伏せに使う）
    if session.get("uid"):
        try:
            with _WRITE_LOCK:  # 書き込みを直列化
                get_db().execute("UPDATE users SET last_lat=?, last_lon=? WHERE id=?",
                                 (lat, lon, session["uid"]))
                get_db().commit()
        except Exception:
            pass

    # 外部通信が無効（無料プラン等）なら、待たせずに即「天気なし」を返す
    if not NETWORK_ENABLED:
        return jsonify(ok=False, disabled=True, error="天気機能は現在オフです")

    wx = fetch_weather(lat, lon)
    if not wx:
        return jsonify(ok=False, error="天気が取得できませんでした"), 500
    return jsonify(ok=True, temp=wx["temp"], condition=wx["condition"], temp_tag=wx["tag"],
                   approx=approx, city=city)


@app.route("/api/locate", methods=["POST"])
@login_required
def api_locate():
    """位置だけ記録する（受信画面を開いたタイミングなどで呼ぶ）。"""
    data = request.get_json(force=True)
    lat, lon = data.get("lat"), data.get("lon")
    if lat is None or lon is None:
        return jsonify(error="位置がありません"), 400
    with _WRITE_LOCK:  # 書き込みを直列化
        get_db().execute("UPDATE users SET last_lat=?, last_lon=? WHERE id=?",
                         (str(lat), str(lon), uid()))
        get_db().commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- pages
@app.route("/")
def index():
    return render_template("index.html", open_letter_id="")

@app.route("/open/<lid>")
def open_letter_page(lid):
    """メールの開封リンクの着地点。トップ画面を出し、ログイン後にこの便りへ誘導する。
    （ログインしていなければ、ログイン後に自動でこの便りを開きにいく）"""
    # lid はテンプレートに埋め込むだけ。安全のため英数字に限定。
    safe = lid if re.fullmatch(r"[A-Za-z0-9]{1,32}", lid or "") else ""
    return render_template("index.html", open_letter_id=safe)

# ---------------------------------------------------------------- letters
@app.route("/api/letters")
@login_required
def api_letters():
    rows = get_db().execute("SELECT * FROM letters WHERE user_id=? ORDER BY sent_date DESC, id DESC", (uid(),)).fetchall()
    received, in_transit = [], []
    for r in rows:
        if _is_arrived(r):
            received.append(letter_to_dict(r))
        else:
            in_transit.append(sealed_meta(r))

    # 受信便りの並び：
    #  ① まだ開けていない「新しく届いた便り」を一番上に（到着が新しいものほど上）。
    #  ② 開封済みは「最近ふれた順」（開封時刻と対話の最新メッセージの新しい方）で続ける。
    # これで、開けるようになった新着がいつも最上部に来る。
    def _sort_key(d):
        new = not d.get("opened")   # 届いたがまだ開けていない＝新着
        if new:
            t = d.get("arrive_at") or ((d.get("arrive_date") or "") + "T00:00:00")
        else:
            t = d.get("opened_at") or ""
            th = d.get("thread") or []
            if th:
                t = max(t, th[-1].get("created_at") or "")
            t = t or (d.get("sent_date") or "")
        return (1 if new else 0, t)
    received.sort(key=_sort_key, reverse=True)
    return jsonify(received=received, in_transit=in_transit)


@app.route("/api/letters", methods=["POST"])
@login_required
def api_create_letter():
    data = request.get_json(force=True)
    poem = (data.get("poem") or "").strip()[:80]  # 80字制約
    photo = data.get("photo")
    voice = data.get("voice")
    if not poem and not photo and not voice:
        return jsonify(error="写真かことば、声をひとつ。"), 400

    # 保険：写真・音声のサイズ上限（base64の文字数）。クライアントで圧縮しているが、
    # 圧縮失敗時のフォールバックや不正クライアントから巨大データが来ても、永続ディスクを
    # 守るためサーバ側でも弾く。写真は約3MB、音声は約4MB相当まで。
    if photo and len(photo) > 4_000_000:
        return jsonify(error="写真が大きすぎます。もう少し小さい画像でお願いします。"), 413
    if voice and len(voice) > 5_500_000:
        return jsonify(error="音声が長すぎます。短く録り直してください。"), 413
    
    arrive_at = data.get("arrive_at")
    try:
        dt = datetime.fromisoformat(arrive_at)
        arrive_date = dt.date().isoformat()
    except (TypeError, ValueError):
        return jsonify(error="届く日時が正しくありません。"), 400

    weather_event = data.get("weather_event")  # snow/rain/hot/cold or None
    # デモ用：今日でも送れる。「今より後」でないと届く前に開いてしまうので過去だけ弾く。
    # ただし天気待ち伏せ便りは「封じた今から天気を待つ」ので、過去ガードは適用しない。
    if not weather_event and dt <= datetime.now() - timedelta(minutes=1):
        return jsonify(error="届く日時は今より後にしてください。"), 400

    lid = secrets.token_hex(8)
    seal_env = json.dumps(data.get("seal_env")) if data.get("seal_env") else None
    stamp = (data.get("stamp") or "")[:16] or None  # 封をする時に選んだ切手

    get_db().execute(
        """INSERT INTO letters
           (id,user_id,poem,photo,voice,sent_date,arrive_date,arrive_at,arrive_label,arrive_hidden,opened,emos,from_reply,weather_event,seal_env,stamp)
           VALUES (?,?,?,?,?,?,?,?,?,?,0,'[]',?,?,?,?)""",
        (lid, uid(), poem, photo, voice, date.today().isoformat(), arrive_date, arrive_at,
         data.get("arrive_label", ""), 1 if data.get("arrive_hidden") else 0,
         1 if data.get("from_reply") else 0, weather_event, seal_env, stamp),
    )
    get_db().commit()
    return jsonify(id=lid, ok=True)


@app.route("/api/letters/<lid>/open", methods=["POST"])
@login_required
def api_open_letter(lid):
    row = own_letter(lid)
    if not row:
        return jsonify(error="便りが見つかりません。"), 404
    if not _is_arrived(row):
        return jsonify(error="まだ封の中です。届く日まで待ってください。"), 403
    
    data = request.get_json(force=True)

    open_env = json.dumps(data.get("open_env")) if data.get("open_env") else None
    open_mood = (data.get("open_mood") or "").strip()[:40] or None  # 開封時の気分タグ（任意）
    # 初めて開いた瞬間だけ opened_at を刻む（届いた時の自分の記録）。
    # このとき「向き合った回数」も1つ数える（開封＝最初の対面）。
    already = row["opened_at"] if "opened_at" in row.keys() else None
    if not already:
        now_iso = datetime.now().isoformat(timespec="seconds")
        get_db().execute(
            "UPDATE letters SET opened=1, open_env=?, open_mood=?, opened_at=?, "
            "reflect_count=COALESCE(reflect_count,0)+1 WHERE id=? AND user_id=?",
            (open_env, open_mood, now_iso, lid, uid()))
    else:
        # 再開封：気分が新たに渡されたら更新する（無ければ既存を残す）
        if open_mood:
            get_db().execute("UPDATE letters SET opened=1, open_env=?, open_mood=? WHERE id=? AND user_id=?",
                             (open_env, open_mood, lid, uid()))
        else:
            get_db().execute("UPDATE letters SET opened=1, open_env=? WHERE id=? AND user_id=?",
                             (open_env, lid, uid()))
    get_db().commit()

    # 差分(diff)情報を返す
    return jsonify(ok=True, seal_env=row["seal_env"], open_env=open_env, open_mood=open_mood)


@app.route("/api/letters/<lid>/mood", methods=["POST"])
@login_required
def api_set_open_mood(lid):
    """開封時の気分タグを記録する。差分演出のあとに選んでもらう想定。"""
    row = own_letter(lid)
    if not row:
        return jsonify(error="便りが見つかりません。"), 404
    if not _is_arrived(row):
        return jsonify(error="まだ封の中です。"), 403
    mood = (request.get_json(force=True).get("mood") or "").strip()[:40] or None
    get_db().execute("UPDATE letters SET open_mood=? WHERE id=? AND user_id=?", (mood, lid, uid()))
    get_db().commit()
    return jsonify(ok=True, open_mood=mood)


@app.route("/api/letters/<lid>/emos", methods=["POST"])
@login_required
def api_set_emos(lid):
    row = own_letter(lid)
    if not row: return jsonify(error="便りが見つかりません。"), 404
    if not _is_arrived(row): return jsonify(error="まだ封の中です。"), 403
    
    emos = request.get_json(force=True).get("emos", [])
    get_db().execute("UPDATE letters SET emos=? WHERE id=? AND user_id=?", (json.dumps(emos, ensure_ascii=False), lid, uid()))
    get_db().commit()
    return jsonify(ok=True)

@app.route("/api/letters/<lid>/reply", methods=["POST"])
@login_required
def api_reply(lid):
    row = own_letter(lid)
    if not row: return jsonify(error="便りが見つかりません。"), 404
    if not _is_arrived(row): return jsonify(error="まだ封の中です。"), 403

    text = (request.get_json(force=True).get("text") or "").strip()
    if not text: return jsonify(error="空の返事です。"), 400

    now_iso = datetime.now().isoformat(timespec="seconds")
    get_db().execute(
        "INSERT INTO thread (letter_id,who,text,created,created_at,kind) VALUES (?,?,?,?,?,?)",
        (lid, "now", text, date.today().isoformat(), now_iso, "reply"))
    # この便りと向き合った回数を増やす
    get_db().execute("UPDATE letters SET reflect_count = COALESCE(reflect_count,0)+1 WHERE id=? AND user_id=?", (lid, uid()))
    get_db().commit()
    return jsonify(ok=True)

_WX_JP = {"snow": "雪", "rain": "雨", "fog": "霧", "cloud": "曇り", "clear": "晴れ"}


def _env_phrase(env):
    """seal_env / open_env(dict) を「雨で12℃」のような短い語に。無ければ空。"""
    if not env or not isinstance(env, dict):
        return ""
    cond = _WX_JP.get(env.get("condition"), "")
    temp = env.get("temp")
    if cond and temp is not None:
        return f"{cond}で{round(temp)}℃"
    return cond or (f"{round(temp)}℃" if temp is not None else "")


def _weather_context_text(seal_env, open_env):
    """封をした日と開けた日の天気を、AIプロンプト用の一文にまとめる。"""
    s = _env_phrase(seal_env)
    o = _env_phrase(open_env)
    if s and o:
        return f"封をしたあの日は「{s}」。それを開けている今日は「{o}」。"
    if s:
        return f"封をしたあの日は「{s}」だった。"
    if o:
        return f"これを開けている今日は「{o}」。"
    return ""


def _profile_context_text(user_id, limit=3):
    """30の質問の回答から、AIに渡す“ごく薄い背景”を作る。
    全部渡すと会話に反映され過ぎて不自然（＝気持ち悪い）になるため、答えのうち
    ランダムに数件だけ（最大 limit 件）を、あくまで雰囲気のヒントとして渡す。"""
    row = get_db().execute("SELECT onboarding FROM users WHERE id=?", (user_id,)).fetchone()
    answers = _load_onboarding(row["onboarding"] if row else None)
    if not answers:
        return ""
    qids = [q for q in answers if 0 <= q < len(ONBOARDING_QUESTIONS)]
    random.shuffle(qids)   # 毎回違う数件＝特定の回答に固執させない
    lines = [f"・{ONBOARDING_QUESTIONS[q]} → {answers[q]}" for q in qids[:limit]]
    return "\n".join(lines)


def _gemini_question(prompt, api_key):
    """Google Gemini（Google AI Studio）で問いを1つ生成する。
    追加ライブラリ不要：標準の urllib.request で REST API を直接叩く。
    無料枠（クレカ登録不要）で使えるのが利点。失敗時は例外を投げる（呼び出し側でフォールバック）。
    モデルは環境変数 TAYORI_GEMINI_MODEL で変更可（既定 gemini-2.5-flash-lite）。

    無料枠は 429(毎分の上限) と 503(混雑) が出やすい。どちらも一時的なので、
    少し待って再試行し、それでもダメなら『空いている別モデル』に自動で切り替える
    （flash 系が混雑していても lite 系は通ることが多い）。"""
    import urllib.request
    import urllib.error
    # 鍵にプレースホルダ（日本語）や非ASCIIが混じっていると、ヘッダー生成時に
    # 暗号のような 'ascii' codec エラーになる。先に弾いて、原因が分かる例外にする。
    if ("…" in api_key or "..." in api_key or "（" in api_key
            or "ここ" in api_key or "鍵" in api_key):
        raise ValueError(".env の GEMINI_API_KEY が例文（プレースホルダ）のままです。"
                         "本物の鍵に置き換えてください。")
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("GEMINI_API_KEY に日本語など非ASCII文字が含まれています。"
                         "鍵は英数字（AIzaSy… または AQ.…）だけです。コピーし直してください。")

    # 試すモデルの順番：環境変数の指定（あれば最優先）→ 無料枠で空きやすい控え。
    preferred = os.environ.get("TAYORI_GEMINI_MODEL")
    fallbacks = ["gemini-2.5-flash-lite", "gemini-flash-lite-latest",
                 "gemini-2.0-flash-lite", "gemini-2.5-flash"]
    models = ([preferred] if preferred else []) + [m for m in fallbacks if m != preferred]

    # 温度＝意外性のつまみ。高すぎると本人の言葉から離れて「突拍子のない」問いになる。
    # lite モデルは特に高温で暴投しやすいので、既定は 0.8 に抑える（本人の詩に根ざしつつ
    # 少しだけ意外性が出る塩梅）。もっと遊ばせたい時は TAYORI_GEMINI_TEMP で上げられる。
    try:
        temperature = float(os.environ.get("TAYORI_GEMINI_TEMP", "0.8"))
    except ValueError:
        temperature = 0.8
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "topP": 0.9},
    }).encode("utf-8")
    last_err = None
    for model in models:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent")
        for attempt in range(2):  # 同じモデルで最大2回（混雑時の取りこぼし対策）
            # 鍵は URL の ?key= ではなく X-goog-api-key ヘッダーで渡す。
            # 新形式の鍵（AQ.…）は ?key= だと 401(ACCESS_TOKEN_TYPE_UNSUPPORTED) になる。
            # 旧形式（AIzaSy…）もヘッダーで通るので、これで両対応。
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json", "X-goog-api-key": api_key})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode())
                cands = data.get("candidates") or []
                parts = (cands[0].get("content") or {}).get("parts") or [] if cands else []
                text = "".join(p.get("text", "") for p in parts).strip()
                if text:
                    return text
                break  # 応答は来たが本文が空 → 次のモデルへ
            except urllib.error.HTTPError as e:
                last_err = e
                # 400/401/403 は鍵やリクエスト自体の問題。モデルを変えても直らないので即中断。
                if e.code in (400, 401, 403):
                    raise
                # 429(上限)・503(混雑) は一時的。1回だけ短く待って再試行する。
                if e.code in (429, 503) and attempt == 0:
                    print(f"[Gemini] {model} が {e.code}。少し待って再試行します…", flush=True)
                    time.sleep(2)
                    continue
                # それ以外、または再試行済み → 次のモデルへ切り替え。
                print(f"[Gemini] {model} が {e.code}。別モデルに切り替えます…", flush=True)
                break
    if last_err:
        raise last_err
    return None


def _claude_question(prompt, api_key):
    """Anthropic Claude で問いを1つ生成する（有料）。GEMINI_API_KEY が無いときの代替。
    モデルは環境変数 TAYORI_AI_MODEL で変更可（既定 claude-opus-4-8）。失敗時は例外を投げる。"""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    model = os.environ.get("TAYORI_AI_MODEL", "claude-opus-4-8")
    msg = client.messages.create(model=model, max_tokens=1000,
                                 messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if b.type == "text").strip() or None


@app.route("/api/letters/<lid>/ask", methods=["POST"])
@login_required
def api_ask_past_self(lid):
    """過去の自分が「問い」を返す。
    AIが使える環境（GEMINI_API_KEY または ANTHROPIC_API_KEY あり＋外部通信可）なら本物の対話、
    無ければ、過去の自分が書いた詩と経過時間から問いを組み立てて返す（AI不要）。"""
    row = own_letter(lid)
    if row is None:
        return jsonify(error="便りが見つかりません。"), 404
    if not _is_arrived(row):
        return jsonify(error="まだ封の中です。"), 403
    L = letter_to_dict(row)

    now_iso = datetime.now().isoformat(timespec="seconds")

    # --- AI が使える環境なら本物の対話を試す ---
    # Gemini（無料枠・クレカ不要）を優先し、無ければ Claude（有料）を使う。
    # どちらの鍵も無い／通信OFF／失敗時は、下のローカル定型生成へ静かにフォールバックする。
    gemini_key = os.environ.get("GEMINI_API_KEY")
    claude_key = os.environ.get("ANTHROPIC_API_KEY")
    if NETWORK_ENABLED and (gemini_key or claude_key):
        convo = "\n".join(("今の自分: " if m["who"] == "now" else "過去の自分: ") + m["text"] for m in L["thread"])
        # 封をした日と今日の天気（差分）を文脈に添える
        weather_ctx = _weather_context_text(L.get("seal_env"), L.get("open_env"))
        # 30の質問の回答を「その人らしさ」の事前知識として渡す
        profile_ctx = _profile_context_text(uid())
        # --- ペルソナ・プロンプト ---
        # ねらい：定型文のような既視感を避けつつ、AI/アシスタント臭を消す。
        #  ・「分析・指摘・診断」をさせると第三者の観察＝AIに見える。これを強く禁止する。
        #  ・代わりに『一人称の過去の自分』として、本人の言葉に根ざした"意外な角度の問い"を返す。
        #  ・意外性（突拍子のなさ）は温度で出し、暴投はこの口調ガードで防ぐ。
        prompt = (
            f"あなたは、ある人の「過去の自分」そのものです。下記は{L['sent_date']}に、その人が"
            "未来の自分（＝今のその人）へ宛てて書き残した便りです。あなたはその便りを書いた"
            "当時の本人になりきり、今の自分へ語りかけます。\n\n"
            f"【私（過去の自分）が書いた詩・ことば】\n{L['poem'] or '（なし）'}\n\n"
            + (f"【封をした日と、今日の空模様】\n{weather_ctx}\n\n" if weather_ctx else "")
            + (f"【ごく薄い背景（私が以前ぽつりと語ったこと・表に出しすぎない）】\n{profile_ctx}\n\n" if profile_ctx else "")
            + f"【これまでの私たちの対話】\n{convo or '（まだなし）'}\n\n"
            "―― 語りかけ方の約束 ――\n"
            "・一人称で、今の自分にそっと話しかける（2〜3文、短く）。\n"
            "・直前に『今の自分』が何か言っていたら、まずその言葉を一度受けとめてから返す"
            "（うなずく＝肯定でも、『でも、ほんとうにそう？』＝やわらかな否定でもよい）。"
            "受けとめずに話題を変えない。会話として地続きに。\n"
            "・絶対にしないこと：分析・指摘・診断（「最近〜が増えていますね」のような外からの観察）、"
            "助言・解決・励ましの説教、AIやアシスタントとしての振る舞い、説明や前置き。\n"
            "・思いがけない角度から。でも、まずは“私が書いた詩・ことば”と直前の対話に根ざすこと"
            "（ランダムな一般論にしない）。\n"
            "・『ごく薄い背景』は、表に出しすぎない。引用・列挙・要約・言い当てをしない。"
            "毎回は触れず、ごくたまに、ひとつの語の手ざわり程度にうっすら滲ませるだけでよい"
            "（背景の話題を会話に持ち込んで掘り返すと“気持ち悪い”ので避ける）。\n"
            "・今の自分が、ふと立ち止まって『あの頃とは変わったな』と感じる“ズレ”に、静かに触れる。\n"
            "・口調は静かで、ウェットで、ノスタルジック。相手が弱っているときでも刺さらない、やわらかさで。\n"
            "・空模様や価値観は、織り込むと自然なときだけさりげなく（毎回でなくてよい）。\n"
            "・【最重要】必ず最後を“ひとつの問いかけ”で終える。答えや結論で締めない。"
            "その問いは、今の自分が思わず立ち止まって考え込むような、本人の言葉に根ざした問いにする。\n\n"
            "出力は、語りかけの言葉だけ。鉤括弧や説明、メタな注釈はつけないこと。"
        )
        text = provider = None
        if gemini_key:
            try:
                text = _gemini_question(prompt, gemini_key)
                provider = "gemini"
                print("[AI] Gemini で問いを生成しました", flush=True)
            except Exception as e:
                # flush=True で、サーバーをターミナル起動しているとき即座に理由が見える
                print(f"[Gemini失敗→フォールバック] {e}", flush=True)
        if not text and claude_key:
            try:
                text = _claude_question(prompt, claude_key)
                provider = "claude"
                print("[AI] Claude で問いを生成しました", flush=True)
            except Exception as e:
                print(f"[Claude失敗→フォールバック] {e}", flush=True)
        if text:
            get_db().execute("INSERT INTO thread (letter_id,who,text,created,created_at,kind) VALUES (?,?,?,?,?,?)",
                             (lid, "ai", text, date.today().isoformat(), now_iso, "question"))
            get_db().commit()
            return jsonify(text=text, used_ai=True, provider=provider)

    # --- AI なし：過去の自分の言葉から「問い」を組み立てる ---
    # なぜ定型生成になったかを必ずログに残す（原因切り分け用）
    if not NETWORK_ENABLED:
        print("[AI] 定型生成。理由: TAYORI_ENABLE_NETWORK が未設定（外部通信OFF）", flush=True)
    elif not (os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        print("[AI] 定型生成。理由: GEMINI_API_KEY も ANTHROPIC_API_KEY も未設定", flush=True)
    text = _build_self_question(L)
    get_db().execute("INSERT INTO thread (letter_id,who,text,created,created_at,kind) VALUES (?,?,?,?,?,?)",
                     (lid, "ai", text, date.today().isoformat(), now_iso, "question"))
    get_db().commit()
    return jsonify(text=text, used_ai=False)


def _build_self_question(L):
    """過去の自分の詩・経過時間・既出の問いから、新しい問いを1つ選んで返す。
    外部通信もAIも使わない。何度押しても少しずつ違う問いが出るようにする。"""
    import random

    poem = (L.get("poem") or "").strip()
    sent = L.get("sent_date") or ""
    # 経過日数
    try:
        gap_days = (date.today() - date.fromisoformat(sent[:10])).days
    except Exception:
        gap_days = 0

    # 既に出した問いの数（同じ問いを繰り返しにくくする）
    asked = [m for m in L.get("thread", []) if m.get("who") == "ai"]
    seen = {m["text"] for m in asked}

    # 経過時間のことば
    if gap_days >= 365:
        span = f"{gap_days // 365}年前"
    elif gap_days >= 30:
        span = f"{gap_days // 30}ヶ月前"
    elif gap_days >= 1:
        span = f"{gap_days}日前"
    else:
        span = "ついさっき"

    # 詩から短い手がかりを1行抜く（最初の意味のある行）
    first_line = ""
    for ln in poem.splitlines():
        if ln.strip():
            first_line = ln.strip()
            break

    # 問いの候補。詩がある場合と無い場合で変える。
    pool = []
    if first_line:
        pool += [
            f"{span}のわたしは「{first_line}」と書いた。今のあなたは、これにうなずける？",
            f"「{first_line}」── この言葉、今のあなたにはどう響く？",
            f"あの時のわたしが残した「{first_line}」。あなたは、もう違うことを思ってる？",
            f"「{first_line}」と書いたわたしへ。今のあなたなら、何を書き足す？",
        ]
    pool += [
        f"{span}のわたしは、何が一番こわかったと思う？",
        f"あれから、あなたは何を手放した？　何を握りしめたまま？",
        f"{span}のわたしに、今のあなたから一言だけ伝えるとしたら？",
        "あの頃のわたしが知らなかったことを、ひとつだけ教えて。",
        "今のあなたは、あの時のわたしより少しは自由になれた？",
        f"{span}から今日まで、変わらずにいるものは何？",
    ]

    # 封をした日／開けた日の天気があれば、それに触れる問いも候補に加える
    s = _env_phrase(L.get("seal_env"))
    o = _env_phrase(L.get("open_env"))
    if s and o:
        pool += [
            f"封をしたあの日は「{s}」、開けている今日は「{o}」。あなたの心も、あの頃と変わった？",
            f"あの日の「{s}」の空を、まだ覚えてる？　今日の「{o}」の下で、何を思う？",
        ]
    elif s:
        pool.append(f"封をしたのは「{s}」の日だった。あの空気を、今のあなたはどう思い出す？")

    # まだ出していない問いを優先
    fresh = [q for q in pool if q not in seen]
    if not fresh:
        fresh = pool  # 全部出し切ったら繰り返し可
    return random.choice(fresh)


# ---------------------------------------------------------------- timeline
@app.route("/api/timeline")
@login_required
def api_timeline():
    rows = get_db().execute("SELECT * FROM letters WHERE user_id=? ORDER BY sent_date", (uid(),)).fetchall()
    nodes = []
    for r in rows:
        d = letter_to_dict(r, include_thread=False)
        if d["arrived"]:
            nodes.append(dict(date=d["sent_date"], kind="sent", id=d["id"], poem=d["poem"],
                              photo=bool(d["photo"]), voice=bool(d["voice"]),
                              emos=d["emos"], opened=d["opened"], hidden=d["arrive_hidden"], sealed=False))
        else:
            nodes.append(dict(date=d["sent_date"], kind="sent", id=d["id"], poem=None, photo=False, voice=False, emos=[], opened=False, hidden=d["arrive_hidden"], sealed=True))
            nodes.append(dict(date=r["arrive_at"][:10], kind="future", id=d["id"], poem=None, photo=False, voice=False, emos=[], opened=False, hidden=d["arrive_hidden"], sealed=True))
    nodes.sort(key=lambda n: n["date"])
    return jsonify(nodes=nodes)


# ---------------------------------------------------------------- admin
# 管理ダッシュボードへのアクセス可否。
#   ① ログイン中のユーザーが admin アカウントなら許可（ログイン画面から admin でログイン）
#   ② 環境変数 TAYORI_ADMIN_TOKEN が設定されていれば ?token=◯◯ でも許可（保険・API用）
# どちらも満たさなければ拒否。誰でも開ける状態にはしない。
def _admin_ok():
    u = current_user()
    if u and u["username"] == "admin":
        return True
    want = os.environ.get("TAYORI_ADMIN_TOKEN")
    if want:
        got = request.args.get("token") or request.headers.get("X-Admin-Token")
        if got == want:
            return True
    return False

# 便りの「中身」（詩・対話など）を管理画面で読めるようにするか。
# tayori は本来「本人すら開封日まで覗けない」のが核なので、既定はオフ。
# ローカルでの管理用に見たいときだけ環境変数で有効化する:
#   export TAYORI_ADMIN_READ_CONTENT=1
# 公開時はこれを外せば、中身は管理画面からも一切見えなくなる。
ADMIN_READ_CONTENT = bool(os.environ.get("TAYORI_ADMIN_READ_CONTENT", "1"))

def _make_db_snapshot(dest_path):
    """動作中でも壊れない整合性のあるDBコピーを dest_path に作る。
    単純なファイルコピー(cp)はWAL/書き込み途中を掴んで壊れることがあるため、
    SQLite公式のオンラインバックアップAPI(conn.backup)を使う。"""
    src = sqlite3.connect(DB_PATH, timeout=30)
    dst = sqlite3.connect(dest_path)
    try:
        with dst:
            # ページ分割でコピーする。一度に全ページ(pages=-1)だとコピー中ずっと読みロックを
            # 保持し、さらに書き込みが入るとバックアップが最初からやり直し(livelock)になって
            # 全書き込みが詰まる。pages=64＋sleepで小刻みにロックを解放し、書き込みを通す。
            src.backup(dst, pages=64, sleep=0.01)
    finally:
        dst.close()
        src.close()


def _backup_s3_config():
    """オフサイト自動バックアップの設定（Cloudflare R2 / S3 互換）を環境変数から読む。
    4つ揃って初めて有効。未設定なら None（＝自動バックアップは行わない）。"""
    ep = os.environ.get("TAYORI_BACKUP_S3_ENDPOINT")
    bk = os.environ.get("TAYORI_BACKUP_S3_BUCKET")
    ak = os.environ.get("TAYORI_BACKUP_S3_KEY")
    sk = os.environ.get("TAYORI_BACKUP_S3_SECRET")
    if ep and bk and ak and sk:
        return {"endpoint": ep, "bucket": bk, "key": ak, "secret": sk}
    return None


def _run_backup_to_s3():
    """整合性コピー→gzip→R2/S3 へアップロード。古い分は最新 KEEP 個だけ残して削除。
    設定が無ければ何もしない。失敗してもアプリ本体には影響させない（例外は飲み込む）。"""
    cfg = _backup_s3_config()
    if not cfg:
        return False
    try:
        import gzip
        import boto3
    except ImportError:
        print("[たより] バックアップ: boto3 が無いためスキップ（requirements.txt 確認）", flush=True)
        return False
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # スナップショット中は書き込みロックと競合しうる。所要時間を必ず記録して、
        # 「サイトが重い時刻」と突き合わせられるようにする（重さの原因切り分け用）。
        _t0 = time.monotonic()
        print(f"[たより] バックアップ開始 {datetime.now().strftime('%H:%M:%S')}", flush=True)
        _make_db_snapshot(tmp)
        _snap_ms = (time.monotonic() - _t0) * 1000.0
        print(f"[たより] スナップショット完了（{_snap_ms:.0f}ms・この間は書き込みが詰まりうる）", flush=True)
        with open(tmp, "rb") as fh:
            blob = gzip.compress(fh.read())
        key = "backups/tayori-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".db.gz"
        s3 = boto3.client("s3", endpoint_url=cfg["endpoint"],
                          aws_access_key_id=cfg["key"], aws_secret_access_key=cfg["secret"])
        s3.put_object(Bucket=cfg["bucket"], Key=key, Body=blob)
        print(f"[たより] バックアップ完了 → {key}（{len(blob)} bytes）", flush=True)
        # 古いバックアップを掃除（最新 KEEP 個だけ残す）
        try:
            keep = int(os.environ.get("TAYORI_BACKUP_KEEP", "14"))
        except ValueError:
            keep = 14
        objs = s3.list_objects_v2(Bucket=cfg["bucket"], Prefix="backups/").get("Contents", [])
        objs.sort(key=lambda o: o["Key"])
        for o in (objs[:-keep] if len(objs) > keep else []):
            s3.delete_object(Bucket=cfg["bucket"], Key=o["Key"])
        return True
    except Exception as e:
        print(f"[たより] バックアップ失敗（本体は継続）: {e}", flush=True)
        return False
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


@app.route("/admin.welcometotayori/backup")
def admin_backup():
    """管理者だけが、今この瞬間の整合バックアップ(.db)をダウンロードできる。
    手元に保存しておけば、ディスク障害時もこのファイルから復元できる。"""
    if not _admin_ok():
        return "アクセス権がありません。", 403
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _make_db_snapshot(tmp)
        with open(tmp, "rb") as fh:
            data = fh.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    fname = "tayori-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".db"
    return Response(data, mimetype="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.route("/admin.welcometotayori")
def admin_page():
    if not _admin_ok():
        return "管理画面へのアクセス権がありません。", 403
    db = get_db()
    now_iso = datetime.now().isoformat(timespec="seconds")

    users = db.execute(
        """SELECT id,username,email,email_verified,notify_enabled,
                  onboarding,onboarded,last_lat,created
           FROM users ORDER BY created"""
    ).fetchall()

    # --- 対話メッセージ数をユーザー単位で集計（who別。中身は読まず件数のみ） ---
    thread_by_user = {}   # uid -> {"total":, "ai":, "now":}
    for row in db.execute(
        """SELECT l.user_id AS uid, t.who AS who, COUNT(*) AS c
           FROM thread t JOIN letters l ON l.id = t.letter_id
           GROUP BY l.user_id, t.who"""):
        d = thread_by_user.setdefault(row["uid"], {"total": 0, "ai": 0, "now": 0})
        d["total"] += row["c"]
        if row["who"] == "ai":
            d["ai"] += row["c"]
        elif row["who"] == "now":
            d["now"] += row["c"]

    onb_total = len(ONBOARDING_QUESTIONS)

    # --- ユーザーごとの投函数・受信状況・利用機能（件数だけ。中身は一切読まない） ---
    # 受信済み = 通常便は arrive_at が現在以前 / 天気便は weather_met_at が入っている
    # 配送中  = それ以外 / 天気待ち = weather_event があって weather_met_at が未確定
    user_stats = {}
    for u in users:
        rows = db.execute(
            """SELECT arrive_at, arrive_date, weather_event, weather_met_at,
                      opened, photo, voice, from_reply, reflect_count
               FROM letters WHERE user_id=?""",
            (u["id"],)
        ).fetchall()
        total = len(rows)
        received = transit = waiting_weather = 0
        opened = photo = voice = weather = reply = reflect = 0
        for r in rows:
            wevent = r["weather_event"]
            if wevent:
                met = r["weather_met_at"]
                if met and met <= now_iso:
                    received += 1
                else:
                    transit += 1
                    waiting_weather += 1
                weather += 1
            else:
                arrive_at = r["arrive_at"] or (r["arrive_date"] + "T00:00:00")
                if arrive_at <= now_iso:
                    received += 1
                else:
                    transit += 1
            if r["opened"]:
                opened += 1
            if r["photo"]:
                photo += 1
            if r["voice"]:
                voice += 1
            if r["from_reply"]:
                reply += 1
            reflect += (r["reflect_count"] or 0)
        th = thread_by_user.get(u["id"], {"total": 0, "ai": 0, "now": 0})
        ob = _load_onboarding(u["onboarding"])
        onb_answered = sum(1 for v in ob.values() if str(v).strip())
        user_stats[u["id"]] = {
            "total": total, "received": received,
            "transit": transit, "waiting_weather": waiting_weather,
            "opened": opened, "photo": photo, "voice": voice,
            "weather": weather, "reply": reply, "reflect": reflect,
            "dialogues": th["total"], "ai": th["ai"], "replies": th["now"],
            "onb_answered": onb_answered,
        }

    # --- 全体サマリー（件数＋派生する率） ---
    def _sum(k):
        return sum(s[k] for s in user_stats.values())
    totals = {
        "users": len(users),
        "letters": _sum("total"),
        "received": _sum("received"),
        "transit": _sum("transit"),
        "waiting_weather": _sum("waiting_weather"),
        "opened": _sum("opened"),
        "dialogues": _sum("dialogues"),
        "ai": _sum("ai"),
        "photo": _sum("photo"),
        "voice": _sum("voice"),
        "weather": _sum("weather"),
        "reply": _sum("reply"),
        "emails": sum(1 for u in users if u["email"]),
        "verified": sum(1 for u in users if u["email_verified"]),
        "onboarded": sum(1 for u in users if user_stats[u["id"]]["onb_answered"]),
        "located": sum(1 for u in users if u["last_lat"]),
        "notify": sum(1 for u in users if u["notify_enabled"]),
    }
    totals["open_rate"] = round(totals["opened"] / totals["received"] * 100) if totals["received"] else 0
    totals["email_rate"] = round(totals["emails"] / totals["users"] * 100) if totals["users"] else 0
    totals["onb_rate"] = round(totals["onboarded"] / totals["users"] * 100) if totals["users"] else 0
    totals["avg_letters"] = round(totals["letters"] / totals["users"], 1) if totals["users"] else 0

    # --- 直近の登録ユーザー数推移（日別・過去14日） ---
    signups = {}
    for u in users:
        day = (u["created"] or "")[:10]
        if day:
            signups[day] = signups.get(day, 0) + 1
    trend = []
    cumulative_before = 0
    # 過去14日分の枠を用意（0の日も出す）
    span_days = 14
    start = date.today() - timedelta(days=span_days - 1)
    # span 開始より前の登録は累計の初期値に乗せる
    for u in users:
        d = (u["created"] or "")[:10]
        if d and d < start.isoformat():
            cumulative_before += 1
    running = cumulative_before
    for i in range(span_days):
        d = (start + timedelta(days=i)).isoformat()
        new = signups.get(d, 0)
        running += new
        trend.append({"date": d, "new": new, "cumulative": running})

    # バーの高さ(%)を Python 側で計算しておく（テンプレートにロジックを置かない）
    max_new = max((t["new"] for t in trend), default=0)
    for t in trend:
        t["bar_h"] = int(round(t["new"] / max_new * 100)) if (max_new and t["new"]) else 0

    enriched_users = []
    for u in users:
        d = dict(u)
        d["stats"] = user_stats[u["id"]]
        d["has_location"] = bool(u["last_lat"])
        d.pop("onboarding", None)  # 生のJSON回答はテンプレに渡さない（件数だけで十分）
        enriched_users.append(d)

    # --- 最近の便り（中身つき）。ADMIN_READ_CONTENT が有効なときだけ集める ---
    # ※ tayori 本来の思想では中身は本人すら見られない。これは管理用の覗き窓で、
    #    公開時は TAYORI_ADMIN_READ_CONTENT を外せば下のリストは空になる。
    recent_letters = []
    if ADMIN_READ_CONTENT:
        uname = {u["id"]: u["username"] for u in users}
        rows = db.execute(
            """SELECT id, user_id, poem, photo, voice, sent_date,
                      arrive_at, arrive_date, arrive_label, weather_event,
                      weather_met_at, opened, emos
               FROM letters
               ORDER BY sent_date DESC, id DESC
               LIMIT 50"""
        ).fetchall()
        for r in rows:
            wevent = r["weather_event"]
            if wevent:
                met = r["weather_met_at"]
                status = "受信済み" if (met and met <= now_iso) else "天気待ち"
            else:
                arrive_at = r["arrive_at"] or (r["arrive_date"] + "T00:00:00")
                status = "受信済み" if arrive_at <= now_iso else "配送中"
            # 対話スレッドの件数（中身までは出さず件数のみ）
            tcount = db.execute(
                "SELECT COUNT(*) AS c FROM thread WHERE letter_id=?", (r["id"],)
            ).fetchone()["c"]
            try:
                emos = json.loads(r["emos"] or "[]")
            except Exception:
                emos = []
            poem = (r["poem"] or "").strip()
            recent_letters.append({
                "id": r["id"],
                "username": uname.get(r["user_id"], "—"),
                "poem": poem,
                "has_photo": bool(r["photo"]),
                "has_voice": bool(r["voice"]),
                "sent_date": r["sent_date"],
                "arrive_label": r["arrive_label"] or "",
                "status": status,
                "opened": bool(r["opened"]),
                "emos": emos,
                "thread_count": tcount,
            })

    return render_template(
        "admin.html",
        users=enriched_users,
        totals=totals,
        trend=trend,
        recent_letters=recent_letters,
        read_content=ADMIN_READ_CONTENT,
        onb_total=onb_total,
    )

@app.route("/api/admin/letters/<lid>")
def api_admin_letter_detail(lid):
    """管理用：1通の便りの全文＋対話を返す。ADMIN_READ_CONTENT が無効なら拒否。"""
    if not _admin_ok():
        return jsonify(error="権限がありません。"), 403
    if not ADMIN_READ_CONTENT:
        return jsonify(error="中身の閲覧は無効化されています。"), 403
    db = get_db()
    r = db.execute(
        """SELECT l.*, u.username AS username
           FROM letters l JOIN users u ON u.id = l.user_id
           WHERE l.id=?""", (lid,)
    ).fetchone()
    if not r:
        return jsonify(error="便りが見つかりません。"), 404
    try:
        emos = json.loads(r["emos"] or "[]")
    except Exception:
        emos = []
    thread = db.execute(
        "SELECT who,text,created,created_at,kind FROM thread WHERE letter_id=? ORDER BY id",
        (lid,)
    ).fetchall()
    return jsonify(
        id=r["id"],
        username=r["username"],
        poem=r["poem"] or "",
        has_photo=bool(r["photo"]),
        has_voice=bool(r["voice"]),
        sent_date=r["sent_date"],
        arrive_label=r["arrive_label"] or "",
        opened=bool(r["opened"]),
        emos=emos,
        thread=[dict(t) for t in thread],
    )


@app.route("/api/admin/users/<uid_>/delete", methods=["POST"])
def api_admin_delete_user(uid_):
    if not _admin_ok():
        return jsonify(error="権限がありません。"), 403
    db = get_db()
    row = db.execute("SELECT username FROM users WHERE id=?", (uid_,)).fetchone()
    if not row:
        return jsonify(error="ユーザーが見つかりません。"), 404
    if row["username"] == "admin":
        return jsonify(error="管理者アカウントは削除できません。"), 403
    try:
        with _WRITE_LOCK:  # 複数DELETE＋commitを1トランザクションで直列化
            # このユーザーの便り・スレッド・下書きも全て物理削除（関連データを残さない）
            db.execute(
                "DELETE FROM thread WHERE letter_id IN (SELECT id FROM letters WHERE user_id=?)",
                (uid_,))
            db.execute("DELETE FROM letters WHERE user_id=?", (uid_,))
            db.execute("DELETE FROM drafts  WHERE user_id=?", (uid_,))
            db.execute("DELETE FROM users   WHERE id=?",      (uid_,))
            db.commit()
    except sqlite3.OperationalError as e:
        print(f"[たより] ユーザー削除 書き込み失敗（再試行可）: {e}", flush=True)
        return jsonify(error="いま混み合っています。数秒おいて、もう一度お試しください。"), 503
    return jsonify(ok=True)


if __name__ == "__main__":
    init_db()
    start_notifier()
    app.run(debug=True, port=5001)
else:
    init_db()
    start_notifier()
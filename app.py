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
import html
import random
import atexit
import shutil
import signal
import smtplib
import sqlite3
import secrets
import colorsys
import hashlib
import tempfile
import threading
import urllib.request   # 関数内で遅延importすると、複数スレッドが同時に初回importを走らせた際
import urllib.error     # 「cannot access submodule 'request'（循環import）」で失敗する。
                        # 起動時にモジュールレベルで1回だけimportして競合を防ぐ。
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, parseaddr, make_msgid, formatdate
from functools import wraps
from collections import Counter
from datetime import datetime, date, timedelta

from flask import Flask, request, jsonify, render_template, g, session, Response, redirect
from werkzeug.security import generate_password_hash, check_password_hash

# サーバーのタイムゾーンを日本時間に固定する。
os.environ["TZ"] = os.environ.get("TAYORI_TZ", "Asia/Tokyo")
try:
    time.tzset()
except AttributeError:
    pass

APP_DIR = os.path.dirname(os.path.abspath(__file__))
_DB_DESIRED = os.environ.get("TAYORI_DB_PATH") or os.path.join(APP_DIR, "tayori.db")

def _resolve_db_path(desired):
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
                      "【このままだと再デプロイでユーザーが消えます】", flush=True)
            return p
    return desired


DB_PATH = _resolve_db_path(_DB_DESIRED)

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
print(f"[たより] DB_PATH = {DB_PATH} / フォルダ書込可={os.access(_db_dir, os.W_OK)} "
      f"（TAYORI_DB_PATH={'未設定' if not os.environ.get('TAYORI_DB_PATH') else '設定済'}）", flush=True)
if _LOCAL_CACHE:
    print(f"[たより] ローカルキャッシュDB有効：実行={DB_PATH} ／ 永続={_PERSIST_DB_PATH}"
          f"（{_PERSIST_SECONDS}秒ごと＋終了時に保存）", flush=True)


def _restore_from_durable():
    if not _LOCAL_CACHE:
        return
    try:
        if os.path.exists(_PERSIST_DB_PATH) and not os.path.exists(DB_PATH):
            shutil.copy2(_PERSIST_DB_PATH, DB_PATH)
            for ext in ("-wal", "-shm", "-journal"):
                if os.path.exists(_PERSIST_DB_PATH + ext):
                    shutil.copy2(_PERSIST_DB_PATH + ext, DB_PATH + ext)
            print(f"[たより] 起動復元：{_PERSIST_DB_PATH} → {DB_PATH}", flush=True)
    except Exception as e:
        print(f"[たより] 起動復元に失敗（新規DBで起動）: {e}", flush=True)


_WRITE_LOCK = threading.RLock()
_persist_lock = threading.Lock()

def _persist_to_durable():
    if not _LOCAL_CACHE:
        return False
    if not _persist_lock.acquire(blocking=False):
        return False
    stage = DB_PATH + ".persist.tmp"
    durtmp = _PERSIST_DB_PATH + ".tmp"
    try:
        with _WRITE_LOCK:
            # WALモード時はステージコピー前に本体へ強制統合（TRUNCATE）する
            if os.environ.get("TAYORI_SQLITE_WAL") == "1":
                try:
                    c = sqlite3.connect(DB_PATH, timeout=5)
                    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    c.close()
                except Exception:
                    pass
            shutil.copyfile(DB_PATH, stage)
        shutil.copyfile(stage, durtmp)
        os.replace(durtmp, _PERSIST_DB_PATH)
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

if _LOCAL_CACHE:
    atexit.register(_persist_to_durable)

    def _persist_on_signal(signum, frame):
        _persist_to_durable()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    try:
        signal.signal(signal.SIGTERM, _persist_on_signal)
    except (ValueError, OSError):
        pass


def _load_dotenv():
    path = os.path.join(APP_DIR, ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
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
    MAX_CONTENT_LENGTH=10 * 1024 * 1024, # 16MB -> 10MBに変更 (メモリ保護)
)

# ── SEO ─────────────────────────────────────────────────────────
# canonical/OGP/sitemap の基準URL。本番は www に統一（apexは301→www）。
SITE_URL = os.environ.get("SITE_URL", "https://www.tayori-letter.com").rstrip("/")
# sitemap/robots に載せてよい「公開ページ」だけを集約（増えたらここに足す）。
# 手紙(/open)・API・管理・認証系は絶対に載せない。
PUBLIC_PATHS = ["/", "/terms", "/privacy"]


@app.context_processor
def inject_seo():
    # 全テンプレートで canonical_url / SITE_URL を使えるようにする。
    return {"SITE_URL": SITE_URL, "canonical_url": SITE_URL + request.path}

@app.before_request
def _perf_start():
    g._t0 = time.monotonic()


_COMPRESSIBLE = ("text/html", "text/css", "text/plain", "text/javascript",
                 "application/javascript", "application/json", "image/svg+xml")
_GZIP_MIN_BYTES = 1024


@app.after_request
def _finalize_response(resp):
    try:
        ctype = (resp.content_type or "").split(";")[0].strip()
        if not resp.direct_passthrough and request.method in ("GET", "HEAD"):
            if ctype == "text/html" and resp.status_code == 200:
                resp.add_etag()
                resp.headers.setdefault("Cache-Control", "no-cache")
                resp.make_conditional(request)

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
        print(f"[たより] 応答最適化スキップ: {e}", flush=True)

    try:
        dt = (time.monotonic() - getattr(g, "_t0", time.monotonic())) * 1000.0
        if dt >= 200:
            print(f"[たより][slow] {dt:6.0f}ms {request.method} {request.path}"
                  f" -> {resp.status_code}", flush=True)
    except Exception:
        pass
    return resp


NETWORK_ENABLED = bool(os.environ.get("TAYORI_ENABLE_NETWORK"))
# AI機能のマスタースイッチ。AI要素（問い生成・対話・肖像・章編み）は停止中。
# コードは将来のopt-inに備えて温存しており、再有効化は TAYORI_ENABLE_AI=1 の設定のみで行える。
AI_ENABLED = bool(os.environ.get("TAYORI_ENABLE_AI"))
BASE_URL = (os.environ.get("TAYORI_BASE_URL") or "http://127.0.0.1:5000").rstrip("/")

_wal_ready = False
_USE_WAL = os.environ.get("TAYORI_SQLITE_WAL") == "1"
_BUSY_TIMEOUT_MS = int(os.environ.get("TAYORI_BUSY_TIMEOUT_MS", "15000"))
_SYNC_MODE = (os.environ.get("TAYORI_SQLITE_SYNC", "OFF") or "OFF").upper()
if _SYNC_MODE not in ("OFF", "NORMAL", "FULL"):
    _SYNC_MODE = "OFF"


def _connect():
    global _wal_ready
    conn = sqlite3.connect(DB_PATH, timeout=_BUSY_TIMEOUT_MS / 1000.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        conn.execute(f"PRAGMA synchronous={_SYNC_MODE}")
        if _USE_WAL and not _wal_ready:
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


try:
    _PBKDF2_ITERS = int(os.environ.get("TAYORI_PBKDF2_ITERS", "100000"))
except ValueError:
    _PBKDF2_ITERS = 100000
_PW_METHOD = f"pbkdf2:sha256:{_PBKDF2_ITERS}"


def _hash_pw(pw):
    return generate_password_hash(pw, method=_PW_METHOD)


def _normalize_journal_mode():
    try:
        c = sqlite3.connect(DB_PATH, timeout=15)
        try:
            mode = (c.execute("PRAGMA journal_mode").fetchone() or [""])[0]
            if _USE_WAL and str(mode).lower() != "wal":
                newmode = (c.execute("PRAGMA journal_mode=WAL").fetchone() or [""])[0]
                c.execute("PRAGMA synchronous=NORMAL")
                print(f"[たより] DBを{newmode}へ切替（読書ブロック解消＋fsync停止対策・TAYORI_SQLITE_WAL=1）", flush=True)
            elif not _USE_WAL and str(mode).lower() == "wal":
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                newmode = (c.execute("PRAGMA journal_mode=DELETE").fetchone() or [""])[0]
                print(f"[たより] DBをWAL→{newmode}へ戻しました（永続ディスクのdisk I/O error対策）", flush=True)
            c.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        finally:
            c.close()
    except sqlite3.Error as e:
        print(f"[たより] journal_mode正規化に失敗: {e} → -wal/-shm の除去を試みます", flush=True)
        for ext in ("-wal", "-shm"):
            try:
                os.remove(DB_PATH + ext)
            except OSError:
                pass

# ── 10問アンケートの設問と「回答→手紙の一文」テンプレート ──────────────
# letter_fragment_template の {answer} が回答文に置き換わり、封をする時に ord 順で連結される。
# is_required は「必須／任意」のやわらかな目印。回答は常に任意で、未完成でも封はできる（呼び水であって検査ではない）。
SURVEY_QUESTIONS = [
    (1,  "いま、いちばん心にかかっていることは何ですか。",       "いま、わたしの心をいちばん占めているのは、{answer}。", 1),
    (2,  "今日、小さくても嬉しかったことは。",                   "その日、{answer}が、少しだけ嬉しかった。",             0),
    (3,  "最近、誰のことをよく思い出しますか。",                 "この頃、よく思い出すのは、{answer}。",                   0),
    (4,  "これからの自分に、続けていてほしいことは。",           "未来のあなたへ。どうか、{answer}を続けていて。",         1),
    (5,  "いま、そろそろ手放していいと思うものは。",             "そして、{answer}は、もう手放していい。",                0),
    (6,  "今日のあなたを、色でたとえると。",                     "今日という日は、{answer}のような色をしていた。",         0),
    (7,  "最近、何にいちばん時間を使いましたか。",               "最近は、{answer}に、多くの時間を使っていた。",           0),
    (8,  "ひそかに、楽しみにしていることは。",                   "ひそかに、{answer}を楽しみにしている。",                0),
    (9,  "いまの自分に、ちゃんとあると感じるものは。",           "いまのわたしには、{answer}が、ちゃんとある。",           0),
    (10, "未来のあなたへ、ひとことだけ。",                       "最後に、ひとこと。{answer}",                            1),
]


def _seed_questions(db):
    """questions が空のときだけ10問を投入する（既存の回答・封をした手紙を壊さない冪等シード）。"""
    try:
        if db.execute("SELECT COUNT(*) AS c FROM questions").fetchone()["c"] == 0:
            db.executemany(
                "INSERT INTO questions (id,ord,prompt,letter_fragment_template,is_required) VALUES (?,?,?,?,?)",
                [(o, o, p, t, r) for (o, p, t, r) in SURVEY_QUESTIONS],
            )
    except sqlite3.OperationalError:
        pass


_init_db_done = False

def _compute_grid_id(lat, lng):
    """area_lat/lng を0.1度セルへ丸めた識別子 "{lat}_{lng}"（約11km四方）。
    位置なし手紙は None。丸めは新規付与とバックフィルで同一式を使う（文字列一致が命）。"""
    if lat is None or lng is None:
        return None
    try:
        return f"{round(float(lat), 1)}_{round(float(lng), 1)}"
    except (TypeError, ValueError):
        return None


def _backfill_grid_ids(db):
    """既存の手紙に grid_id を一度だけ付与する。丸めは Python 側で行い、
    compute_grid_id と完全一致させる（SQLite の ROUND→TEXT 変換の桁化けを避ける）。"""
    rows = db.execute(
        "SELECT id, area_lat, area_lng FROM letters "
        "WHERE grid_id IS NULL AND area_lat IS NOT NULL AND area_lng IS NOT NULL"
    ).fetchall()
    for r in rows:
        gid = _compute_grid_id(r["area_lat"], r["area_lng"])
        if gid:
            db.execute("UPDATE letters SET grid_id=? WHERE id=?", (gid, r["id"]))


def _hex_to_hsl_str(hex_str):
    """"#RRGGBB"/"#RGB" → "hsl(H, S%, L%)"。変換できない値は None（元の値を残す）。"""
    try:
        h = hex_str.strip().lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, AttributeError, IndexError):
        return None
    hue, lig, sat = colorsys.rgb_to_hls(r, g, b)
    return "hsl(%d, %d%%, %d%%)" % (round(hue * 360) % 360, round(sat * 100), round(lig * 100))


def _migrate_colors_to_hsl(db):
    """気分の色を持つ全カラムの HEX 値を "hsl(H, S%, L%)" へ変換する（v3.14・冪等）。
    対象: letters.seal_color / letters.open_color / unemptyable_trash.mood_color /
          woven_scraps.mood_color / notes.color"""
    for table, col in (("letters", "seal_color"), ("letters", "open_color"),
                       ("unemptyable_trash", "mood_color"), ("woven_scraps", "mood_color"),
                       ("notes", "color")):
        rows = db.execute(
            f"SELECT id, {col} AS c FROM {table} WHERE {col} LIKE '#%'").fetchall()
        for r in rows:
            hsl = _hex_to_hsl_str(r["c"])
            if hsl:
                db.execute(f"UPDATE {table} SET {col}=? WHERE id=?", (hsl, r["id"]))


def init_db():
    global _init_db_done
    if _init_db_done:
        return
    
    with _WRITE_LOCK:
        if _init_db_done: return
        _restore_from_durable()
        _normalize_journal_mode()
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
            CREATE TABLE IF NOT EXISTS notes (
                id      TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                color   TEXT,
                text    TEXT,
                env     TEXT,
                created TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS unemptyable_trash (
                id         TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                content    TEXT NOT NULL,
                mood_color TEXT,
                vertical   INTEGER DEFAULT 0,
                random_x   REAL NOT NULL,
                random_y   REAL NOT NULL,
                created_at TEXT NOT NULL,
                trace      TEXT
            );
            CREATE TABLE IF NOT EXISTS woven_scraps (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                mood_color  TEXT,
                woven_month TEXT NOT NULL
            );
            """
        )
        for stmt in (
            "ALTER TABLE letters ADD COLUMN arrive_at TEXT",
            "ALTER TABLE letters ADD COLUMN weather_lock TEXT",
            "ALTER TABLE letters ADD COLUMN seal_env TEXT",
            "ALTER TABLE letters ADD COLUMN open_env TEXT",
            "ALTER TABLE letters ADD COLUMN notified INTEGER DEFAULT 0",
            "ALTER TABLE letters ADD COLUMN weather_event TEXT",
            "ALTER TABLE letters ADD COLUMN weather_met_at TEXT",
            "ALTER TABLE users ADD COLUMN email TEXT",
            "ALTER TABLE users ADD COLUMN last_lat TEXT",
            "ALTER TABLE users ADD COLUMN last_lon TEXT",
            "ALTER TABLE letters ADD COLUMN opened_at TEXT",
            "ALTER TABLE letters ADD COLUMN open_mood TEXT",
            "ALTER TABLE letters ADD COLUMN reflect_count INTEGER DEFAULT 0",
            "ALTER TABLE letters ADD COLUMN stamp TEXT",
            "ALTER TABLE thread ADD COLUMN created_at TEXT",
            "ALTER TABLE thread ADD COLUMN kind TEXT",
            "ALTER TABLE users ADD COLUMN email_token TEXT",
            "ALTER TABLE users ADD COLUMN email_token_at TEXT",
            "ALTER TABLE users ADD COLUMN unsub_token TEXT",
            "ALTER TABLE users ADD COLUMN notify_enabled INTEGER DEFAULT 1",
            "ALTER TABLE letters ADD COLUMN notify_attempts INTEGER DEFAULT 0",
            "ALTER TABLE letters ADD COLUMN notify_failed INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN onboarding TEXT",
            "ALTER TABLE users ADD COLUMN portrait TEXT",
            "ALTER TABLE users ADD COLUMN portrait_at TEXT",
            "ALTER TABLE letters ADD COLUMN trace TEXT",
            "ALTER TABLE users ADD COLUMN persona TEXT",
            "ALTER TABLE users ADD COLUMN persona_at TEXT",
            "ALTER TABLE users ADD COLUMN persona_src TEXT",
            "ALTER TABLE users ADD COLUMN weekly TEXT",
            "ALTER TABLE users ADD COLUMN gen_questions TEXT",
            "ALTER TABLE users ADD COLUMN chapters TEXT",
            "ALTER TABLE letters ADD COLUMN seal_color TEXT",
            "ALTER TABLE letters ADD COLUMN open_color TEXT",
            "ALTER TABLE letters ADD COLUMN seal_q TEXT",
            # リテンション：初めて封をした時に一度だけ「ブックマークに」を出す。表示した瞬間にこの列を立てる。
            "ALTER TABLE users ADD COLUMN bookmark_prompt_shown INTEGER DEFAULT 0",
            # 封じた場所の「エリア」。生の現在地座標は入れない（逆ジオコーディング結果の
            # エリア名とその代表座標のみ）。位置なし手紙はすべてNULLで正常系。
            "ALTER TABLE letters ADD COLUMN area_name TEXT",
            "ALTER TABLE letters ADD COLUMN area_lat REAL",
            "ALTER TABLE letters ADD COLUMN area_lng REAL",
            "ALTER TABLE letters ADD COLUMN time_bucket TEXT",
            # 縦書きの手紙。書いた時の姿（縦/横）ごと封入し、開封時も同じ姿で届く。
            "ALTER TABLE letters ADD COLUMN vertical INTEGER DEFAULT 0",
            # 便箋の書体列（書体選択は撤去済み・明朝のみ。過去データ互換のため列だけ残す）
            "ALTER TABLE letters ADD COLUMN font TEXT",
            # コメント（今の自分→過去の手紙への一方通行）の「その時」：時間帯と気象スナップショット
            "ALTER TABLE thread ADD COLUMN time_bucket TEXT",
            "ALTER TABLE thread ADD COLUMN env TEXT",
            # 開封した場所の「エリア」。封緘時と同じ流儀：生座標は入れず、
            # 逆ジオコーディング結果のエリア名と代表座標（小数第3位丸め）のみ。取れなければNULLで正常系。
            "ALTER TABLE letters ADD COLUMN open_area_name TEXT",
            "ALTER TABLE letters ADD COLUMN open_area_lat REAL",
            "ALTER TABLE letters ADD COLUMN open_area_lng REAL",
            # デモ用の手紙（scripts/seed_demo_data.py で投入）。demo_mode=1 の手紙だけ
            # demo_arrive_at（上書きの開封予定日時）を自由に動かせる。本来の arrive_at は
            # 温存し、上書きはこの列にだけ持つ（NULL に戻せば元の予定に戻る）。
            "ALTER TABLE letters ADD COLUMN demo_mode INTEGER DEFAULT 0",
            "ALTER TABLE letters ADD COLUMN demo_arrive_at TEXT",
            # 屑籠にも筆跡（TypeTrace）を封じる。握りつぶした時の打鍵ごと残る
            "ALTER TABLE unemptyable_trash ADD COLUMN trace TEXT",
            # ほどける日時（2026-07-22「ほどけるまで」）。この日時を過ぎた紙玉は
            # 色片(woven_scraps)へ溶け、本文と筆跡は物理的に消える（不可逆）。
            "ALTER TABLE unemptyable_trash ADD COLUMN unravel_at TEXT",
            # 気分の地図（Mood Night Map / 2026-07-23）。A(気分の宙)・B(地図)で共通の集計基盤。
            #   grid_id                  … area_lat/lng を0.1度に丸めたセル識別子 "{lat}_{lng}"（約11km四方）
            #   excluded_from_aggregate  … A・B共通のオプトアウト。0=集計に含める(既定) / 1=外す
            "ALTER TABLE letters ADD COLUMN grid_id TEXT",
            "ALTER TABLE letters ADD COLUMN excluded_from_aggregate INTEGER DEFAULT 0",
            # ユーザー単位のオプトアウト意思。ONにすると既存・今後すべての手紙を集計から外す。
            # letters.excluded_from_aggregate は投函時にこの値から写す（集計クエリは手紙側だけ見ればよい）。
            "ALTER TABLE users ADD COLUMN aggregate_opt_out INTEGER DEFAULT 0",
            # 気分の地図のリリース告知（事後同意）。一度出したらこの時刻を立てて再表示しない。
            "ALTER TABLE users ADD COLUMN night_map_notice_seen_at TEXT",
        ):
            try:
                db.execute(stmt)
            except sqlite3.OperationalError:
                pass

        # 「ほどけるまで」への移行: 既存の紙玉に created_at 基準で7日ルールを当てると
        # デプロイ即日に古い紙玉の本文が消えてしまう。既存行には「今から7日」の猶予を与える。
        db.execute(
            "UPDATE unemptyable_trash SET unravel_at=? WHERE unravel_at IS NULL",
            ((datetime.now() + timedelta(days=7)).isoformat(timespec="seconds"),))

        # ── 気分の色のHSL移行（2026-07-24 / v3.14）────────────────────
        # ピッカーがスウォッチ→HSLになったのに合わせ、既存のHEX値を "hsl(H, S%, L%)" へ
        # 一括変換する。冪等（HEXで始まる行だけ変換）。読む側は両対応なので取りこぼしても壊れない。
        _migrate_colors_to_hsl(db)

        # ── 気分の地図（Mood Night Map）の集計テーブル ────────────────
        # Postgres なら MATERIALIZED VIEW だが、たよりは SQLite なので普通のテーブルとして持ち、
        # 日次で作り直す（_refresh_mood_grid）。しきい値10通未満のセルはそもそも入れない。
        db.execute(
            """CREATE TABLE IF NOT EXISTS mood_grid (
                grid_id TEXT NOT NULL,
                mood    INTEGER NOT NULL,
                n       INTEGER NOT NULL,
                latest  TEXT,
                lat     REAL,
                lng     REAL,
                PRIMARY KEY (grid_id, mood)
            )""")
        # grid_id バックフィル: 丸めは Python の compute_grid_id と完全一致させる必要がある
        # （SQLite の CAST(ROUND(x,1) AS TEXT) は "33.600000000000001" 等の桁化けを起こすため使わない）。
        _backfill_grid_ids(db)

        # ── 10問アンケート → 未来への手紙（HTMXの並行フロー。既存 letters には一切触れない）──
        # letters / questions / answers の3テーブル構成。手紙本文はDBに持たず、
        # answers × questions.letter_fragment_template を封をする時に組み立てる（＝回答→一文の変換はDB側で管理）。
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS questions (
                id          INTEGER PRIMARY KEY,
                ord         INTEGER NOT NULL,
                prompt      TEXT NOT NULL,
                letter_fragment_template TEXT NOT NULL,
                is_required INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS survey_letters (
                id        TEXT PRIMARY KEY,
                user_id   TEXT NOT NULL,
                created   TEXT NOT NULL,
                sealed    INTEGER DEFAULT 0,
                sealed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS answers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                letter_id   TEXT NOT NULL,
                question_id INTEGER NOT NULL,
                value       TEXT,
                created     TEXT NOT NULL,
                UNIQUE(letter_id, question_id)
            );
            """
        )
        _seed_questions(db)

        try:
            db.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER DEFAULT 0")
            db.execute("UPDATE users SET email_verified=1 WHERE email IS NOT NULL AND email<>''")
        except sqlite3.OperationalError:
            pass

        try:
            db.execute("ALTER TABLE users ADD COLUMN onboarded INTEGER DEFAULT 0")
            db.execute("UPDATE users SET onboarded=1")
        except sqlite3.OperationalError:
            pass

        if db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] == 0:
            demo_id = secrets.token_hex(8)
            db.execute(
                "INSERT INTO users (id,username,pw_hash,created) VALUES (?,?,?,?)",
                (demo_id, "demo", _hash_pw("demo1234"), datetime.now().isoformat()),
            )
            today = date.today()
            s1_arrive = (today - timedelta(days=5)).isoformat() + "T09:00:00"
            s2_arrive = (today - timedelta(days=30)).isoformat() + "T09:00:00"
            
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

        # Admin アカウントの担保
        admin_pw = os.environ.get("TAYORI_ADMIN_PASSWORD")
        if not admin_pw:
            if os.environ.get("TAYORI_PRODUCTION") == "1":
                admin_pw = secrets.token_urlsafe(16)
                print(f"[警告] TAYORI_ADMIN_PASSWORD が未設定です。ランダムパスワードを設定しました: {admin_pw}")
            else:
                admin_pw = "admin.welcometotayori"

        admin_row = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()
        if admin_row is None:
            db.execute(
                "INSERT INTO users (id,username,pw_hash,created,email) VALUES (?,?,?,?,?)",
                (secrets.token_hex(8), "admin", _hash_pw(admin_pw),
                 datetime.now().isoformat(), None),
            )
        else:
            db.execute("UPDATE users SET pw_hash=? WHERE username='admin'",
                       (_hash_pw(admin_pw),))

        for r in db.execute("SELECT id FROM users WHERE unsub_token IS NULL OR unsub_token=''").fetchall():
            db.execute("UPDATE users SET unsub_token=? WHERE id=?", (secrets.token_urlsafe(16), r["id"]))

        db.commit()
        db.close()
        _init_db_done = True

def current_user():
    u = session.get("uid")
    if not u:
        return None
    return get_db().execute(
        "SELECT id,username,email,email_verified,onboarded,"
        "aggregate_opt_out,night_map_notice_seen_at FROM users WHERE id=?", (u,)
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

# 初回に必須で答えてもらう「はじめの問い」の数。0〜(CORE_ONBOARDING-1) がこれに当たる。
# 残り（CORE_ONBOARDING 以降）は「今夜の問い」として、少しずつ受信箱へ届ける。
CORE_ONBOARDING = 10

# 問いの配信ペース。コードを触らず環境変数で毎日／毎週を切り替えられる。
#   TAYORI_Q_INTERVAL_DAYS=1 … 何日ごとに配るか（1=毎日, 7=毎週）
#   TAYORI_Q_BATCH=1          … 一度に届ける問いの数（毎日なら1推奨）
#   TAYORI_Q_HOUR=21          … その日ぶんが「開封」できるようになる時刻（利用者の端末の時刻で判定）
def _q_int(name, default, lo, hi):
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except (ValueError, TypeError):
        return default

QUESTION_INTERVAL_DAYS = _q_int("TAYORI_Q_INTERVAL_DAYS", 1, 1, 60)
QUESTION_BATCH         = _q_int("TAYORI_Q_BATCH", 1, 1, 5)
QUESTION_RELEASE_HOUR  = _q_int("TAYORI_Q_HOUR", 21, 0, 23)

# 静的な問い(ONBOARDING_QUESTIONS)を配り切った後は、AIがその人向けに新しい問いを生成し続ける。
# 生成された問いは gen_questions 列に本文を保存し、id はこの基準値から採番して静的idと衝突させない。
GEN_ID_BASE = 100000

# AIが使えない／生成に失敗したときの予備の問い（枯れさせないための常緑の問い）。
FALLBACK_QUESTIONS = [
    "最近、誰にも言っていない小さな願いは何ですか。",
    "今日、心がふっとほどけた瞬間はありましたか。",
    "いま、いちばん会いたい人の顔を思い浮かべてみてください。誰でしたか。",
    "この頃、繰り返し考えてしまうことは何ですか。",
    "最後に声を出して笑ったのは、いつ、どんなときでしたか。",
    "手放したいのに、まだ手放せずにいるものはありますか。",
    "最近見た夢や、ふと浮かんだ空想を、ひとつ教えてください。",
    "今の自分に、ひとつだけ優しい言葉をかけるとしたら。",
    "この一週間で、いちばん静かだった時間はいつでしたか。",
    "今、少しだけ怖いと感じていることは何ですか。",
]


def _load_onboarding(raw):
    try:
        data = json.loads(raw) if raw else {}
        return {int(k): v for k, v in data.items() if str(v).strip()}
    except (ValueError, TypeError, AttributeError):
        return {}


def _load_weekly(raw):
    """問いの配信状態。batch=いま届いている問いのid, issued=これまで配信済みの全id,
    last_batch=最後に配信した日(ISO)。"""
    try:
        data = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    batch = [int(x) for x in data.get("batch", []) if isinstance(x, (int, str)) and str(x).isdigit()]
    issued = [int(x) for x in data.get("issued", []) if isinstance(x, (int, str)) and str(x).isdigit()]
    return {"batch": batch, "issued": issued, "last_batch": data.get("last_batch")}


def _weekly_pool():
    """配信する問いのプール（初回必須ぶんを除いた残り全部）。"""
    return list(range(CORE_ONBOARDING, len(ONBOARDING_QUESTIONS)))


def _load_gen(raw):
    """AI生成した問い。[{id:int, text:str}] のリスト。"""
    try:
        data = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        data = []
    out = []
    if isinstance(data, list):
        for it in data:
            if isinstance(it, dict) and "id" in it and it.get("text"):
                try:
                    out.append({"id": int(it["id"]), "text": str(it["text"])})
                except (ValueError, TypeError):
                    pass
    return out


def _gen_map(user_id):
    row = get_db().execute("SELECT gen_questions FROM users WHERE id=?", (user_id,)).fetchone()
    return {g["id"]: g["text"] for g in _load_gen(row["gen_questions"] if row else None)}


def _question_text(qid, gen_map=None):
    """id から問い文を引く。静的idなら ONBOARDING_QUESTIONS、それ以外は生成問い(gen_map)から。"""
    if 0 <= qid < len(ONBOARDING_QUESTIONS):
        return ONBOARDING_QUESTIONS[qid]
    if gen_map and qid in gen_map:
        return gen_map[qid]
    return None


def _issue_weekly_if_due(user_id):
    """その日の問いを配り、状態を返す。
    問いは「毎日の宿題」ではなく、便箋にそっと透ける“書き出しの呼び水”。
    ・答えるかどうかは任意。回答の有無に関係なく QUESTION_INTERVAL_DAYS 日ごとに次へ進む
    ・onboarded 直後は、その日ぶんをすぐ配る
    ・静的プールが尽きたらAIが生成して補う
    返り値: {batch:[id...], issued_at: 配信日ISO, exhausted: bool}"""
    db = get_db()
    row = db.execute("SELECT onboarding, weekly, onboarded FROM users WHERE id=?", (user_id,)).fetchone()
    if not row or not row["onboarded"]:
        return {"batch": [], "issued_at": None, "gated": False, "exhausted": False}
    answers = _load_onboarding(row["onboarding"])
    wk = _load_weekly(row["weekly"])
    pool_left = [q for q in _weekly_pool() if q not in wk["issued"] and q not in answers]

    def _persist(new_batch):
        wk["batch"] = new_batch
        wk["issued"] = sorted(set(wk["issued"]) | set(new_batch))
        wk["last_batch"] = date.today().isoformat()
        try:
            with _WRITE_LOCK:
                db.execute("UPDATE users SET weekly=? WHERE id=?",
                           (json.dumps(wk, ensure_ascii=False), user_id))
                db.commit()
        except sqlite3.OperationalError as e:
            print(f"[たより] weekly 書き込み失敗（再試行可）: {e}", flush=True)

    def _gated(batch):
        # 初回バッチ（過去に配ったものが無い）は時刻ゲート無しで即見せる。以降は21時ゲートを効かせる。
        return bool(set(wk["issued"]) - set(batch))

    # 次を配ってよいか（間隔を空ける。ただし一度も配っていなければ即配る）。
    # 透かしの問いは、答えなくても日々そっと入れ替わる（義務化しない・過去の問いに固執させない）。
    due = True
    if wk["last_batch"]:
        try:
            last = date.fromisoformat(str(wk["last_batch"])[:10])
            due = (date.today() - last).days >= QUESTION_INTERVAL_DAYS
        except ValueError:
            due = True
    if not due:
        cur = list(wk["batch"])
        return {"batch": cur, "issued_at": wk["last_batch"], "gated": _gated(cur), "exhausted": False}

    # まず静的プールから。足りなければ、その人向けの問いをAIで生成して補う（枯れさせない）。
    new_batch = pool_left[:QUESTION_BATCH]
    if len(new_batch) < QUESTION_BATCH:
        new_batch += _generate_weekly_questions(user_id, QUESTION_BATCH - len(new_batch))
    if not new_batch:
        return {"batch": [], "issued_at": wk["last_batch"], "gated": False, "exhausted": True}
    _persist(new_batch)
    return {"batch": new_batch, "issued_at": wk["last_batch"], "gated": _gated(new_batch),
            "exhausted": False}


def _generate_weekly_questions(user_id, n):
    """その人向けの新しい問いを n 個作り、gen_questions 列に保存して、採番した id のリストを返す。
    AIが使えれば persona と既出の問いを踏まえて生成、使えなければ常緑の予備から重複を避けて選ぶ。"""
    if n <= 0:
        return []
    db = get_db()
    row = db.execute("SELECT gen_questions, onboarding FROM users WHERE id=?", (user_id,)).fetchone()
    gen = _load_gen(row["gen_questions"] if row else None)
    answers = _load_onboarding(row["onboarding"] if row else None)

    # すでに尋ねた問い（静的＋生成）の文面一覧。重複回避に使う。
    asked = [ONBOARDING_QUESTIONS[q] for q in sorted(answers) if 0 <= q < len(ONBOARDING_QUESTIONS)]
    asked += [g["text"] for g in gen]
    asked_set = set(asked)

    def _clean(t):
        t = (t or "").strip().splitlines()[0].strip() if (t or "").strip() else ""
        return t.strip("「」\"'　 ").strip()[:60]

    made = []
    gemini_key = os.environ.get("GEMINI_API_KEY")
    claude_key = os.environ.get("ANTHROPIC_API_KEY")
    if AI_ENABLED and NETWORK_ENABLED and (gemini_key or claude_key):
        persona = _get_or_make_persona(user_id) or _profile_context_text(user_id)
        for _ in range(n):
            recent = asked[-12:]
            prompt = (
                "あなたは、ある人が自分自身と静かに向き合うための『問い』を、そっと一つ差し出す存在です。"
                "下記のその人の輪郭と、これまで尋ねた問いを踏まえ、まだ触れていない角度から新しい問いを1つだけ作ってください。\n\n"
                + (f"【その人の輪郭（内なる理解。問いの奥行きにだけ使い、言い当てない）】\n{persona}\n\n" if persona else "")
                + ("【すでに尋ねた問い（主題が重ならないように）】\n" + "\n".join("・" + q for q in recent) + "\n\n" if recent else "")
                + "―― 問いの約束 ――\n"
                "・その人の主観・記憶・感情に、そっと触れる問い。答えたくなるやわらかさで。\n"
                "・分析・診断・助言・励ましはしない。AIらしさを出さない。\n"
                "・抽象論ではなく、具体的な場面や情景を思い出させる問い。\n"
                "・過去の問いと似た主題・言い回しは避け、新しい入り口から。\n"
                "・40字以内、静かな敬体で1文（例：〜はありますか。／〜を、ひとつ。）。\n\n"
                "出力は、問いの文だけ。メタな注釈はつけないこと。"
            )
            text = None
            if gemini_key:
                try:
                    text = _gemini_question(prompt, gemini_key)
                except Exception as e:
                    print(f"[問い生成 Gemini失敗→フォールバック] {e}", flush=True)
            if not text and claude_key:
                try:
                    text = _claude_question(prompt, claude_key)
                except Exception as e:
                    print(f"[問い生成 Claude失敗→フォールバック] {e}", flush=True)
            text = _clean(text)
            if text and text not in asked_set:
                made.append(text)
                asked.append(text)
                asked_set.add(text)

    # AIで足りない/使えないぶんは、常緑の予備から重複を避けて補う。
    if len(made) < n:
        for q in FALLBACK_QUESTIONS:
            if len(made) >= n:
                break
            if q not in asked_set:
                made.append(q)
                asked_set.add(q)

    if not made:
        return []

    base = GEN_ID_BASE + len(gen)
    new_ids = []
    for i, text in enumerate(made):
        qid = base + i
        gen.append({"id": qid, "text": text})
        new_ids.append(qid)
    try:
        with _WRITE_LOCK:
            db.execute("UPDATE users SET gen_questions=? WHERE id=?",
                       (json.dumps(gen, ensure_ascii=False), user_id))
            db.commit()
    except sqlite3.OperationalError as e:
        print(f"[たより] gen_questions 書き込み失敗（再試行可）: {e}", flush=True)
        return []
    return new_ids


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("uid"):
            return jsonify(error="ログインしてください。", auth=False), 401
        return f(*a, **kw)
    return wrapper

def uid():
    return session["uid"]

@app.route("/")
def index():
    # Adminは一般ユーザーUI（投函・受信・年表）を使わない。管理ダッシュボードへ直行させる。
    u = current_user()
    if u and u["username"] == "admin":
        return redirect("/admin.welcometotayori")
    return render_template("index.html", open_letter_id="")

@app.route("/open/<lid>")
def open_letter_page(lid):
    safe = lid if re.fullmatch(r"[A-Za-z0-9]{1,32}", lid or "") else ""
    # 手紙の中身はユーザーの内面そのもの。検索には絶対に載せない。
    return render_template("index.html", open_letter_id=safe, robots="noindex,nofollow")

@app.route("/terms")
def terms_page():
    return render_template("terms.html")

@app.route("/privacy")
def privacy_page():
    return render_template("privacy.html")

# ピル型の紙＋朱の蝋封。ロゴ(static/img/logo.png)と同じ寸法・色。
# 円(r13.29)はピル内半径(14.12-枠0.4)より小さく枠線の内側に収める＝はみ出さない。
_FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
    "<rect width='100' height='100' fill='#EDE3D1'/>"
    "<rect x='10' y='35.88' width='80' height='28.24' rx='14.12' fill='#F2EBDD' "
    "stroke='#3A2E25' stroke-width='0.8'/>"
    "<circle cx='75.76' cy='50' r='13.29' fill='#AE5239'/>"
    "</svg>"
)

@app.route("/favicon.ico")
@app.route("/favicon.svg")
def favicon():
    resp = Response(_FAVICON_SVG, mimetype="image/svg+xml")
    # ロゴ調整中は短めに（Cloudflare/ブラウザに旧版が長く残らないよう）。
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/robots.txt")
def robots_txt():
    # 公開ページだけ許可し、手紙・API・管理・認証系は明示的に拒否する。
    body = (
        "User-agent: *\n"
        "Allow: /$\n"
        "Allow: /terms\n"
        "Allow: /privacy\n"
        "Disallow: /open/\n"
        "Disallow: /map\n"
        "Disallow: /api/\n"
        "Disallow: /admin.welcometotayori\n"
        "Disallow: /verify/\n"
        "Disallow: /unsubscribe/\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )
    resp = Response(body, mimetype="text/plain")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/sitemap.xml")
def sitemap_xml():
    # PUBLIC_PATHS を唯一の情報源にして、公開URLだけを列挙する。
    urls = "".join(
        f"<url><loc>{SITE_URL}{p}</loc><changefreq>weekly</changefreq></url>"
        for p in PUBLIC_PATHS
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>"
    )
    resp = Response(xml, mimetype="application/xml")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp

@app.route("/api/onboarding", methods=["GET"])
@login_required
def api_get_onboarding():
    row = get_db().execute(
        "SELECT onboarding,onboarded FROM users WHERE id=?", (uid(),)
    ).fetchone()
    answers = _load_onboarding(row["onboarding"] if row else None)
    # 「はじめの問い」は初回必須ぶん（先頭 CORE_ONBOARDING 問）だけを出す。
    # 残りは「今週の問い」として受信箱へ少しずつ届く（過去の問いに固執させない）。
    return jsonify(
        questions=[{"id": i, "text": q} for i, q in enumerate(ONBOARDING_QUESTIONS[:CORE_ONBOARDING])],
        answers={str(k): v for k, v in answers.items() if k < CORE_ONBOARDING},
        onboarded=bool(row["onboarded"]) if row else False,
    )


@app.route("/api/onboarding", methods=["POST"])
@login_required
def api_save_onboarding():
    data = request.get_json(force=True)
    incoming = data.get("answers") or {}
    db = get_db()
    row = db.execute("SELECT onboarding FROM users WHERE id=?", (uid(),)).fetchone()
    answers = _load_onboarding(row["onboarding"] if row else None)
    for k, v in incoming.items():
        try:
            qid = int(k)
        except (ValueError, TypeError):
            continue
        if not (0 <= qid < len(ONBOARDING_QUESTIONS)):
            continue
        text = (str(v) if v is not None else "").strip()[:300]
        if text:
            answers[qid] = text
        else:
            answers.pop(qid, None)
    done = 1 if data.get("done") else 0
    try:
        with _WRITE_LOCK:
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


@app.route("/api/weekly", methods=["GET"])
@login_required
def api_get_weekly():
    """いま届いている「今夜の問い」を返す。時刻ゲート(21時)は端末時刻で見せ方を変えるため、
    release_hour と issued_at をクライアントへ渡す。"""
    state = _issue_weekly_if_due(uid())
    gm = _gen_map(uid())
    qs = [{"id": q, "text": _question_text(q, gm)}
          for q in state["batch"] if _question_text(q, gm)]
    return jsonify(
        questions=qs,
        issued_at=state["issued_at"],
        gated=state.get("gated", True),
        release_hour=QUESTION_RELEASE_HOUR,
        exhausted=state["exhausted"],
    )


@app.route("/api/weekly/answer", methods=["POST"])
@login_required
def api_answer_weekly():
    """今夜の問いへの答えを保存する。保存先は onboarding と同じ辞書（personaが自動で厚くなる）。"""
    data = request.get_json(force=True)
    try:
        qid = int(data.get("qid"))
    except (ValueError, TypeError):
        return jsonify(error="問いが指定されていません。"), 400
    # 週次の静的問い(10〜)か、AI生成問い(gen_questionsに存在)だけ答えられる。初回必須(0〜9)は不可。
    is_weekly_static = (CORE_ONBOARDING <= qid < len(ONBOARDING_QUESTIONS))
    is_generated = qid in _gen_map(uid())
    if not (is_weekly_static or is_generated):
        return jsonify(error="その問いには答えられません。"), 400
    text = (str(data.get("text") or "")).strip()[:300]
    if not text:
        return jsonify(error="ことばが空です。"), 400
    db = get_db()
    row = db.execute("SELECT onboarding FROM users WHERE id=?", (uid(),)).fetchone()
    answers = _load_onboarding(row["onboarding"] if row else None)
    answers[qid] = text
    try:
        with _WRITE_LOCK:
            db.execute("UPDATE users SET onboarding=? WHERE id=?",
                       (json.dumps(answers, ensure_ascii=False), uid()))
            db.commit()
    except sqlite3.OperationalError as e:
        print(f"[たより] weekly answer 書き込み失敗（再試行可）: {e}", flush=True)
        return jsonify(error="いま少し混み合っています。数秒おいて、もう一度お試しください。"), 503
    # 答え終えたら、次の配信が来ているか判定して返す（時刻・間隔次第では空）
    state = _issue_weekly_if_due(uid())
    gm = _gen_map(uid())
    nxt = [{"id": q, "text": _question_text(q, gm)}
           for q in state["batch"] if _question_text(q, gm)]
    return jsonify(ok=True, questions=nxt, issued_at=state["issued_at"],
                   gated=state.get("gated", True),
                   release_hour=QUESTION_RELEASE_HOUR, exhausted=state["exhausted"])


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
    if not email:
        return jsonify(error="メールアドレスを入力してください。便りの到着をお知らせするために使います。"), 400
    if not EMAIL_RE.match(email):
        return jsonify(error="メールアドレスの形式が正しくありません。"), 400
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        return jsonify(error="その名前はもう使われています。"), 409
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        return jsonify(error="そのメールアドレスはすでに使われています。"), 409
    new_id = secrets.token_hex(8)
    pw_hash = _hash_pw(password)
    got = _WRITE_LOCK.acquire(timeout=20)
    if not got:
        return jsonify(error="いま混み合っています。数秒おいて、もう一度お試しください。"), 503
    try:
        db.execute(
            "INSERT INTO users (id,username,pw_hash,created,email,unsub_token) VALUES (?,?,?,?,?,?)",
            (new_id, username, pw_hash, datetime.now().isoformat(),
             email or None, secrets.token_urlsafe(16)),
        )
        db.commit()
    except sqlite3.OperationalError as e:
        print(f"[たより] register 書き込み失敗（再試行可）: {e}", flush=True)
        return jsonify(error="いま少し混み合っています。数秒おいて、もう一度お試しください。"), 503
    finally:
        _WRITE_LOCK.release()
    email_pending = False
    if email:
        _issue_email_verification(db, new_id, email, username)
        email_pending = True
    session.permanent = True
    session["uid"] = new_id
    return jsonify(ok=True, username=username, email=email or None,
                   email_verified=False, email_pending=email_pending,
                   onboarded=False)

@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row or not check_password_hash(row["pw_hash"], password):
        return jsonify(error="名前かパスワードが違います。"), 401
    try:
        if not str(row["pw_hash"]).startswith("pbkdf2:"):
            with _WRITE_LOCK:
                db.execute("UPDATE users SET pw_hash=? WHERE id=?", (_hash_pw(password), row["id"]))
                db.commit()
    except Exception as e:
        print(f"[たより] pw再ハッシュ失敗（継続）: {e}", flush=True)
    session.permanent = True
    session["uid"] = row["id"]
    keys = row.keys()
    return jsonify(ok=True, username=row["username"],
                   is_admin=(row["username"] == "admin"),
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
    keys = u.keys()
    return jsonify(auth=True, username=u["username"],
                   is_admin=(u["username"] == "admin"),
                   email=u["email"] if "email" in keys else None,
                   email_verified=bool(u["email_verified"]) if "email_verified" in keys else False,
                   onboarded=bool(u["onboarded"]) if "onboarded" in keys else True,
                   # 気分の地図: 集計オプトアウトの状態と、リリース告知を出すべきか
                   aggregate_opt_out=bool(u["aggregate_opt_out"]) if "aggregate_opt_out" in keys else False,
                   night_map_notice=not bool(u["night_map_notice_seen_at"]) if "night_map_notice_seen_at" in keys else True,
                   weather_enabled=NETWORK_ENABLED)


@app.route("/api/email", methods=["POST"])
@login_required
def api_set_email():
    data = request.get_json(force=True)
    email = (data.get("email") or "").strip()
    if email and not EMAIL_RE.match(email):
        return jsonify(error="メールアドレスの形式が正しくありません。"), 400
    db = get_db()
    if email:
        _issue_email_verification(db, uid(), email, current_user()["username"])
        with _WRITE_LOCK:
            db.execute("UPDATE letters SET notify_attempts=0, notify_failed=0 WHERE user_id=?", (uid(),))
            db.commit()
        return jsonify(ok=True, email=email, email_verified=False, email_pending=True)
    
    with _WRITE_LOCK:
        db.execute("UPDATE users SET email=NULL, email_verified=0, email_token=NULL, email_token_at=NULL WHERE id=?", (uid(),))
        db.commit()
    return jsonify(ok=True, email=None, email_verified=False)


@app.route("/api/account/name", methods=["POST"])
@login_required
def api_change_name():
    new = (request.get_json(force=True).get("username") or "").strip()
    if not USERNAME_RE.match(new):
        return jsonify(error="名前は2〜24文字で。漢字・かな・英数字と _ . - が使えます。"), 400
    db = get_db()
    cur = db.execute("SELECT username FROM users WHERE id=?", (uid(),)).fetchone()
    if not cur:
        return jsonify(error="ユーザーが見つかりません。"), 404
    if cur["username"] == "admin":
        return jsonify(error="管理者アカウントの名前は変更できません。"), 403
    if cur["username"] == new:
        return jsonify(ok=True, username=new)
    if db.execute("SELECT 1 FROM users WHERE username=? AND id<>?", (new, uid())).fetchone():
        return jsonify(error="その名前はもう使われています。"), 409
    with _WRITE_LOCK:
        db.execute("UPDATE users SET username=? WHERE id=?", (new, uid()))
        db.commit()
    return jsonify(ok=True, username=new)


@app.route("/api/account/password", methods=["POST"])
@login_required
def api_change_password():
    data = request.get_json(force=True)
    current = data.get("current") or ""
    new = data.get("new") or ""
    db = get_db()
    row = db.execute("SELECT pw_hash FROM users WHERE id=?", (uid(),)).fetchone()
    if not row or not check_password_hash(row["pw_hash"], current):
        return jsonify(error="いまのパスワードが違います。"), 401
    if len(new) < 8:
        return jsonify(error="新しいパスワードは8文字以上にしてください。"), 400
    with _WRITE_LOCK:
        db.execute("UPDATE users SET pw_hash=? WHERE id=?", (_hash_pw(new), uid()))
        db.commit()
    return jsonify(ok=True)


@app.route("/api/settings/aggregate-opt-out", methods=["GET", "POST"])
@login_required
def api_aggregate_opt_out():
    """気分の地図（A・B共通）の集計から自分の手紙を外す/戻す。
    ONにするとユーザーの既存・今後すべての手紙が対象（今後ぶんは投函時に写す）。
    集計テーブルは日次更新なので、地図への反映は即時ではない（意図的）。"""
    db = get_db()
    if request.method == "POST":
        out = 1 if request.get_json(force=True).get("opt_out") else 0
        with _WRITE_LOCK:
            db.execute("UPDATE users SET aggregate_opt_out=? WHERE id=?", (out, uid()))
            db.execute("UPDATE letters SET excluded_from_aggregate=? WHERE user_id=?", (out, uid()))
            db.commit()
        return jsonify(ok=True, opt_out=bool(out))
    row = db.execute(
        "SELECT COALESCE(aggregate_opt_out,0) AS o FROM users WHERE id=?", (uid(),)).fetchone()
    return jsonify(opt_out=bool(row and row["o"]))


@app.route("/api/settings/night-map-notice-seen", methods=["POST"])
@login_required
def api_night_map_notice_seen():
    """気分の地図リリース告知を一度出したら既読にする（再表示しない）。"""
    db = get_db()
    with _WRITE_LOCK:
        db.execute(
            "UPDATE users SET night_map_notice_seen_at=? "
            "WHERE id=? AND night_map_notice_seen_at IS NULL",
            (datetime.now().isoformat(timespec="seconds"), uid()))
        db.commit()
    return jsonify(ok=True)


def _is_arrived(row):
    keys = row.keys() if hasattr(row, "keys") else []
    # デモ手紙の上書き開封日時は天気待ちより優先（デモ操作で自由に開けられるようにするため）
    if "demo_mode" in keys and row["demo_mode"] and row["demo_arrive_at"]:
        return datetime.fromisoformat(row["demo_arrive_at"]) <= datetime.now()
    if "weather_event" in keys and row["weather_event"]:
        met = row["weather_met_at"] if "weather_met_at" in keys else None
        if met:
            return datetime.fromisoformat(met) <= datetime.now()
        return False
    arrive_at = row["arrive_at"] or (row["arrive_date"] + "T00:00:00")
    return datetime.fromisoformat(arrive_at) <= datetime.now()


def _letter_opened(row):
    """開封済みかどうか。opened_at の有無が唯一の真実だが、opened_at 列が無い時代に
    開封された旧データ（opened=1・opened_at=NULL）も開封済みとして扱う（再封印しない）。"""
    keys = row.keys() if hasattr(row, "keys") else []
    if "opened_at" in keys and row["opened_at"]:
        return True
    return bool(row["opened"])


def letter_to_dict(row, include_thread=True):
    d = dict(row)
    d.pop("user_id", None)
    # タイプ再生のデータ(trace)は重いので一覧では本体を送らず、有無のフラグだけにする。
    # 本体は GET /api/letters/<id>/trace で再生時に取りにいく。
    _trace = d.pop("trace", None)
    d["has_trace"] = bool(_trace)
    d["emos"] = json.loads(d.get("emos") or "[]")
    d["arrive_hidden"] = bool(d["arrive_hidden"])
    d["opened"] = bool(d["opened"])
    d["from_reply"] = bool(d["from_reply"])
    d["vertical"] = bool(d.get("vertical"))  # 縦書きで封入された手紙
    d["demo_mode"] = bool(d.get("demo_mode"))  # デモ用（開封予定日時を自由に動かせる）
    d["arrived"] = _is_arrived(row)
    
    if d.get("seal_env"): d["seal_env"] = json.loads(d["seal_env"])
    if d.get("open_env"): d["open_env"] = json.loads(d["open_env"])
    
    if include_thread:
        rows = get_db().execute(
            "SELECT who,text,created,created_at,kind,time_bucket,env FROM thread WHERE letter_id=? ORDER BY id",
            (d["id"],)).fetchall()
        thread = []
        for r in rows:
            m = dict(r)
            try:
                m["env"] = json.loads(m["env"]) if m.get("env") else None
            except (TypeError, ValueError):
                m["env"] = None
            thread.append(m)
        d["thread"] = thread
    return d

def own_letter(lid):
    return get_db().execute("SELECT * FROM letters WHERE id=? AND user_id=?", (lid, uid())).fetchone()


def _sealed_card_fields(row):
    """封印カード（sealed / openable）に出してよいメタデータだけを束ねる。本文(poem)は絶対に含めない。
    出すのは字数・向き・気分の色・封じた日の天気/場所/時間帯だけ（本文の形は一切漏らさない）。"""
    keys = row.keys()
    poem = row["poem"] or ""
    env = None
    if "seal_env" in keys and row["seal_env"]:
        try:
            env = json.loads(row["seal_env"])
        except (TypeError, ValueError):
            env = None
    return {
        "char_count": len(poem),
        "vertical": bool(row["vertical"]) if "vertical" in keys else False,
        "seal_color": row["seal_color"] if "seal_color" in keys else None,
        "seal_env": env,
        "area_name": row["area_name"] if "area_name" in keys else None,
        "time_bucket": row["time_bucket"] if "time_bucket" in keys else None,
    }


def openable_meta(row):
    """開封日が来た・まだ開けていない手紙のカード。sealed と同じく本文はネットワークに一切流さない
    （本文は POST /api/letters/<id>/open のレスポンスで初めて配信される）。"""
    keys = row.keys()
    m = {
        "id": row["id"],
        "sent_date": row["sent_date"],
        "arrive_date": row["arrive_date"],
        "arrive_at": row["arrive_at"],
        "arrive_label": row["arrive_label"],
        "arrive_hidden": bool(row["arrive_hidden"]),
        "opened": False,
        "openable": True,
        "arrived": True,
        "from_reply": bool(row["from_reply"]),
        "weather_event": row["weather_event"] if "weather_event" in keys else None,
        "demo_mode": bool(row["demo_mode"]) if "demo_mode" in keys else False,
        "demo_arrive_at": row["demo_arrive_at"] if "demo_arrive_at" in keys else None,
        "has_photo": bool(row["photo"]),
        "has_voice": bool(row["voice"]),
    }
    m.update(_sealed_card_fields(row))
    return m


def sealed_meta(row):
    keys = row.keys()
    demo_mode = bool(row["demo_mode"]) if "demo_mode" in keys else False
    demo_at = row["demo_arrive_at"] if (demo_mode and "demo_arrive_at" in keys) else None
    arrive_at = demo_at or row["arrive_at"] or (row["arrive_date"] + "T00:00:00")
    dt = datetime.fromisoformat(arrive_at)
    wevent = row["weather_event"] if "weather_event" in keys else None
    out = {
        "id": row["id"],
        "sent_date": row["sent_date"],
        "arrive_date": row["arrive_date"],
        "arrive_label": row["arrive_label"],
        "arrive_hidden": bool(row["arrive_hidden"]),
        "seconds_left": int((dt - datetime.now()).total_seconds()),
        "weather_event": wevent,
        # デモの上書き日時がある間はカウントダウン表示にする（天気待ち表示にしない）
        "waiting_weather": bool(wevent) and not demo_at,
        "has_photo": bool(row["photo"]),
        "has_voice": bool(row["voice"]),
        "from_reply": bool(row["from_reply"]),
        "demo_mode": demo_mode,
        "arrive_at": arrive_at,  # デモの日時編集の初期値（上書き中は上書き後の値）
    }
    # 封印カード（§3-1）の表示情報：投函日・場所・時間帯・天気・気分の色・「◯字を封じた」＋墨の塊
    out.update(_sealed_card_fields(row))
    return out

def _smtp_config():
    user = os.environ.get("TAYORI_SMTP_USER")
    pw = os.environ.get("TAYORI_SMTP_PASS")
    if not NETWORK_ENABLED or not user or not pw:
        return None
    return {
        "host": os.environ.get("TAYORI_SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("TAYORI_SMTP_PORT", "587")),
        "user": user,
        "pw": pw,
        "from": os.environ.get("TAYORI_MAIL_FROM") or formataddr(("tayori-たより-", user)),
    }


def _html_email(body, unsubscribe_url=None):
    """プレーン本文から、素朴で清潔なHTML版を作る（URLはリンク化）。到達率と見た目のため。"""
    safe = html.escape(body)
    safe = re.sub(r'https?://[^\s<]+',
                  lambda m: f'<a href="{m.group(0)}" style="color:#B5543A;text-decoration:underline">{m.group(0)}</a>',
                  safe).replace("\n", "<br>")
    foot = ""
    if unsubscribe_url:
        foot = (f'<div style="margin-top:24px;font-size:12px;color:#9c8f7c">'
                f'このお知らせを止める：<a href="{unsubscribe_url}" style="color:#9c8f7c">配信を停止</a></div>')
    return (
        '<div style="background:#F2EBDD;padding:30px 16px;'
        "font-family:'Hiragino Mincho ProN','Yu Mincho',serif;color:#3A2E25\">"
        '<div style="max-width:480px;margin:0 auto;background:#EDE3D1;border:1px solid #CBBBA0;'
        'border-radius:4px;padding:30px 26px">'
        '<div style="font-size:25px;letter-spacing:0.14em;margin-bottom:16px">tayori-たより-</div>'
        f'<div style="font-size:15px;line-height:2.0">{safe}</div>'
        f'{foot}'
        '</div></div>'
    )


def send_email(to_addr, subject, body, unsubscribe_url=None):
    cfg = _smtp_config()
    if not cfg:
        print("\n―― [メール通知・擬似送信] ――――――――――――")
        print(f"  宛先: {to_addr}")
        print(f"  件名: {subject}")
        print(f"  本文: {body}")
        print("――――――――――――――――――――――――\n")
        return True
    try:
        # text + HTML のマルチパート（プレーン単体よりスパム判定されにくく、見た目も整う）
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        from_name, from_addr = parseaddr(cfg["from"])
        msg["From"] = formataddr((from_name, from_addr)) if from_addr else cfg["from"]
        msg["To"] = to_addr
        msg["Date"] = formatdate(localtime=True)
        _dom = from_addr.split("@")[-1] if from_addr and "@" in from_addr else None
        msg["Message-ID"] = make_msgid(domain=_dom) if _dom else make_msgid()
        if from_addr:
            msg["Reply-To"] = from_addr
        # 配信停止ヘッダ（Gmail/iCloud が信頼の手がかりにする。ワンクリック対応）
        if unsubscribe_url:
            msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(_html_email(body, unsubscribe_url), "html", "utf-8"))
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


EMAIL_TOKEN_TTL = timedelta(days=7)
MAX_NOTIFY_ATTEMPTS = 5


def _issue_email_verification(db, user_id, email, username):
    token = secrets.token_urlsafe(24)
    with _WRITE_LOCK:
        db.execute(
            "UPDATE users SET email=?, email_verified=0, email_token=?, email_token_at=?, notify_enabled=1 WHERE id=?",
            (email, token, datetime.now().isoformat(timespec="seconds"), user_id),
        )
        db.commit()
    verify_url = f"{BASE_URL}/verify/{token}"
    subject = "tayori-たより- — メールアドレスの確認"
    body = (
        f"{username} さんへ。\n"
        "tayori-たより- の通知メールを、このアドレスで受け取る設定をしました。\n"
        "下のリンクを開いて、確認を完了してください（7日間有効）。\n"
        f"{verify_url}\n"
    )
    threading.Thread(target=send_email, args=(email, subject, body), daemon=True).start()
    return True


def _landing_page(title, message, ok=True):
    color = "#6B8478" if ok else "#B5543A"
    safe_msg = html.escape(message).replace("&lt;br&gt;", "<br>")
    return (
        "<!doctype html><html lang=ja><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{title} — tayori-たより-</title><style>"
        "body{background:#F2EBDD;color:#3A2E25;font-family:'Hiragino Mincho ProN',serif;"
        "display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:24px}"
        ".card{max-width:380px;text-align:center;background:#EDE3D1;border:1px solid #CBBBA0;"
        "border-radius:4px;padding:36px 28px;box-shadow:0 10px 30px -18px rgba(58,46,37,.5)}"
        "h1{font-size:34px;letter-spacing:.18em;margin:0 0 6px}"
        f".m{{color:{color};font-size:15px;letter-spacing:.05em;line-height:1.95;margin-top:14px}}"
        "a{color:#B5543A}</style></head><body><div class=card><h1>たより</h1>"
        f"<div class=m>{safe_msg}</div>"
        f"<p style='margin-top:22px'><a href='{BASE_URL}/'>戻る →</a></p>"
        "</div></body></html>"
    )


@app.route("/verify/<token>")
def verify_email(token):
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
    with _WRITE_LOCK:
        db.execute("UPDATE users SET email_verified=1, email_token=NULL, email_token_at=NULL WHERE id=?", (row["id"],))
        db.commit()
    return _landing_page("確認完了", "メールアドレスを確認しました。<br>便りが届く頃に、そっとお知らせが届きます。")


@app.route("/unsubscribe/<token>")
def unsubscribe(token):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{8,80}", token or ""):
        return _landing_page("配信停止", "リンクが正しくありません。", ok=False), 400
    db = get_db()
    row = db.execute("SELECT id FROM users WHERE unsub_token=?", (token,)).fetchone()
    if not row:
        return _landing_page("配信停止", "このリンクは無効です。", ok=False), 404
    with _WRITE_LOCK:
        db.execute("UPDATE users SET notify_enabled=0 WHERE id=?", (row["id"],))
        db.commit()
    return _landing_page("配信停止", "通知メールの配信を停止しました。<br>再開したいときは、アプリの📧設定からメールを登録し直してください。")


def _temp_tag(temp):
    return "hot" if temp >= 28 else ("cold" if temp <= 13 else "normal")


def _fetch_weather_open_meteo(lat, lon):
    import urllib.request
    # current= 形式で湿度も取る（湿度は封入インクの「滲み」の素になる）。
    # 降水・気圧は開封演出（にじみの収束時間・墨の粒状感）の素として投函時に凍結する。
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           f"&current=temperature_2m,relative_humidity_2m,weather_code,precipitation,surface_pressure")
    req = urllib.request.Request(url, headers={"User-Agent": "tayori/1.0"})
    with urllib.request.urlopen(req, timeout=4) as response:
        data = json.loads(response.read().decode())
    cw = data.get("current", {})
    code = cw.get("weather_code", 0)
    temp = cw.get("temperature_2m", 20.0)
    humidity = cw.get("relative_humidity_2m")
    precip = cw.get("precipitation")
    pressure = cw.get("surface_pressure")
    condition = "clear"
    if code in [71, 73, 75, 77, 85, 86]:
        condition = "snow"
    elif code in [51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99]:
        condition = "rain"
    elif code in [45, 48]:
        condition = "fog"
    elif code in [1, 2, 3]:
        condition = "cloud"
    return {"condition": condition, "temp": temp, "tag": _temp_tag(temp),
            "humidity": humidity, "precip": precip, "pressure": pressure}


def _fetch_weather_owm(lat, lon, api_key):
    import urllib.request
    url = (f"https://api.openweathermap.org/data/2.5/weather"
           f"?lat={lat}&lon={lon}&units=metric&appid={api_key}")
    req = urllib.request.Request(url, headers={"User-Agent": "tayori/1.0"})
    with urllib.request.urlopen(req, timeout=4) as response:
        data = json.loads(response.read().decode())
    temp = (data.get("main") or {}).get("temp", 20.0)
    humidity = (data.get("main") or {}).get("humidity")
    pressure = (data.get("main") or {}).get("pressure")
    precip = (data.get("rain") or {}).get("1h") or (data.get("snow") or {}).get("1h") or 0
    wid = ((data.get("weather") or [{}])[0]).get("id", 800)
    if 600 <= wid < 700:
        condition = "snow"
    elif 200 <= wid < 600:
        condition = "rain"
    elif 700 <= wid < 800:
        condition = "fog"
    elif 801 <= wid < 810:
        condition = "cloud"
    else:
        condition = "clear"
    return {"condition": condition, "temp": temp, "tag": _temp_tag(temp),
            "humidity": humidity, "precip": precip, "pressure": pressure}


def fetch_weather(lat, lon):
    if not NETWORK_ENABLED:
        return None
    owm_key = os.environ.get("TAYORI_OWM_KEY")
    if owm_key:
        try:
            return _fetch_weather_owm(lat, lon, owm_key)
        except Exception as e:
            print(f"[天気取得失敗:OWM→Open-Meteoへ] {e}")
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
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        ip = xff.split(",")[0].strip()
        if ip:
            return ip
    return request.remote_addr or ""


def _ip_geolocate(client_ip=None):
    if not NETWORK_ENABLED:
        return None
    import urllib.request
    def _is_public(ip):
        return ip and not (ip.startswith(("10.", "127.", "192.168.", "172.16.",
                                          "172.17.", "172.18.", "172.19.", "172.2",
                                          "172.30.", "172.31.", "::1", "fc", "fd"))
                           or ip == "localhost")
    target = client_ip if _is_public(client_ip) else ""
    try:
        url = f"https://ipwho.is/{target}"
        with urllib.request.urlopen(url, timeout=4) as r:
            d = json.loads(r.read().decode())
        if d.get("success") and d.get("latitude") is not None:
            return d["latitude"], d["longitude"], d.get("city")
    except Exception as e:
        print(f"[IP位置推定失敗] {e}", flush=True)
    return None


def _weather_matches(event, wx):
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
    if not NETWORK_ENABLED:
        return
    db = _connect()
    try:
        rows = db.execute(
            """SELECT l.id AS lid, l.weather_event AS event, l.arrive_at AS arrive_at,
                      u.last_lat AS lat, u.last_lon AS lon
               FROM letters l JOIN users u ON u.id = l.user_id
               WHERE l.weather_event IS NOT NULL AND l.weather_event<>''
                 AND (l.weather_met_at IS NULL OR l.weather_met_at='')"""
        ).fetchall()
        wx_cache = {}
        now = datetime.now()
        for r in rows:
            try:
                if r["arrive_at"] and datetime.fromisoformat(r["arrive_at"]) > now:
                    continue
            except ValueError:
                pass
            if not r["lat"] or not r["lon"]:
                continue
            key = (r["lat"], r["lon"])
            if key not in wx_cache:
                wx_cache[key] = fetch_weather(r["lat"], r["lon"])
            wx = wx_cache[key]
            if _weather_matches(r["event"], wx):
                with _WRITE_LOCK:
                    db.execute("UPDATE letters SET weather_met_at=? WHERE id=?",
                               (now.isoformat(timespec="seconds"), r["lid"]))
                    db.commit()
                print(f"[天気待ち伏せ成立] {r['event']} → 便り {r['lid']} が届きました")
    except Exception as e:
        print(f"[天気待ち伏せチェックでエラー] {e}")
    finally:
        db.close()


def _check_and_notify():
    db = _connect()
    try:
        now = datetime.now()
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
                        continue
                except ValueError:
                    continue
            open_url = f"{BASE_URL}/open/{r['lid']}"
            unsub_url = f"{BASE_URL}/unsubscribe/{r['unsub']}" if r["unsub"] else None
            subject = "たより — 便りが、届きました"
            body = (
                f"{r['username']} さんへ。\n"
                "過去のあなたが封をしたたよりが、いま届きました。\n"
                "封の中身は、まだあなたも見ていません。\n"
                "下のリンクをひらいて、封蝋をそっとほどいてください。\n"
                f"{open_url}\n\n"
                "tayori ーたより\n"
                + (f"\n通知を止めるには: {unsub_url}\n" if unsub_url else "")
            )
            if send_email(r["email"], subject, body, unsubscribe_url=unsub_url):
                with _WRITE_LOCK:
                    db.execute("UPDATE letters SET notified=1 WHERE id=?", (r["lid"],))
                    db.commit()
            else:
                attempts = r["attempts"] + 1
                failed = 1 if attempts >= MAX_NOTIFY_ATTEMPTS else 0
                with _WRITE_LOCK:
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
    global _notify_started
    if os.environ.get("TAYORI_DISABLE_NOTIFIER") == "1":
        print("[たより] 通知ループは TAYORI_DISABLE_NOTIFIER=1 のため停止中", flush=True)
        return
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

    # 起動直後の猶予。デプロイ直後はワーカー起動と新規登録が重なりやすく、背景のDB処理が
    # 登録/オンボの読み書きと競合すると「設問0件」等になり得る。背景ループの最初の一手を
    # この秒数だけ遅らせ、起動直後の数十秒は登録処理にDBを譲る。
    try:
        grace = float(os.environ.get("TAYORI_STARTUP_GRACE", "12"))
    except ValueError:
        grace = 12.0

    def notify_loop():
        time.sleep(grace)
        while True:
            try:
                _check_weather_events()
                _check_and_notify()
            except Exception as e:
                print(f"[たより] 通知ループでエラー（継続）: {e}", flush=True)
            time.sleep(interval)

    def maintenance_loop():
        # 起動直後にいきなりS3バックアップを走らせない（従来は last_backup=0 で初回即実行→
        # 起動直後の登録とDBで競合する温床だった）。初回は起動から約5分後にずらす。
        last_backup = time.time() - backup_hours * 3600 + 300
        last_dissolve = 0.0
        last_mood_grid = 0.0    # 気分の地図の集計。0.0 起点で起動直後に一度作る
        time.sleep(grace + 8)   # persist は notifier より少し後ろにずらす
        while True:
            try:
                if _LOCAL_CACHE:
                    _persist_to_durable()
            except Exception as e:
                print(f"[たより] 永続化でエラー（継続）: {e}", flush=True)
            try:
                # ほどけるまで: 7日を過ぎた紙玉を色片へ還す（1時間ごと・読み取り時の遅延溶解が保険）
                if time.time() - last_dissolve >= 3600:
                    last_dissolve = time.time()
                    _db = _connect()
                    try:
                        n = _dissolve_scraps(_db)
                        if n:
                            print(f"[たより] ほどけるまで: {n}片が色片に還りました", flush=True)
                    finally:
                        _db.close()
            except Exception as e:
                print(f"[たより] 溶解バッチでエラー（継続）: {e}", flush=True)
            try:
                # 気分の地図: 集計テーブルを日次で作り直す（即時反映しないのが設計）
                if time.time() - last_mood_grid >= 86400:
                    last_mood_grid = time.time()
                    _db = _connect()
                    try:
                        c = _refresh_mood_grid(_db)
                        print(f"[たより] 気分の地図: {c}セルを更新", flush=True)
                    finally:
                        _db.close()
            except Exception as e:
                print(f"[たより] 気分の地図の更新でエラー（継続）: {e}", flush=True)
            try:
                if _backup_s3_config() and (time.time() - last_backup) >= backup_hours * 3600:
                    ok = _run_backup_to_s3()
                    last_backup = time.time() if ok else (time.time() - backup_hours * 3600 + 3600)
            except Exception as e:
                print(f"[たより] バックアップ判定でエラー（継続）: {e}", flush=True)
            time.sleep(_PERSIST_SECONDS)

    threading.Thread(target=notify_loop, daemon=True, name="tayori-notifier").start()
    threading.Thread(target=maintenance_loop, daemon=True, name="tayori-persist").start()
    _bk = "・オフサイトBK有効" if _backup_s3_config() else ""
    _pc = f"・永続化{_PERSIST_SECONDS}秒ごと(別スレッド)" if _LOCAL_CACHE else ""
    print(f"[たより] 便りのチェックを開始しました（{interval}秒ごと · 天気待ち伏せ＋メール通知{_bk}{_pc}）", flush=True)


@app.route("/api/weather")
def api_weather():
    lat, lon = request.args.get("lat"), request.args.get("lon")
    approx, city = False, None

    if not lat or not lon:
        if not NETWORK_ENABLED:
            return jsonify(ok=False, disabled=True, error="天気機能は現在オフです")
        ip = _ip_geolocate(_client_ip())
        if not ip:
            return jsonify(ok=False, error="位置を推定できませんでした")
        lat, lon, city = str(ip[0]), str(ip[1]), ip[2]
        approx = True

    if session.get("uid"):
        try:
            with _WRITE_LOCK:
                get_db().execute("UPDATE users SET last_lat=?, last_lon=? WHERE id=?",
                                 (lat, lon, session["uid"]))
                get_db().commit()
        except Exception:
            pass

    if not NETWORK_ENABLED:
        return jsonify(ok=False, disabled=True, error="天気機能は現在オフです")

    wx = fetch_weather(lat, lon)
    if not wx:
        return jsonify(ok=False, error="天気が取得できませんでした"), 500
    return jsonify(ok=True, temp=wx["temp"], condition=wx["condition"], temp_tag=wx["tag"],
                   humidity=wx.get("humidity"), precip=wx.get("precip"), pressure=wx.get("pressure"),
                   approx=approx, city=city)


# ── 封じた場所のエリア変換（Nominatim逆ジオコーディングのプロキシ）──
# 受け取った生座標は変換にだけ使い、保存もログも一切しない。
# 返すのはエリア名と、その「エリアの代表座標」（＝ユーザーの実座標ではない）。
_NOMINATIM_LOCK = threading.Lock()
_nominatim_last = 0.0
# 日本ではsuburbに町名が入ることが多いが保証はないため、狭い順にフォールバックする。
_AREA_KEYS = ("neighbourhood", "quarter", "suburb", "city_district",
              "town", "village", "city")


@app.route("/api/reverse-geocode", methods=["POST"])
@login_required
def api_reverse_geocode():
    data = request.get_json(force=True, silent=True) or {}
    try:
        lat, lng = float(data.get("lat")), float(data.get("lng"))
    except (TypeError, ValueError):
        return jsonify(area_name=None)
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return jsonify(area_name=None)
    if not NETWORK_ENABLED:
        return jsonify(area_name=None)

    # Nominatim利用規約（1req/秒）: 直前の呼び出しから1秒未満なら待ってから叩く。
    global _nominatim_last
    with _NOMINATIM_LOCK:
        wait = 1.0 - (time.monotonic() - _nominatim_last)
        if wait > 0:
            time.sleep(wait)
        _nominatim_last = time.monotonic()

    url = ("https://nominatim.openstreetmap.org/reverse"
           f"?format=jsonv2&lat={lat}&lon={lng}&accept-language=ja")
    req = urllib.request.Request(
        url, headers={"User-Agent": "tayori/1.0 (https://www.tayori-letter.com)"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read().decode())
    except Exception:
        # 失敗しても封緘フローは止めない。座標が残るためエラー詳細もログに出さない。
        return jsonify(area_name=None)

    addr = d.get("address") or {}
    area = next((addr[k] for k in _AREA_KEYS if addr.get(k)), None)
    try:
        # 結果オブジェクト側の座標＝エリアの代表点。念のため約100m（小数第3位）に丸める。
        area_lat = round(float(d.get("lat")), 3)
        area_lng = round(float(d.get("lon")), 3)
    except (TypeError, ValueError):
        area_lat = area_lng = None
    if not area or area_lat is None or area_lng is None:
        return jsonify(area_name=None)
    return jsonify(area_name=str(area)[:80], area_lat=area_lat, area_lng=area_lng)


@app.route("/api/locate", methods=["POST"])
@login_required
def api_locate():
    data = request.get_json(force=True)
    lat, lon = data.get("lat"), data.get("lon")
    if lat is None or lon is None:
        return jsonify(error="位置がありません"), 400
    with _WRITE_LOCK:
        get_db().execute("UPDATE users SET last_lat=?, last_lon=? WHERE id=?",
                         (str(lat), str(lon), uid()))
        get_db().commit()
    return jsonify(ok=True)


# ── 封じた場所の地図 ─────────────────────────────────────────────
# 手紙を封じた場所が、事後的に点になるだけの地図。
# 出すのはエリア名・時間帯・点の存在のみ。本文・日付・開封状態には一切つながない。

@app.route("/map")
def map_page():
    guard = _page_login_guard()
    if guard:
        return guard
    # ShadeMap（日照シミュレーション）のAPIキー。無ければ地図は素のOSMのまま成立する。
    return render_template("map.html",
                           shademap_key=os.environ.get("TAYORI_SHADEMAP_KEY", ""))


@app.route("/api/map")
@login_required
def api_map():
    # 手紙のidは返さない（地図から本文へたどる経路を持たせない）。
    # 非対称表示：未開封の点は「場所名」だけ（封の中の時間・空気は開封まで明かさない）。
    # 開封済みの点は「封をした日時・時間帯・その時の天気」＝時間と空気が主役になる。
    # 天気は表示の有無に関わらず封緘時に seal_env として必ず記録済み（表示と記録は分離）。
    #
    # ?until=<ISO日時> を渡すと「その日時点の地図」を返す（開封時の「あの日と今日」の見比べ用）。
    # 当時まだ無かった点は返さず、当時まだ開封されていなかった点は未開封の姿（場所名のみ）で返す。
    # 日付の絞り込みはサーバ側で行い、未開封の点の封緘日時をクライアントへ渡さない原則は崩さない。
    until = (request.args.get("until") or "").strip()[:32] or None
    rows = get_db().execute(
        """SELECT area_name, area_lat, area_lng, time_bucket, opened, opened_at, sent_date, seal_env,
                  open_area_name, open_area_lat, open_area_lng
           FROM letters
           WHERE user_id=? AND area_name IS NOT NULL
             AND area_lat IS NOT NULL AND area_lng IS NOT NULL""",
        (uid(),)).fetchall()
    points = []
    for r in rows:
        if until:
            # sent_date / opened_at はどちらもISO文字列なので文字列比較で時系列になる
            if (r["sent_date"] or "") > until:
                continue  # その日にはまだ封をしていなかった点
            opened_then = bool(r["opened"]) and bool(r["opened_at"]) and r["opened_at"] <= until
        else:
            opened_then = bool(r["opened"])
        p = {"area_name": r["area_name"], "area_lat": r["area_lat"],
             "area_lng": r["area_lng"], "opened": opened_then}
        if opened_then:
            p["sent_at"] = r["sent_date"]
            p["time_bucket"] = r["time_bucket"]
            # 開いた場所（エリア名＋丸め座標のみ）。開封の弧（封をした場所⇔開いた場所）の材料。
            # 未開封の点には付けない＝非対称表示の原則そのまま。
            p["open_area_name"] = r["open_area_name"]
            p["open_area_lat"] = r["open_area_lat"]
            p["open_area_lng"] = r["open_area_lng"]
            try:
                env = json.loads(r["seal_env"]) if r["seal_env"] else None
            except (TypeError, ValueError):
                env = None
            if env:
                p["weather"] = {"condition": env.get("condition"), "temp": env.get("temp")}
        points.append(p)
    return jsonify(points=points)


@app.route("/api/map/moods")
@login_required
def api_map_moods():
    # 気分の地図（エリア単位 aura）。サーバから返すのはエリアに集約済みのものだけ：
    #   ・座標の生値・手紙id・本文は載せない（center/bbox はエリア内の丸め済み座標から出す）
    #   ・moods は開封済みの手紙のみ。封をしたままの気分色はサーバから出さない（開封で初めて色が出る）
    #   ・件数は返さない。moods の配列長がそのまま件数になるため、返す前にシャッフルし
    #     8件を超えたら8件に切る（色の混ざり具合は8件あれば十分収束する）
    #   ・has_sealed は真偽値のみ。数は出さない
    # ?until=<ISO日時> は /api/map と同じ「あの日の地図」用（開封状態も当時の姿で判定）。
    until = (request.args.get("until") or "").strip()[:32] or None
    rows = get_db().execute(
        """SELECT area_name, area_lat, area_lng, opened, opened_at, sent_date, seal_color
           FROM letters
           WHERE user_id=? AND area_name IS NOT NULL
             AND area_lat IS NOT NULL AND area_lng IS NOT NULL""",
        (uid(),)).fetchall()
    areas = {}
    for r in rows:
        if until and (r["sent_date"] or "") > until:
            continue   # その日にはまだ封をしていなかった
        if until:
            opened = bool(r["opened"]) and bool(r["opened_at"]) and r["opened_at"] <= until
        else:
            opened = bool(r["opened"])
        a = areas.setdefault(r["area_name"], {"lats": [], "lngs": [], "moods": [], "sealed": False})
        a["lats"].append(r["area_lat"])
        a["lngs"].append(r["area_lng"])
        if opened:
            m = _mood_index(r["seal_color"])
            if m is not None:
                a["moods"].append(_MOOD_SLUGS[m])
        else:
            a["sealed"] = True
    out = []
    for name, a in areas.items():
        random.shuffle(a["moods"])
        out.append({
            "id": name, "label": name,
            "center": [round(sum(a["lats"]) / len(a["lats"]), 3),
                       round(sum(a["lngs"]) / len(a["lngs"]), 3)],
            "bbox": [[min(a["lats"]), min(a["lngs"])], [max(a["lats"]), max(a["lngs"])]],
            "moods": a["moods"][:8],
            "has_sealed": a["sealed"],
        })
    return jsonify(areas=out)


# ──「言葉の編み物」（/archive・全レター横断のパッチワーク）と「地の糸」（他者moodの気配）は
#    2026-07-24 に機能ごと削除。屑籠の7日溶解（本文と筆跡が消える不可逆の仕組み）はそのまま。
#    色片(woven_scraps)テーブルと溶解時の書き込みも温存する（非破壊・将来の眺めの余地のため）。

# 気分7色（地図の量子化パレット）。
# 並びは「静→明→暖→重」の温度順：凪→芽→陽→温→恋→憂→沈。
# 旧9スウォッチ・v3.7以前の自由色は最近傍へ丸め、珍しい色から個人が浮かび上がるのを防ぐ（色の量子化）。
_MOOD_SWATCH_HEX = ["#C9D4D2", "#C4CDB4", "#EBD9AE", "#E8C4A8",
                    "#DFAFAE", "#C0B2C4", "#8C7F80"]
# 地図APIが返す気分の識別子（hexは外に出さず、この slug と紙側のCSSトークン --mood-* で対応させる）
_MOOD_SLUGS = ["nagi", "me", "hi", "on", "koi", "yuu", "shizumi"]


_HSL_RE = re.compile(
    r"^hsla?\(\s*(-?[\d.]+)(?:deg)?\s*[, ]\s*([\d.]+)%\s*[, ]\s*([\d.]+)%")


def _hex_to_rgb(h):
    """色文字列 → (r,g,b)。HEX(#RGB/#RRGGBB)と hsl()/hsla() の両方を受ける。
    v3.14でピッカーがHSL保存になったが、旧データ・デモ投入はHEXのまま来るため両対応。"""
    try:
        h = h.strip()
        m = _HSL_RE.match(h)
        if m:
            hue = (float(m.group(1)) % 360) / 360.0
            sat = min(100.0, max(0.0, float(m.group(2)))) / 100.0
            lig = min(100.0, max(0.0, float(m.group(3)))) / 100.0
            r, g, b = colorsys.hls_to_rgb(hue, lig, sat)
            return (round(r * 255), round(g * 255), round(b * 255))
        h = h.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, AttributeError, IndexError):
        return None


def _quantize_to_swatch(color):
    rgb = _hex_to_rgb(color)
    if rgb is None:
        return None
    best, best_d = None, None
    for sw in _MOOD_SWATCH_HEX:
        s = _hex_to_rgb(sw)
        d = sum((a - b) ** 2 for a, b in zip(rgb, s))
        if best_d is None or d < best_d:
            best, best_d = sw, d
    return best


# ── 気分の宙（mood space / v7・語のネットワーク）──────────────
# 2026-07-24 v7：点の散布ではなく、手紙のタグ（＝年表の感情タグ emos）そのものが星になる。
# 返すのは語・色相・季節・時間帯・天気・エリアのハッシュ・開封までの残り日数・封じてからの経過だけ。
# 本文・日付・件名・タイトル・生の地名は一切返さない。id はハッシュ化して差分同期にのみ使う。

_MOOD_SWATCH_INDEX = {h.lower(): i for i, h in enumerate(_MOOD_SWATCH_HEX)}


def _mood_index(color):
    """seal_color(HEX/HSL) → 気分7色の番号(0-6)。旧9スウォッチ・自由色は最近傍へ丸める。"""
    q = _quantize_to_swatch(color)
    return _MOOD_SWATCH_INDEX.get(q.lower()) if q else None


@app.route("/mood")
def mood_page():
    guard = _page_login_guard()
    if guard:
        return guard
    return render_template("mood.html")


# v7 の語の材料。1〜3語のタグ・8変数のメタデータを手紙単位で返す。
# 他ユーザー分も同じスキーマで混ぜる（クライアントは自分/他人を区別しない）。
# demo除外・オプトアウト尊重は地図(B)と同じ意思。他人の色相は7色量子化後の値だけを出し、
# 他人の日数は7日単位に丸める（細かい残日数・経過は個人のスケジュールを指しうるため）。
_OTHERS_CACHE_SECONDS = 60
_OTHERS_MAX = 120          # 宙に混ぜる他者の手紙の上限。超えた分は毎回ランダムに間引く
_others_lock = threading.Lock()
_others_cache = {"t": 0.0, "letters": []}   # letters: (user_id, letter_dict)

_MOOD_BAND = {"morning": "morning", "day": "noon", "evening": "evening", "night": "night"}


def _mood_season(sent_date):
    try:
        m = int(sent_date[5:7])
    except (TypeError, ValueError, IndexError):
        return "winter"
    if 3 <= m <= 5:
        return "spring"
    if 6 <= m <= 8:
        return "summer"
    if 9 <= m <= 11:
        return "autumn"
    return "winter"


def _mood_band(row):
    b = _MOOD_BAND.get(row["time_bucket"] if "time_bucket" in row.keys() else None)
    if b:
        return b
    try:
        h = int(row["sent_date"][11:13])
    except (TypeError, ValueError, IndexError):
        return "night"
    if 5 <= h < 11:
        return "morning"
    if 11 <= h < 16:
        return "noon"
    if 16 <= h < 19:
        return "evening"
    return "night"


def _mood_weather(row):
    """封入時の気象を4分類に丸める（fogはcloudへ）。記録がなければcloud。"""
    cond = None
    if row["seal_env"]:
        try:
            cond = (json.loads(row["seal_env"]) or {}).get("condition")
        except (ValueError, TypeError):
            cond = None
    cond = cond or row["weather_event"]
    if cond in ("clear", "cloud", "rain", "snow"):
        return cond
    return "cloud"


def _mood_hue(color):
    """色文字列(HSL/HEX) → 色相 0–359。色なし・解釈不能は None。"""
    if not color:
        return None
    m = _HSL_RE.match(color.strip())
    if m:
        return int(float(m.group(1)) % 360)
    rgb = _hex_to_rgb(color)
    if rgb is None:
        return None
    h, _l, _s = colorsys.rgb_to_hls(*(c / 255.0 for c in rgb))
    return int(h * 360) % 360


# ── 気分の宙：人の名前・あだ名を宙に出さないフィルタ（2026-07-24）──
# 本文抽出（C案）で名前が漏れるのを防ぐ。完全な人名判定は形態素解析なしには不可能なので
# best-effort：明示ブロックリスト＋あだ名接尾辞＋各ユーザーの登録名で落とす。
# これで捕まえられない未知の人名（例「健太」「マリア」）は regex では検出不能＝残る。
_MOOD_NAME_BLOCK = {
    "筒井", "筒井晃生", "つつい", "ツツイ", "tsutsui",
    "こう", "こうちゃん", "こうくん", "コウ", "つつこう", "ツツコウ",
    "テスト", "てすと", "test",
}
# この語尾で終わる語は人の呼び名とみなして落とす（あだ名接尾辞）
_MOOD_NICK_SUFFIX = ("ちゃん", "チャン", "くん", "クン", "君",
                     "さん", "サン", "たん", "タン", "っち", "ッチ")


def _mood_norm_word(s):
    """比較用に正規化：小文字化＋全角英数→半角。"""
    x = str(s or "").strip().lower()
    return "".join(
        chr(ord(c) - 0xFEE0) if ("ａ" <= c <= "ｚ" or "Ａ" <= c <= "Ｚ" or "０" <= c <= "９") else c
        for c in x)


def _mood_name_blocked(word, extra=None):
    """word が人の名前・あだ名なら True。extra はそのユーザーの登録名など（正規化済み集合）。"""
    w = _mood_norm_word(word)
    if not w:
        return True
    if w in _MOOD_NAME_BLOCK or (extra and w in extra):
        return True
    return any(word.endswith(s) for s in _MOOD_NICK_SUFFIX)


def _mood_words_from_poem(poem, extra_block=None):
    """本文から名詞相当の語を最大3つ抜く（章題抽出器 _CH_WORD_RE を流用）。
    人の名前・あだ名（_mood_name_blocked）は除く。
    ★重要：これは封の中（未開封）の本文からの抽出であり、本文秘匿の鉄則の例外。
    2026-07-24 ユーザーの明示的な選択（C案）で、本人自身の手紙にのみ許可。
    他ユーザーの手紙には絶対に適用しない（呼び出し側で own のみ渡す）。"""
    src = poem or ""
    seen, words = set(), []
    for m in _CH_WORD_RE.finditer(src):
        w = m.group(0).strip()
        if not w or w in _CH_WORD_STOP or w in seen:
            continue
        if _mood_name_blocked(w, extra_block):
            continue
        # 語の直後に敬称・あだ名接尾辞が続くなら人の呼び名とみなす（例「田中くん」→田中を落とす）
        tail = src[m.end():m.end() + 3]
        if any(tail.startswith(s) for s in _MOOD_NICK_SUFFIX):
            continue
        seen.add(w)
        words.append(w[:24])
        if len(words) >= 3:
            break
    return words


def _mood_letter(r, now, blur=False, allow_body=False, extra_block=None):
    """letters行 → v7の手紙dict。対象外（開封済み・到着済み・語なし）は None。
    blur=True は他人の手紙：色相を7色量子化後の値に、日数を7日単位に丸める。
    allow_body=True（本人のみ）は emos が無い時、本文から語を抽出する（C案・本文秘匿の例外）。
    人の名前・あだ名は emos タグ・抽出語の両方から除く（extra_block は本人の登録名など）。"""
    try:
        if _letter_opened(r) or _is_arrived(r):
            return None
    except (TypeError, ValueError):
        return None
    try:
        emos = json.loads(r["emos"] or "[]")
    except (ValueError, TypeError):
        emos = []
    tags = [str(e).strip()[:24] for e in emos
            if str(e).strip() and not _mood_name_blocked(str(e).strip(), extra_block)][:3]
    if not tags and allow_body and "poem" in r.keys():
        tags = _mood_words_from_poem(r["poem"], extra_block)
    if not tags:
        return None   # 語のない手紙は宙に出ない（空配列は返さない）
    arrive = ((r["demo_arrive_at"] if r["demo_mode"] else None)
              or r["arrive_at"] or (r["arrive_date"] + "T00:00:00"))
    try:
        until = max(0, (datetime.fromisoformat(arrive) - now).days)
        since = max(0, (now - datetime.fromisoformat(r["sent_date"][:19])).days)
    except (TypeError, ValueError):
        return None
    hue = _mood_hue(_quantize_to_swatch(r["seal_color"]) if blur else r["seal_color"])
    if blur:
        until = (until // 7) * 7
        since = (since // 7) * 7
    # エリアは平文を出さずハッシュだけ。クライアントは同一判定にしか使わない。
    # 位置なしの手紙は手紙ごとに別の値＝どこの語とも「同じ土地」にならない。
    area_src = r["area_name"] or ("letter:" + r["id"])
    return {
        "id": hashlib.sha256(("mood:" + r["id"]).encode()).hexdigest()[:12],
        "tags": tags,
        "hue": hue,
        "season": _mood_season(r["sent_date"]),
        "band": _mood_band(r),
        "weather": _mood_weather(r),
        "area": hashlib.sha256(area_src.encode()).hexdigest()[:6],
        "daysUntilOpen": until,
        "daysSinceSealed": since,
    }


# 気分の宙が letters から読んでよい列。SELECT * は使わない：
# 本文(poem)・生座標(area_lat/lng)・件名相当を絶対に載せないため、列を指で数えて書く。
_MOOD_COLS = ("id, user_id, emos, seal_color, sent_date, time_bucket, seal_env, "
              "area_name, opened, opened_at, arrive_at, arrive_date, "
              "demo_mode, demo_arrive_at, weather_event, weather_met_at")


def _mood_space_all_letters():
    """封の中の全ユーザーの (user_id, 手紙dict) を返す。呼び出し側で本人分を除く。
    全ユーザー横断クエリなのでグローバルキャッシュを挟む（60秒＝ポーリングと同じ周期）。"""
    now = time.time()
    with _others_lock:
        if now - _others_cache["t"] < _OTHERS_CACHE_SECONDS:
            return _others_cache["letters"]
    rows = get_db().execute(
        f"SELECT {_MOOD_COLS} FROM letters "
        "WHERE COALESCE(demo_mode,0)=0 "
        "AND COALESCE(excluded_from_aggregate,0)=0 "
        "AND COALESCE(emos,'[]')<>'[]'").fetchall()
    dt_now = datetime.now()
    letters = []
    for r in rows:
        d = _mood_letter(r, dt_now, blur=True)
        if d:
            letters.append((r["user_id"], d))
    with _others_lock:
        _others_cache["t"] = time.time()
        _others_cache["letters"] = letters
    return letters


@app.route("/api/mood-space")
@login_required
def api_mood_space():
    db = get_db()
    # 本人の登録名は「人の名前」として宙に出さない（本文抽出で自分の名前が漏れるのを防ぐ）。
    urow = db.execute("SELECT username FROM users WHERE id=?", (uid(),)).fetchone()
    extra_block = set()
    if urow and urow["username"]:
        for part in re.split(r"[\s　]+", urow["username"]):
            n = _mood_norm_word(part)
            if n:
                extra_block.add(n)
    # 本人の手紙だけ poem を読む（本文からの語抽出＝C案は本人限定）。emos の有無で絞らない。
    rows = db.execute(
        f"SELECT {_MOOD_COLS}, poem FROM letters WHERE user_id=?", (uid(),)).fetchall()
    now = datetime.now()
    letters = []
    for r in rows:
        d = _mood_letter(r, now, allow_body=True, extra_block=extra_block)
        if d:
            letters.append(d)
    others = [d for u, d in _mood_space_all_letters() if u != uid()]
    if len(others) > _OTHERS_MAX:
        others = random.sample(others, _OTHERS_MAX)
    letters += others
    random.shuffle(letters)   # 並び順から投函順・自分/他人の境目を消す
    return jsonify(letters=letters)


# ── 気分の地図（Mood Night Map / B）の集計テーブル ─────────────────
# Postgres なら MATERIALIZED VIEW + REFRESH だが、たよりは SQLite なので普通のテーブルを
# 日次で作り直す（maintenance_loop から呼ぶ）。個票・本文・ID・生座標には一切触れない。
MOOD_GRID_THRESHOLD = 10   # 匿名性のしきい値。下げないこと（母数が小さいと色が個人を指す）


def _refresh_mood_grid(db):
    """0.1度セル×気分ごとに10通以上まとまった分だけを mood_grid に残す。
    近傍量子化(_mood_index)が要るので集計は Python 側で行う。lat/lng はセル内平均
    （セル中心ではなく平均にすることで境界のグリッド感が緩む）。返り値は残ったセル数。"""
    agg = {}   # (grid_id, mood) -> [n, latest, lat_sum, lng_sum]
    rows = db.execute(
        "SELECT grid_id, seal_color, sent_date, area_lat, area_lng FROM letters "
        "WHERE grid_id IS NOT NULL AND COALESCE(demo_mode,0)=0 "
        "AND COALESCE(excluded_from_aggregate,0)=0 "
        "AND seal_color IS NOT NULL AND seal_color<>''").fetchall()
    for r in rows:
        if r["area_lat"] is None or r["area_lng"] is None:
            continue
        m = _mood_index(r["seal_color"])
        if m is None:
            continue
        a = agg.get((r["grid_id"], m))
        if a is None:
            agg[(r["grid_id"], m)] = [1, r["sent_date"], r["area_lat"], r["area_lng"]]
        else:
            a[0] += 1
            if r["sent_date"] and (a[1] is None or r["sent_date"] > a[1]):
                a[1] = r["sent_date"]
            a[2] += r["area_lat"]
            a[3] += r["area_lng"]
    with _WRITE_LOCK:
        db.execute("DELETE FROM mood_grid")
        kept = 0
        for (gid, m), (n, latest, lat_s, lng_s) in agg.items():
            if n < MOOD_GRID_THRESHOLD:      # しきい値未満はそもそも入れない
                continue
            db.execute(
                "INSERT INTO mood_grid (grid_id,mood,n,latest,lat,lng) VALUES (?,?,?,?,?,?)",
                (gid, m, n, latest, lat_s / n, lng_s / n))
            kept += 1
        db.commit()
    return kept


@app.route("/api/letters")
@login_required
def api_letters():
    rows = get_db().execute("SELECT * FROM letters WHERE user_id=? ORDER BY sent_date DESC, id DESC", (uid(),)).fetchall()
    received, in_transit = [], []
    for r in rows:
        if _is_arrived(r):
            # 【本文秘匿の鉄則】開封日が来ても、開封操作（opened_at）まで本文は配信しない。
            # openable の手紙はメタデータだけ返し、本文は開封APIのレスポンスで初めて届く。
            if _letter_opened(r):
                received.append(letter_to_dict(r))
            else:
                received.append(openable_meta(r))
        else:
            in_transit.append(sealed_meta(r))

    def _sort_key(d):
        new = not d.get("opened")
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
    # 80字は固定の仕様（クライアントの maxlength と対）。
    # 行頭の字下げや空行は意図した余白として保ち、末尾の余りだけ落とす。空判定のみtrimで行う。
    poem = (data.get("poem") or "")[:80].rstrip()
    if not poem.strip():
        poem = ""
    photo = data.get("photo")
    voice = data.get("voice")
    if not poem and not photo and not voice:
        return jsonify(error="写真かことば、声をひとつ。"), 400

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

    weather_event = data.get("weather_event")
    if not weather_event and dt <= datetime.now() - timedelta(minutes=1):
        return jsonify(error="届く日時は今より後にしてください。"), 400

    lid = secrets.token_hex(8)
    seal_env = json.dumps(data.get("seal_env")) if data.get("seal_env") else None
    stamp = (data.get("stamp") or "")[:16] or None
    # 封入する「その時」の記録：気分の色（カラー・ピッカー）と、便箋に透けていた問い
    seal_color = (data.get("seal_color") or "").strip()[:32] or None
    seal_q = (data.get("seal_q") or "").strip()[:80] or None

    # タイプ再生（TypeTrace）の打鍵スナップショット列。JSON文字列で保存。暴走サイズは捨てる。
    trace = data.get("trace")
    if trace is not None and not isinstance(trace, str):
        trace = json.dumps(trace, ensure_ascii=False)
    if trace and len(trace) > 600_000:
        trace = None

    # 封じた場所のエリア（逆ジオコーディング済みの名前と代表座標のみ。生座標は受けない前提）。
    # 名前・座標・時間帯が揃っていなければ、すべてNULLの「位置なし手紙」として扱う。
    area_name = (str(data.get("area_name") or "")).strip()[:80] or None
    time_bucket = data.get("time_bucket")
    if time_bucket not in ("morning", "day", "evening", "night"):
        time_bucket = None
    try:
        area_lat = round(float(data.get("area_lat")), 3)
        area_lng = round(float(data.get("area_lng")), 3)
    except (TypeError, ValueError):
        area_lat = area_lng = None
    if not (area_name and area_lat is not None and area_lng is not None
            and -90.0 <= area_lat <= 90.0 and -180.0 <= area_lng <= 180.0):
        area_name = area_lat = area_lng = time_bucket = None

    # 縦書きで書かれた手紙かどうか（書いた時の姿ごと封入する）
    vertical = 1 if data.get("vertical") else 0
    # 書体は明朝のみ（書体選択は撤去。letters.font 列は過去データ互換のため残置し、新規は書かない）

    sent_iso = datetime.now().isoformat(timespec="seconds")
    db = get_db()
    # 気分の地図（A・B）用: エリア座標を0.1度セルへ丸めた grid_id を、位置を保存するのと同じ
    # トランザクションで入れる。集計から抜けている人の手紙は、投函時点で excluded を立てておく
    # （後で一括UPDATEしなくても集計クエリは letters 側だけ見ればよい）。
    grid_id = _compute_grid_id(area_lat, area_lng)
    _optout = db.execute(
        "SELECT COALESCE(aggregate_opt_out,0) AS o FROM users WHERE id=?", (uid(),)).fetchone()
    excluded = 1 if (_optout and _optout["o"]) else 0
    with _WRITE_LOCK:
        db.execute(
            """INSERT INTO letters
               (id,user_id,poem,photo,voice,sent_date,arrive_date,arrive_at,arrive_label,arrive_hidden,opened,emos,from_reply,weather_event,seal_env,stamp,trace,seal_color,seal_q,area_name,area_lat,area_lng,time_bucket,vertical,grid_id,excluded_from_aggregate)
               VALUES (?,?,?,?,?,?,?,?,?,?,0,'[]',?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lid, uid(), poem, photo, voice, sent_iso, arrive_date, arrive_at,
             data.get("arrive_label", ""), 1 if data.get("arrive_hidden") else 0,
             1 if data.get("from_reply") else 0, weather_event, seal_env, stamp, trace,
             seal_color, seal_q, area_name, area_lat, area_lng, time_bucket, vertical,
             grid_id, excluded),
        )
        db.commit()
    # 開封のお知らせメールは認証済みアドレスにしか送られない（_check_and_notify の条件と対）。
    # 未設定／確認待ちのまま投函した時は、そのたびに知らせられるよう状態を返す。
    u = db.execute("SELECT email, COALESCE(email_verified,0) AS verified FROM users WHERE id=?", (uid(),)).fetchone()
    notify_reason = None
    if not (u and u["email"]):
        notify_reason = "none"
    elif not u["verified"]:
        notify_reason = "pending"
    return jsonify(id=lid, ok=True, notify_off=bool(notify_reason), notify_reason=notify_reason)


# ── デモ用：開封予定日時の上書き ─────────────────────────────────
# demo_mode=1 の手紙（seed_demo_data.py で投入）だけが対象。demo_arrive_at を
# 動かして「まだ開けられない／もう開けられる」を自由に再現する。本来の arrive_at
# には触れず、null を送れば上書き解除で元の予定に戻る。
@app.route("/api/letters/<lid>/demo-arrive", methods=["POST"])
@login_required
def api_demo_arrive(lid):
    row = own_letter(lid)
    if row is None:
        return jsonify(error="そのたよりは見つかりません。"), 404
    if not ("demo_mode" in row.keys() and row["demo_mode"]):
        return jsonify(error="デモ用のたよりではありません。"), 403
    data = request.get_json(force=True)
    raw = data.get("demo_arrive_at")
    if raw:
        try:
            val = datetime.fromisoformat(raw).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            return jsonify(error="日時が正しくありません。"), 400
    else:
        val = None
    db = get_db()
    with _WRITE_LOCK:
        db.execute("UPDATE letters SET demo_arrive_at=? WHERE id=?", (val, lid))
        db.commit()
    return jsonify(ok=True, demo_arrive_at=val)


# ──「問い直しの栞」（封じる直前にAIが本文を読んで問いを返す機能）は 2026-07-24 に完全削除。
# 「AIは手紙の中身を読まない」という設計思想と矛盾するため、温存コードごと撤去した。


# ══════════════════════════════════════════════════════════════════════
#  10問アンケート → 未来への手紙（HTMXの並行フロー）
#  /letter/new → /letter/<id>/answer（hx-swap-oobで手紙プレビューと次の質問を同時差し替え）→ /letter/<id>/seal
#  既存の投函SPA（letters テーブル）とは独立。survey_letters / questions / answers を使う。
# ══════════════════════════════════════════════════════════════════════

def _page_login_guard():
    """HTMLページ用のログインガード。未ログインならトップへ返す redirect を返し、
    ログイン済みなら None。API用の login_required（JSON 401）とは別に、ページは / へ誘導する。"""
    if not session.get("uid"):
        return redirect("/")
    return None


def _survey_letter(db, lid):
    """本人の、まだ封をしていない手紙を返す。無ければ None。"""
    return db.execute(
        "SELECT * FROM survey_letters WHERE id=? AND user_id=?", (lid, session.get("uid"))
    ).fetchone()


def _survey_questions(db):
    return db.execute(
        "SELECT id,ord,prompt,letter_fragment_template,is_required FROM questions ORDER BY ord"
    ).fetchall()


def _survey_answers_map(db, lid):
    """{question_id: value} を返す（skip は value='' で記録済み＝再出題しないため）。"""
    rows = db.execute(
        "SELECT question_id, value FROM answers WHERE letter_id=?", (lid,)
    ).fetchall()
    return {r["question_id"]: (r["value"] or "") for r in rows}


def _assemble_fragments(db, lid):
    """回答済み（空でない）設問を ord 順に手紙の一文へ変換して返す。回答→一文の変換はここ（DBのテンプレート）で行う。"""
    rows = db.execute(
        """SELECT q.letter_fragment_template AS tpl, a.value AS val
           FROM answers a JOIN questions q ON q.id = a.question_id
           WHERE a.letter_id = ? AND a.value IS NOT NULL AND TRIM(a.value) <> ''
           ORDER BY q.ord""",
        (lid,),
    ).fetchall()
    return [r["tpl"].replace("{answer}", r["val"].strip()) for r in rows]


def _next_question(questions, answered_ids):
    """まだ触れていない（回答も skip もしていない）最初の設問。全て触れ終えたら None。"""
    for q in questions:
        if q["id"] not in answered_ids:
            return q
    return None


def _render_letter_parts(db, lid, oob=False):
    """#question-area（次の設問 or 封をする案内）と #letter-preview を描画。
    oob=True のとき preview に hx-swap-oob を付け、POST 応答で両方を同時差し替えする。"""
    questions = _survey_questions(db)
    amap = _survey_answers_map(db, lid)
    nxt = _next_question(questions, set(amap.keys()))
    fragments = _assemble_fragments(db, lid)
    answered_count = sum(1 for v in amap.values() if v.strip())
    question_html = render_template(
        "_letter_question.html", lid=lid, q=nxt,
        total=len(questions), answered=answered_count,
    )
    preview_html = render_template(
        "_letter_preview.html", fragments=fragments, oob=oob,
    )
    return question_html, preview_html


@app.route("/letter/new")
def letter_new():
    guard = _page_login_guard()
    if guard:
        return guard
    db = get_db()
    lid = secrets.token_hex(8)
    with _WRITE_LOCK:
        db.execute(
            "INSERT INTO survey_letters (id,user_id,created,sealed) VALUES (?,?,?,0)",
            (lid, uid(), datetime.now().isoformat(timespec="seconds")),
        )
        db.commit()
    return redirect(f"/letter/{lid}/answer")


@app.route("/letter/<lid>/answer", methods=["GET", "POST"])
def letter_answer(lid):
    guard = _page_login_guard()
    if guard:
        return guard
    db = get_db()
    row = _survey_letter(db, lid)
    if row is None:
        return redirect("/")
    if row["sealed"]:
        return redirect(f"/letter/{lid}/seal")

    if request.method == "POST":
        try:
            qid = int(request.form.get("question_id", ""))
        except (TypeError, ValueError):
            qid = None
        # skip も「触れた」として記録する（value=''）。回答は最大400文字。
        value = "" if request.form.get("skip") else (request.form.get("value") or "").strip()[:400]
        if qid is not None:
            with _WRITE_LOCK:
                db.execute(
                    """INSERT INTO answers (letter_id,question_id,value,created)
                       VALUES (?,?,?,?)
                       ON CONFLICT(letter_id,question_id)
                       DO UPDATE SET value=excluded.value, created=excluded.created""",
                    (lid, qid, value, datetime.now().isoformat(timespec="seconds")),
                )
                db.commit()
        question_html, preview_html = _render_letter_parts(db, lid, oob=True)
        # 手紙プレビュー（oob）と次の設問を同時に返す
        return Response(preview_html + "\n" + question_html, mimetype="text/html")

    # GET：フル画面
    question_html, preview_html = _render_letter_parts(db, lid, oob=False)
    return render_template(
        "letter.html", lid=lid,
        question_area=question_html, preview=preview_html,
    )


@app.route("/letter/<lid>/seal", methods=["GET", "POST"])
def letter_seal(lid):
    guard = _page_login_guard()
    if guard:
        return guard
    db = get_db()
    row = _survey_letter(db, lid)
    if row is None:
        return redirect("/")

    show_bookmark = False
    if not row["sealed"]:
        with _WRITE_LOCK:
            db.execute(
                "UPDATE survey_letters SET sealed=1, sealed_at=? WHERE id=? AND user_id=?",
                (datetime.now().isoformat(timespec="seconds"), lid, uid()),
            )
            # リテンション：初めて封をした人にだけ「ブックマークに」を出す。
            # クリック判定ではなく“表示した瞬間”にフラグを立てる（＝この封で見せたら二度と出さない）。
            u = db.execute(
                "SELECT COALESCE(bookmark_prompt_shown,0) AS shown FROM users WHERE id=?", (uid(),)
            ).fetchone()
            if u and not u["shown"]:
                show_bookmark = True
                db.execute("UPDATE users SET bookmark_prompt_shown=1 WHERE id=?", (uid(),))
            db.commit()

    fragments = _assemble_fragments(db, lid)
    return render_template(
        "seal.html", lid=lid, fragments=fragments, show_bookmark=show_bookmark,
    )


@app.route("/api/letters/<lid>/trace", methods=["GET"])
@login_required
def api_get_trace(lid):
    """タイプ再生用：その便りの打鍵スナップショット列を返す（到着後のみ）。"""
    row = own_letter(lid)
    if row is None:
        return jsonify(error="便りが見つかりません。"), 404
    # 打鍵スナップショットは本文そのもの。到着だけでなく「開封済み」まで出さない（チラ見せ禁止）。
    if not _is_arrived(row) or not _letter_opened(row):
        return jsonify(error="まだ封の中です。"), 403
    raw = row["trace"] if "trace" in row.keys() else None
    try:
        steps = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        steps = None
    return jsonify(trace=steps)


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
    open_mood = (data.get("open_mood") or "").strip()[:40] or None

    # 開封した場所のエリア（封緘時と同じ流儀：逆ジオコーディング済みの名前と丸め座標のみ）。
    # 揃っていなければすべてNULL＝場所なし開封として正常に続行する。
    o_name = (str(data.get("open_area_name") or "")).strip()[:80] or None
    try:
        o_lat = round(float(data.get("open_area_lat")), 3)
        o_lng = round(float(data.get("open_area_lng")), 3)
    except (TypeError, ValueError):
        o_lat = o_lng = None
    if not (o_name and o_lat is not None and o_lng is not None
            and -90.0 <= o_lat <= 90.0 and -180.0 <= o_lng <= 180.0):
        o_name = o_lat = o_lng = None

    with _WRITE_LOCK:
        already = row["opened_at"] if "opened_at" in row.keys() else None
        if not already:
            now_iso = datetime.now().isoformat(timespec="seconds")
            # 「開けた日の場所」は最初の開封の時だけ記録する（opened_at と同じく、後から動かさない）
            get_db().execute(
                "UPDATE letters SET opened=1, open_env=?, open_mood=?, opened_at=?, "
                "open_area_name=?, open_area_lat=?, open_area_lng=?, "
                "reflect_count=COALESCE(reflect_count,0)+1 WHERE id=? AND user_id=?",
                (open_env, open_mood, now_iso, o_name, o_lat, o_lng, lid, uid()))
        else:
            if open_mood:
                get_db().execute("UPDATE letters SET opened=1, open_env=?, open_mood=? WHERE id=? AND user_id=?",
                                 (open_env, open_mood, lid, uid()))
            else:
                get_db().execute("UPDATE letters SET opened=1, open_env=? WHERE id=? AND user_id=?",
                                 (open_env, lid, uid()))
        get_db().commit()

    keys = row.keys()
    # 開封トランザクションのレスポンスで、本文（poem 等）を初めて配信する。
    # first_open は初回開封かどうかの一回きりフラグ（opened_at はサーバ側で確定済み。
    # 二度目以降の呼び出しは冪等に同じ手紙を返すだけ）。フロントは現状これを使っていないが、
    # 将来の「初回だけの演出」向けに残置。
    fresh = own_letter(lid)
    return jsonify(ok=True, first_open=not bool(already),
                   letter=letter_to_dict(fresh) if fresh else None,
                   seal_env=row["seal_env"], open_env=open_env, open_mood=open_mood,
                   seal_color=(row["seal_color"] if "seal_color" in keys else None),
                   seal_q=(row["seal_q"] if "seal_q" in keys else None),
                   sent_date=row["sent_date"])


@app.route("/api/letters/<lid>/color", methods=["POST"])
@login_required
def api_set_open_color(lid):
    """開封時に選び直した「今の気分の色」を記録する（封をした日の色との差分になる）。"""
    row = own_letter(lid)
    if not row:
        return jsonify(error="便りが見つかりません。"), 404
    if not _is_arrived(row):
        return jsonify(error="まだ封の中です。"), 403
    color = (request.get_json(force=True).get("color") or "").strip()[:32] or None
    with _WRITE_LOCK:
        get_db().execute("UPDATE letters SET open_color=? WHERE id=? AND user_id=?",
                         (color, lid, uid()))
        get_db().commit()
    return jsonify(ok=True, open_color=color)


@app.route("/api/letters/<lid>/mood", methods=["POST"])
@login_required
def api_set_open_mood(lid):
    row = own_letter(lid)
    if not row:
        return jsonify(error="便りが見つかりません。"), 404
    if not _is_arrived(row):
        return jsonify(error="まだ封の中です。"), 403
    mood = (request.get_json(force=True).get("mood") or "").strip()[:40] or None
    
    with _WRITE_LOCK:
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
    with _WRITE_LOCK:
        get_db().execute("UPDATE letters SET emos=? WHERE id=? AND user_id=?", (json.dumps(emos, ensure_ascii=False), lid, uid()))
        get_db().commit()
    return jsonify(ok=True)

@app.route("/api/letters/<lid>/reply", methods=["POST"])
@login_required
def api_reply(lid):
    row = own_letter(lid)
    if not row: return jsonify(error="便りが見つかりません。"), 404
    if not _is_arrived(row): return jsonify(error="まだ封の中です。"), 403

    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    if not text: return jsonify(error="空の返事です。"), 400

    # コメントの「その時」を継承する：時間帯（端末ローカルで確定済み）と気象スナップショット
    time_bucket = data.get("time_bucket")
    if time_bucket not in ("morning", "day", "evening", "night"):
        time_bucket = None
    env = json.dumps(data.get("env")) if data.get("env") else None

    now_iso = datetime.now().isoformat(timespec="seconds")

    with _WRITE_LOCK:
        get_db().execute(
            "INSERT INTO thread (letter_id,who,text,created,created_at,kind,time_bucket,env) VALUES (?,?,?,?,?,?,?,?)",
            (lid, "now", text, date.today().isoformat(), now_iso, "reply", time_bucket, env))
        get_db().execute("UPDATE letters SET reflect_count = COALESCE(reflect_count,0)+1 WHERE id=? AND user_id=?", (lid, uid()))
        get_db().commit()
    return jsonify(ok=True)

# ── 一筆箋：超軽量な日々の記録レイヤー ──
# 入力は「気分の色1タップ＋一行（任意）」だけ。気象スナップショットを自動で封入する。
# 通知・リマインド・ストリーク・空白日の可視化・日常的な分析は一切しない。
# 蓄積された点群が参照されるのは、便りの開封時（色の点群）とAI対話の文脈だけ。
NOTE_TEXT_MAX = 60


@app.route("/api/notes", methods=["POST"])
@login_required
def api_create_note():
    data = request.get_json(force=True)
    color = (data.get("color") or "").strip()[:32] or None
    text = (data.get("text") or "").strip()[:NOTE_TEXT_MAX]
    if not color and not text:
        return jsonify(error="色かことばを、ひとつ。"), 400
    env = json.dumps(data.get("env")) if data.get("env") else None
    nid = secrets.token_hex(8)
    db = get_db()
    try:
        with _WRITE_LOCK:
            db.execute(
                "INSERT INTO notes (id,user_id,color,text,env,created) VALUES (?,?,?,?,?,?)",
                (nid, uid(), color, text or None, env,
                 datetime.now().isoformat(timespec="seconds")))
            db.commit()
    except sqlite3.OperationalError as e:
        print(f"[たより] 一筆箋 書き込み失敗（再試行可）: {e}", flush=True)
        return jsonify(error="いま少し混み合っています。数秒おいて、もう一度お試しください。"), 503
    return jsonify(ok=True, id=nid)


@app.route("/api/notes")
@login_required
def api_list_notes():
    rows = get_db().execute(
        "SELECT id,color,text,env,created FROM notes WHERE user_id=? ORDER BY created DESC LIMIT 500",
        (uid(),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["env"] = json.loads(d["env"]) if d["env"] else None
        except (TypeError, ValueError):
            d["env"] = None
        out.append(d)
    return jsonify(notes=out)


# ── 捨てられない屑籠 ──────────────────────────────────────────
# 握りつぶして投げ捨てた手紙の行き先。「破壊したはずのものが全部残っている」が思想なので、
# DELETE も UPDATE も存在しない（消せないことが仕様。エンドポイントを後から足さないこと）。
# カメラ映像・手のランドマーク座標はクライアントに閉じ、ここには本文と散乱座標だけが届く。

# ── ほどけるまで（2026-07-22・二段階の溶解）─────────────────────
# 捨てた言葉は7日のあいだ「揺らいでいる」＝読める・筆跡も再生できる・ひろげて書きつづけられる。
# 7日を過ぎるか「もう、戻らない」を選んだ紙玉は、色片(woven_scraps)へ溶けて消える。
# 本文・筆跡はその時に物理的に消える（不可逆）。
# 改定された恒久ルール: ユーザー任意の削除APIは今後も作らない。出口はこの溶解だけ。

UNRAVEL_AFTER = timedelta(days=7)


def _dissolve_scraps(db, user_id=None, tid=None):
    """ほどける日時を過ぎた紙玉（tid指定時はその一枚を今すぐ）を色片へ還す。冪等。
    バッチ・読み取り時の遅延溶解・「もう、戻らない」の三経路すべてがここを通る。"""
    if tid:
        q = "SELECT id,user_id,mood_color,created_at FROM unemptyable_trash WHERE id=? AND user_id=?"
        args = (tid, user_id)
    else:
        q = ("SELECT id,user_id,mood_color,created_at FROM unemptyable_trash "
             "WHERE unravel_at IS NOT NULL AND unravel_at<=?")
        args = (datetime.now().isoformat(timespec="seconds"),)
        if user_id:
            q += " AND user_id=?"
            args = args + (user_id,)
    with _WRITE_LOCK:
        rows = db.execute(q, args).fetchall()
        for r in rows:
            db.execute(
                "INSERT INTO woven_scraps (id,user_id,mood_color,woven_month) VALUES (?,?,?,?)",
                (secrets.token_hex(8), r["user_id"], r["mood_color"],
                 (r["created_at"] or "")[:7] or "0000-00"))
            db.execute("DELETE FROM unemptyable_trash WHERE id=?", (r["id"],))
        if rows:
            db.commit()
    return len(rows)


@app.route("/api/trash/<tid>/dissolve", methods=["POST"])
@login_required
def api_trash_dissolve(tid):
    # 「もう、戻らない」：7日を待たず、いま色片へ還す。確認ダイアログはクライアント必須。
    n = _dissolve_scraps(get_db(), user_id=uid(), tid=tid)
    if not n:
        return jsonify(error="見つかりませんでした。"), 404
    return jsonify(ok=True)


@app.route("/api/trash", methods=["POST"])
@login_required
def api_trash_save():
    data = request.get_json(force=True)
    # 便箋と同じ80字制約。行頭の字下げ・空行は書かれたまま保ち、末尾の余りだけ落とす。
    content = (data.get("content") or "")[:80].rstrip()
    if not content.strip():
        return jsonify(error="白紙は握りつぶせません。"), 400
    mood = (data.get("mood_color") or "").strip()[:32] or None
    vertical = 1 if data.get("vertical") else 0
    # 筆跡（TypeTrace）。letters と同じ流儀：JSON文字列で保存し、暴走サイズは捨てる。
    trace = data.get("trace")
    if trace is not None and not isinstance(trace, str):
        trace = json.dumps(trace, ensure_ascii=False)
    if trace and len(trace) > 600_000:
        trace = None
    # 散乱座標はクライアント提案を受けるが、範囲外・欠損はサーバ側で振り直す（0〜100の%座標）
    try:
        rx = float(data.get("random_x"))
        ry = float(data.get("random_y"))
        if not (0.0 <= rx <= 100.0 and 0.0 <= ry <= 100.0):
            raise ValueError
    except (TypeError, ValueError):
        rx = random.uniform(8, 92)
        ry = random.uniform(10, 90)
    tid = secrets.token_hex(8)
    db = get_db()
    now = datetime.now()
    try:
        with _WRITE_LOCK:
            db.execute(
                """INSERT INTO unemptyable_trash
                   (id,user_id,content,mood_color,vertical,random_x,random_y,created_at,trace,unravel_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (tid, uid(), content, mood, vertical, rx, ry,
                 now.isoformat(timespec="seconds"), trace,
                 (now + UNRAVEL_AFTER).isoformat(timespec="seconds")))
            db.commit()
    except sqlite3.OperationalError as e:
        print(f"[たより] 屑籠 書き込み失敗（再試行可）: {e}", flush=True)
        return jsonify(error="いま少し混み合っています。数秒おいて、もう一度お試しください。"), 503
    return jsonify(ok=True, id=tid), 201


@app.route("/api/trash")
@login_required
def api_trash_list():
    # 古いものから返す（底に古い紙玉が沈んでいる順）。件数は画面に出さない方針だが上限だけ守る。
    # trace 本体は重いので一覧には載せず、有無のフラグだけ返す（本体は /api/trash/<tid>/trace）。
    # unravel_at はクライアントが「ほどけ具合」を描くための材料（数字のカウントダウンには使わない）。
    db = get_db()
    # 遅延溶解の保険: 常駐ループが止まっていても、見た瞬間には必ず正しい状態にする
    _dissolve_scraps(db, user_id=uid())
    rows = db.execute(
        """SELECT id,content,mood_color,vertical,random_x,random_y,created_at,unravel_at,
                  CASE WHEN trace IS NULL THEN 0 ELSE 1 END AS has_trace
           FROM unemptyable_trash WHERE user_id=? ORDER BY created_at ASC LIMIT 500""",
        (uid(),)).fetchall()
    return jsonify(items=[dict(r) for r in rows])


@app.route("/api/trash/<tid>/trace")
@login_required
def api_trash_trace(tid):
    row = get_db().execute(
        "SELECT trace FROM unemptyable_trash WHERE id=? AND user_id=?",
        (tid, uid())).fetchone()
    if not row:
        return jsonify(error="not found"), 404
    try:
        steps = json.loads(row["trace"]) if row["trace"] else None
    except (TypeError, ValueError):
        steps = None
    return jsonify(trace=steps)


@app.route("/api/letters/bulk-discard", methods=["POST"])
@login_required
def api_letters_bulk_discard():
    # 「一気に捨てる」：封の中（まだ届いていない）のたよりだけを、まとめて屑籠へ移す。
    # 恒久ルール「消せない屑籠」のとおり、行き先は unemptyable_trash。紙玉になった言葉は
    # 屑籠で読めるが、もう封には戻せない。届いてしまったたよりは歴史の一部なので対象外。
    data = request.get_json(force=True)
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids:
        return jsonify(error="捨てるたよりが選ばれていません。"), 400
    ids = [str(i)[:64] for i in ids][:100]
    db = get_db()
    moved = 0
    try:
        with _WRITE_LOCK:
            for lid in ids:
                row = db.execute("SELECT * FROM letters WHERE id=? AND user_id=?",
                                 (lid, uid())).fetchone()
                if not row or _is_arrived(row):
                    continue
                keys = row.keys()
                _now = datetime.now()
                db.execute(
                    """INSERT INTO unemptyable_trash
                       (id,user_id,content,mood_color,vertical,random_x,random_y,created_at,trace,unravel_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (secrets.token_hex(8), uid(),
                     (row["poem"] or "")[:80].rstrip(),
                     row["seal_color"] if "seal_color" in keys else None,
                     row["vertical"] if ("vertical" in keys and row["vertical"]) else 0,
                     random.uniform(8, 92), random.uniform(10, 90),
                     _now.isoformat(timespec="seconds"),
                     row["trace"] if "trace" in keys else None,
                     (_now + UNRAVEL_AFTER).isoformat(timespec="seconds")))
                db.execute("DELETE FROM thread WHERE letter_id=?", (lid,))
                db.execute("DELETE FROM letters WHERE id=? AND user_id=?", (lid, uid()))
                moved += 1
            db.commit()
    except sqlite3.OperationalError as e:
        print(f"[たより] 一気に捨てる 書き込み失敗（再試行可）: {e}", flush=True)
        return jsonify(error="いま少し混み合っています。数秒おいて、もう一度お試しください。"), 503
    return jsonify(ok=True, moved=moved)


_WX_JP = {"snow": "雪", "rain": "雨", "fog": "霧", "cloud": "曇り", "clear": "晴れ"}


def _env_phrase(env):
    if not env or not isinstance(env, dict):
        return ""
    cond = _WX_JP.get(env.get("condition"), "")
    temp = env.get("temp")
    if cond and temp is not None:
        return f"{cond}で{round(temp)}℃"
    return cond or (f"{round(temp)}℃" if temp is not None else "")


def _weather_context_text(seal_env, open_env):
    s = _env_phrase(seal_env)
    o = _env_phrase(open_env)
    if s and o:
        return f"封をしたあの日は「{s}」。それを開けている今日は「{o}」。"
    if s:
        return f"封をしたあの日は「{s}」だった。"
    if o:
        return f"これを開けている今日は「{o}」。"
    return ""


def _notes_context_text(user_id, since_iso=None, limit=30):
    """一筆箋の点群をAI対話の文脈に変換する。「日付・天気・色・一行」の時系列の点。
    点が多いほど、対話は浅い相槌から『本人も気づいていない変化の指摘』に近づく。"""
    q = "SELECT color,text,env,created FROM notes WHERE user_id=?"
    args = [user_id]
    if since_iso:
        q += " AND created>=?"
        args.append(since_iso)
    q += " ORDER BY created DESC LIMIT ?"
    args.append(limit)
    rows = get_db().execute(q, args).fetchall()
    lines = []
    for r in reversed(rows):
        try:
            env = json.loads(r["env"]) if r["env"] else None
        except (TypeError, ValueError):
            env = None
        bits = [(r["created"] or "")[:10]]
        wx = _env_phrase(env)
        if wx:
            bits.append(wx)
        if r["color"]:
            bits.append(f"気分の色{r['color']}")
        line = "・" + "、".join(b for b in bits if b)
        if r["text"]:
            line += f"「{r['text']}」"
        lines.append(line)
    return "\n".join(lines)


def _profile_context_text(user_id, limit=3):
    row = get_db().execute("SELECT onboarding FROM users WHERE id=?", (user_id,)).fetchone()
    answers = _load_onboarding(row["onboarding"] if row else None)
    if not answers:
        return ""
    gm = _gen_map(user_id)  # AI生成問いへの答えも輪郭の材料に含める
    qids = [q for q in answers if _question_text(q, gm)]
    random.shuffle(qids)
    lines = [f"・{_question_text(q, gm)} → {answers[q]}" for q in qids[:limit]]
    return "\n".join(lines)


def _gemini_question(prompt, api_key):
    import urllib.request
    import urllib.error
    if ("…" in api_key or "..." in api_key or "（" in api_key
            or "ここ" in api_key or "鍵" in api_key):
        raise ValueError(".env の GEMINI_API_KEY が例文（プレースホルダ）のままです。")
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("GEMINI_API_KEY に非ASCII文字が含まれています。")

    preferred = os.environ.get("TAYORI_GEMINI_MODEL")
    fallbacks = ["gemini-2.5-flash-lite", "gemini-flash-lite-latest",
                 "gemini-2.0-flash-lite", "gemini-2.5-flash"]
    models = ([preferred] if preferred else []) + [m for m in fallbacks if m != preferred]

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
        for attempt in range(2):
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
                break
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (400, 401, 403):
                    raise
                if e.code in (429, 503) and attempt == 0:
                    time.sleep(2)
                    continue
                break
    if last_err:
        raise last_err
    return None


def _claude_question(prompt, api_key):
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    model = os.environ.get("TAYORI_AI_MODEL", "claude-opus-4-8")
    msg = client.messages.create(model=model, max_tokens=1000,
                                 messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if b.type == "text").strip() or None


def _gemini_multimodal(parts, api_key, temperature=0.75, max_tokens=1600, thinking_budget=None):
    """parts は Gemini の contents.parts 形式（{"text":...} / {"inline_data":{...}}）。
    写真・音声を含む人物分析に使う。モデルfallbackは _gemini_question と同様。
    マルチモーダルに強い flash を優先。媒体が原因の 400 は呼び出し側で素材を減らして再試行する。
    thinking_budget=0 で思考トークンを止める（gemini-2.5系は既定で思考が maxOutputTokens を食い潰し、
    出力が途中で切れるため、まとまった本文が要る用途では 0 を渡す）。"""
    import urllib.request
    import urllib.error
    if ("…" in api_key or "..." in api_key or "（" in api_key
            or "ここ" in api_key or "鍵" in api_key):
        raise ValueError(".env の GEMINI_API_KEY が例文（プレースホルダ）のままです。")
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("GEMINI_API_KEY に非ASCII文字が含まれています。")
    preferred = os.environ.get("TAYORI_GEMINI_MODEL")
    fallbacks = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
    models = ([preferred] if preferred else []) + [m for m in fallbacks if m != preferred]
    gen_cfg = {"temperature": temperature, "topP": 0.9, "maxOutputTokens": max_tokens}
    if thinking_budget is not None:
        gen_cfg["thinkingConfig"] = {"thinkingBudget": thinking_budget}
    body = json.dumps({
        "contents": [{"parts": parts}],
        "generationConfig": gen_cfg,
    }).encode("utf-8")
    last_err = None
    for model in models:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent")
        for attempt in range(2):
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json", "X-goog-api-key": api_key})
            try:
                with urllib.request.urlopen(req, timeout=45) as resp:
                    data = json.loads(resp.read().decode())
                cands = data.get("candidates") or []
                ps = (cands[0].get("content") or {}).get("parts") or [] if cands else []
                text = "".join(p.get("text", "") for p in ps).strip()
                if text:
                    return text
                break
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code in (400, 401, 403):
                    raise           # 400=媒体不正の可能性。呼び出し側で素材を減らす。
                if e.code in (429, 503) and attempt == 0:
                    time.sleep(2)
                    continue
                break
    if last_err:
        raise last_err
    return None


def _split_data_url(durl):
    """'data:image/jpeg;base64,XXXX' → ('image/jpeg', 'XXXX')。違う形式なら None。"""
    if not durl or not isinstance(durl, str) or not durl.startswith("data:"):
        return None
    try:
        head, b64 = durl.split(",", 1)
    except ValueError:
        return None
    mime = head[5:].split(";")[0].strip() or "application/octet-stream"
    if not b64:
        return None
    return mime, b64


PORTRAIT_PROMPT = (
    "あなたは、ある人を長く見守ってきた、洞察の深い分析者です。"
    "この人が遺した言葉・写真・声、そして『初めの問い』への答えを手がかりに、"
    "「あなたという人」を客観的な自己分析として一篇に描いてください。\n\n"
    "― 手順（必ずこの順で）―\n"
    "1. まず頭の中で素材をすべて読み、価値観／ものの見方の癖／心が動く対象／人との距離の取り方／"
    "抱えやすい悩みや揺れ、を分析する。複数の素材の“あいだ”にある共通点・矛盾・繰り返し現れる主題を束ねる。\n"
    "2. その分析結果“だけ”を本文に書く。素材そのものは本文に持ち込まない。\n\n"
    "― 大切なこと ―\n"
    "・素材は“答え合わせ”ではなく“手がかり”です。質問と答えをなぞったり、引用・列挙したり、"
    "一問ずつ感想を述べたりは絶対にしないこと。\n"
    "・素材にある具体的な出来事・固有名詞・エピソード（学校名、地名、その日にあったこと等）を"
    "そのまま書き写さない。出来事は必ず「そこから読み取れる傾向」に変換してから書く。"
    "読んだ本人が「日記をなぞられた」ではなく「見抜かれた」と感じる抽象度で。\n"
    "・表面の出来事ではなく、その奥にある傾向・パターンに静かに触れる。\n"
    "・占いや性格類型の決めつけ、励まし・助言・説教はしない。診断もしない。\n"
    "・写真や声があれば、その空気感（色・光・声の温度など）も人物像の手がかりにしてよい。"
    "ただし写っているものを説明・列挙はしない。\n"
    "・二人称（「あなたは…」）で、本人へそっと差し出す手紙のように。ただし語りは冷静で、観察に根ざす。\n"
    "・自然で読みやすい日本語で書く。凝りすぎた比喩や難解な言い回しは避け、静かで、温かく、誠実に。\n"
    "・2〜3段落、全体で500字以内（必ず500字を超えない）。段落の間は空行（改行を2つ）で区切る。\n"
    "・見出し・箇条書き・前置き・メタな注釈はつけず、人物素描の本文だけを書く。\n\n"
    "手がかりとなる素材は次のとおりです（これは分析の材料であって、本文に書き写す対象ではありません）。"
)


def _trim_portrait(text, limit=400):
    """肖像が上限字数を超えたら、文末（。！？）の切れ目でそっと整える。途中で不自然に切れないように。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind("。"), head.rfind("！"), head.rfind("？"))
    # 文の切れ目が上限の6割より手前なら、無理に切らず上限で止める
    if cut >= int(limit * 0.6):
        return head[:cut + 1].strip()
    return head.strip()


# 内部用の人物プロファイル。ユーザーには見せず、「過去の自分からの問い」の“奥行き”を作るために使う。
PERSONA_PROMPT = (
    "あなたは、ある人物の心の輪郭を読み解く、静かで洞察の深い分析者です。"
    "この人が遺した言葉・写真・声、そして『初めの問い』への答えを手がかりに、"
    "「この人はどのような価値観と背景を持った人物なのか」を分析した“人物プロファイル”を作成してください。\n\n"
    "― 目的 ―\n"
    "このプロファイルは本人には見せません。のちに『過去の自分』が本人へ問いを投げかけるとき、"
    "その問いが本人の芯に触れるための、内なる理解として使われます。だから体裁より“核心”を優先してください。\n\n"
    "― 分析の視点（できる範囲で、決めつけずに）―\n"
    "・核となる価値観／ゆずれないもの\n"
    "・世界の見方、ものの考え方の癖（何にこだわり、何を軽んじるか）\n"
    "・心が動く対象、琴線に触れるもの\n"
    "・人との距離の取り方、関係の結び方\n"
    "・繰り返し現れる主題・行動のパターン\n"
    "・抱えやすい葛藤・迷い・恐れ\n"
    "・言葉づかい、語り口の特徴\n"
    "・いまの関心と、その人の現在地\n\n"
    "― 書き方 ―\n"
    "・個々の事実を並べるのではなく、複数の素材の“あいだ”に共通して流れるものを束ねる。\n"
    "・占いや性格類型の決めつけ、診断名、断定は避け、「〜の傾向がうかがえる」のように含みを持たせる。\n"
    "・素材が薄い項目は無理に埋めず、確かに読み取れることだけを書く。\n"
    "・上記の視点を見出し（・）で整理してよい。全体で400〜700字。\n"
    "・これは分析メモであり、本人への手紙ではない。二人称の語りかけにはしない。\n\n"
    "手がかりとなる素材は次のとおりです。"
)


def _gather_portrait_inputs(user_id, max_photos=6, max_voices=3, max_poems=40):
    """肖像分析の素材を集める。戻り値: (テキスト素材, 画像parts, 音声parts, 件数dict)。"""
    db = get_db()
    urow = db.execute("SELECT onboarding FROM users WHERE id=?", (user_id,)).fetchone()
    answers = _load_onboarding(urow["onboarding"] if urow else None)
    gm = _gen_map(user_id)  # 初めの問い＋今夜の問い（AI生成ぶんも含む）を肖像の材料に
    ob_lines = []
    for q in sorted(a for a in (answers or {}) if _question_text(a, gm)):
        ans = (answers[q] or "").strip()
        if ans:
            ob_lines.append(f"・{_question_text(q, gm)} → {ans}")

    # 封の中（未開封含む）の便りは材料にしない：AIの文章から封印中の言葉が漏れるのを防ぐ
    rows = db.execute(
        "SELECT poem, photo, voice, sent_date FROM letters WHERE user_id=? AND opened=1 ORDER BY sent_date DESC, id DESC",
        (user_id,)).fetchall()
    poems, image_parts, audio_parts = [], [], []
    for r in rows:
        p = (r["poem"] or "").strip()
        if p and len(poems) < max_poems:
            poems.append(f"（{r['sent_date']}）{p}")
        if len(image_parts) < max_photos:
            d = _split_data_url(r["photo"])
            if d and d[0].startswith("image/"):
                image_parts.append({"inline_data": {"mime_type": d[0], "data": d[1]}})
        if len(audio_parts) < max_voices:
            d = _split_data_url(r["voice"])
            if d and d[0].startswith("audio/"):
                audio_parts.append({"inline_data": {"mime_type": d[0], "data": d[1]}})

    # 一筆箋（日々のひとこと）も人物の手がかりに含める
    nrows = db.execute(
        "SELECT text, created FROM notes WHERE user_id=? AND text IS NOT NULL AND text<>'' "
        "ORDER BY created DESC LIMIT 30", (user_id,)).fetchall()
    note_lines = [f"（{(r['created'] or '')[:10]}）{r['text']}" for r in reversed(nrows)]

    blocks = []
    if ob_lines:
        blocks.append("【初めの問いへの答え】\n" + "\n".join(ob_lines))
    if poems:
        blocks.append("【遺した言葉（便り）】\n" + "\n".join(poems))
    if note_lines:
        blocks.append("【一筆箋（日々のひとこと）】\n" + "\n".join(note_lines))
    if image_parts or audio_parts:
        media_note = []
        if image_parts:
            media_note.append(f"写真{len(image_parts)}枚")
        if audio_parts:
            media_note.append(f"声{len(audio_parts)}件")
        blocks.append("（このあとに、この人が遺した" + "・".join(media_note) + "を添えます）")
    text_block = "\n\n".join(blocks) if blocks else "（素材はまだほとんどありません）"
    counts = {"onboarding": len(ob_lines), "poems": len(poems),
              "photos": len(image_parts), "voices": len(audio_parts),
              "notes": len(note_lines)}
    return text_block, image_parts, audio_parts, counts


def _persona_fingerprint(user_id):
    """人物プロファイルの材料の指紋。材料（初めの問いの回答＋便りの詩・写真・声）が変われば再生成する判断に使う。"""
    db = get_db()
    urow = db.execute("SELECT onboarding FROM users WHERE id=?", (user_id,)).fetchone()
    answers = _load_onboarding(urow["onboarding"] if urow else None)
    parts = [f"{q}:{(answers[q] or '').strip()}" for q in sorted(answers or {})]
    rows = db.execute(
        "SELECT sent_date, poem, photo, voice FROM letters WHERE user_id=? ORDER BY id",
        (user_id,)).fetchall()
    for r in rows:
        p = (r["poem"] or "").strip()
        has_media = (1 if (r["photo"] or "") else 0, 1 if (r["voice"] or "") else 0)
        parts.append(f"{r['sent_date']}|{len(p)}|{p[:24]}|{has_media[0]}{has_media[1]}")
    nrow = db.execute("SELECT COUNT(*) AS c, MAX(created) AS m FROM notes WHERE user_id=?",
                      (user_id,)).fetchone()
    parts.append(f"notes:{nrow['c']}:{nrow['m'] or ''}")
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def _get_or_make_persona(user_id, allow_generate=True):
    """内部用の人物プロファイルを返す。材料が変わっていなければキャッシュを、変わっていてAIが使えるなら生成し直す。
    生成できない場合は、古いキャッシュがあればそれを、無ければ None を返す（呼び出し側は軽い文脈にフォールバック）。"""
    db = get_db()
    row = db.execute("SELECT persona, persona_src FROM users WHERE id=?", (user_id,)).fetchone()
    cached = row["persona"] if row and "persona" in row.keys() else None
    cached_src = row["persona_src"] if row and "persona_src" in row.keys() else None

    fp = _persona_fingerprint(user_id)
    if cached and cached_src == fp:
        return cached
    if not allow_generate:
        return cached

    gemini_key = os.environ.get("GEMINI_API_KEY")
    claude_key = os.environ.get("ANTHROPIC_API_KEY")
    if not (AI_ENABLED and NETWORK_ENABLED and (gemini_key or claude_key)):
        return cached

    text_block, image_parts, audio_parts, counts = _gather_portrait_inputs(user_id)
    if not any(counts.values()):
        return cached  # 材料がまだ無い

    text = None
    if gemini_key:
        instruction = {"text": PERSONA_PROMPT}
        materials = {"text": text_block}
        # 媒体つきで試し、媒体が原因で失敗したら 音声→画像 の順に外して再試行
        for media in (image_parts + audio_parts, image_parts, []):
            try:
                text = _gemini_multimodal([instruction, materials] + media, gemini_key,
                                          temperature=0.6, max_tokens=1400, thinking_budget=0)
                if text:
                    break
            except Exception as e:
                print(f"[プロファイル生成リトライ] 媒体{len(media)}件で失敗: {e}", flush=True)
                continue
    if not text and claude_key:
        try:
            text = _claude_question(PERSONA_PROMPT + "\n\n" + text_block, claude_key)
        except Exception as e:
            print(f"[プロファイル生成 Claude失敗] {e}", flush=True)

    if not text:
        return cached  # 生成できなければ古いキャッシュ（無ければ None）

    now_iso = datetime.now().isoformat(timespec="seconds")
    with _WRITE_LOCK:
        get_db().execute("UPDATE users SET persona=?, persona_at=?, persona_src=? WHERE id=?",
                         (text, now_iso, fp, user_id))
        get_db().commit()
    return text


@app.route("/api/portrait", methods=["GET"])
@login_required
def api_get_portrait():
    row = get_db().execute("SELECT portrait, portrait_at FROM users WHERE id=?", (uid(),)).fetchone()
    ai_ok = bool(AI_ENABLED and NETWORK_ENABLED and os.environ.get("GEMINI_API_KEY"))
    return jsonify(
        portrait=(row["portrait"] if row and "portrait" in row.keys() else None),
        generated_at=(row["portrait_at"] if row and "portrait_at" in row.keys() else None),
        ai_available=ai_ok)


@app.route("/api/portrait", methods=["POST"])
@login_required
def api_make_portrait():
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not (AI_ENABLED and NETWORK_ENABLED and gemini_key):
        return jsonify(error="いまは肖像を描けません（AI接続が無効です）。"), 503

    text_block, image_parts, audio_parts, counts = _gather_portrait_inputs(uid())
    if not any(counts.values()):
        return jsonify(error="まだ材料がありません。便りを綴るか、初めの問いに答えてみてください。"), 400

    instruction = {"text": PORTRAIT_PROMPT}
    materials = {"text": text_block}

    def build(media):
        # 指示 → テキスト素材 → 媒体（写真・声）の順で渡す
        return [instruction, materials] + media

    # 媒体つきで試し、媒体が原因で 400 等になったら 音声→画像 の順に外して再試行する
    attempts = [image_parts + audio_parts, image_parts, []]
    text = None
    last_e = None
    for media in attempts:
        try:
            # thinking_budget=0：gemini-2.5系の思考トークンが出力枠を食い潰し肖像が途中で切れるのを防ぐ
            text = _gemini_multimodal(build(media), gemini_key, max_tokens=1800, thinking_budget=0)
            if text:
                break
        except Exception as e:
            last_e = e
            print(f"[肖像生成リトライ] 媒体{len(media)}件で失敗: {e}", flush=True)
            continue
    if not text:
        print(f"[肖像生成 最終失敗] {last_e}", flush=True)
        return jsonify(error="肖像の生成に失敗しました。少し時間をおいて、もう一度お試しください。"), 502

    text = _trim_portrait(text, limit=500)

    now_iso = datetime.now().isoformat(timespec="seconds")
    with _WRITE_LOCK:
        get_db().execute("UPDATE users SET portrait=?, portrait_at=? WHERE id=?", (text, now_iso, uid()))
        get_db().commit()
    return jsonify(portrait=text, generated_at=now_iso, counts=counts)


@app.route("/api/letters/<lid>/ask", methods=["POST"])
@login_required
def api_ask_past_self(lid):
    row = own_letter(lid)
    if row is None:
        return jsonify(error="便りが見つかりません。"), 404
    # 本文を材料にAIが語る＝間接的な本文漏れ。開封済みになるまで使わせない。
    if not _is_arrived(row) or not _letter_opened(row):
        return jsonify(error="まだ封の中です。"), 403
    L = letter_to_dict(row)

    now_iso = datetime.now().isoformat(timespec="seconds")

    gemini_key = os.environ.get("GEMINI_API_KEY")
    claude_key = os.environ.get("ANTHROPIC_API_KEY")
    if AI_ENABLED and NETWORK_ENABLED and (gemini_key or claude_key):
        convo = "\n".join(("今の自分: " if m["who"] == "now" else "過去の自分: ") + m["text"] for m in L["thread"])
        # 材料から生成・キャッシュした“人物プロファイル”（価値観・背景の理解）。無ければ従来の軽い文脈に。
        profile_ctx = _get_or_make_persona(uid()) or _profile_context_text(uid())
        # 封をしてから今日までの一筆箋の点群（日付・天気・色・一行）。変化の手がかりとして渡す。
        notes_ctx = _notes_context_text(uid(), since_iso=(L.get("sent_date") or "")[:19] or None)
        prompt = (
            f"あなたは、ある人の「過去の自分」そのものです。下記は{L['sent_date']}に、その人が"
            "未来の自分（＝今のその人）へ宛てて書き残した便りです。あなたはその便りを書いた"
            "当時の本人になりきり、今の自分へ語りかけます。\n\n"
            f"【私（過去の自分）が書いた詩・ことば】\n{L['poem'] or '（なし）'}\n\n"
            + (f"【“私”という人の輪郭（内なる理解。口には出さず、問いの奥行きにだけ使う）】\n{profile_ctx}\n\n" if profile_ctx else "")
            + (f"【封をしてから今日までに、その人が日々残した一筆箋（気分の色とひとこと。口に出して列挙せず、変化を感じ取る手がかりにだけ使う）】\n{notes_ctx}\n\n" if notes_ctx else "")
            + f"【これまでの私たちの対話】\n{convo or '（まだなし）'}\n\n"
            "―― 語りかけ方の約束 ――\n"
            "・焦点は、私自身の内面（そのとき感じたこと・考え・記憶）だけに当てる。外の風景や環境（天気・季節・気温など）の描写や比喩には踏み込まない。\n"
            "・一人称で、今の自分にそっと話しかける（2〜3文、短く）。\n"
            "・直前に『今の自分』が何か言っていたら、まずその言葉を一度受けとめてから返す\n"
            "・絶対にしないこと：分析・指摘・診断、助言・解決・励ましの説教、AIとしての振る舞い。\n"
            "・思いがけない角度から。でもまずは“私が書いた詩・ことば”と直前の対話に根ざすこと。\n"
            "・『“私”という人の輪郭』は、その人の価値観や芯に問いを触れさせるための内なる理解であり、口に出して語ったり、言い当てたりしない。\n"
            "・今の自分が、ふと立ち止まって『あの頃とは変わったな』と感じる“ズレ”に、静かに触れる。\n"
            "・口調は静かで、ウェットで、ノスタルジック。\n"
            "・【最重要】必ず最後を“ひとつの問いかけ”で終える。\n\n"
            "出力は、語りかけの言葉だけ。メタな注釈はつけないこと。"
        )
        text = provider = None
        if gemini_key:
            try:
                text = _gemini_question(prompt, gemini_key)
                provider = "gemini"
            except Exception as e:
                print(f"[Gemini失敗→フォールバック] {e}", flush=True)
        if not text and claude_key:
            try:
                text = _claude_question(prompt, claude_key)
                provider = "claude"
            except Exception as e:
                print(f"[Claude失敗→フォールバック] {e}", flush=True)
        if text:
            with _WRITE_LOCK:
                get_db().execute("INSERT INTO thread (letter_id,who,text,created,created_at,kind) VALUES (?,?,?,?,?,?)",
                                 (lid, "ai", text, date.today().isoformat(), now_iso, "question"))
                get_db().commit()
            return jsonify(text=text, used_ai=True, provider=provider)

    if not NETWORK_ENABLED:
        print("[AI] 定型生成。理由: TAYORI_ENABLE_NETWORK が未設定（外部通信OFF）", flush=True)
    elif not (os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        print("[AI] 定型生成。理由: AI鍵未設定", flush=True)
    text = _build_self_question(L)
    with _WRITE_LOCK:
        get_db().execute("INSERT INTO thread (letter_id,who,text,created,created_at,kind) VALUES (?,?,?,?,?,?)",
                         (lid, "ai", text, date.today().isoformat(), now_iso, "question"))
        get_db().commit()
    return jsonify(text=text, used_ai=False)


def _build_self_question(L):
    import random
    poem = (L.get("poem") or "").strip()
    sent = L.get("sent_date") or ""
    try:
        gap_days = (date.today() - date.fromisoformat(sent[:10])).days
    except Exception:
        gap_days = 0

    asked = [m for m in L.get("thread", []) if m.get("who") == "ai"]
    seen = {m["text"] for m in asked}

    if gap_days >= 365:
        span = f"{gap_days // 365}年前"
    elif gap_days >= 30:
        span = f"{gap_days // 30}ヶ月前"
    elif gap_days >= 1:
        span = f"{gap_days}日前"
    else:
        span = "ついさっき"

    first_line = ""
    for ln in poem.splitlines():
        if ln.strip():
            first_line = ln.strip()
            break

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
        f"あれから、あなたは何を手放した？ 何を握りしめたまま？",
        f"{span}のわたしに、今のあなたから一言だけ伝えるとしたら？",
        "あの頃のわたしが知らなかったことを、ひとつだけ教えて。",
        "今のあなたは、あの時のわたしより少しは自由になれた？",
        f"{span}から今日まで、変わらずにいるものは何？",
    ]

    s = _env_phrase(L.get("seal_env"))
    o = _env_phrase(L.get("open_env"))
    if s and o:
        pool += [
            f"封をしたあの日は「{s}」、開けている今日は「{o}」。あなたの心も、あの頃と変わった？",
            f"あの日の「{s}」の空を、まだ覚えてる？ 今日の「{o}」の下で、何を思う？",
        ]
    elif s:
        pool.append(f"封をしたのは「{s}」の日だった。あの空気を、今のあなたはどう思い出す？")

    fresh = [q for q in pool if q not in seen]
    if not fresh:
        fresh = pool
    return random.choice(fresh)


@app.route("/api/timeline")
@login_required
def api_timeline():
    rows = get_db().execute("SELECT * FROM letters WHERE user_id=? ORDER BY sent_date", (uid(),)).fetchall()
    nodes = []
    for r in rows:
        d = letter_to_dict(r, include_thread=False)
        if d["arrived"]:
            # 開封前の本文は年表にも出さない（開封APIより前に body を配信しない鉄則）
            nodes.append(dict(date=d["sent_date"], kind="sent", id=d["id"],
                              poem=(d["poem"] if _letter_opened(r) else None),
                              photo=bool(d["photo"]), voice=bool(d["voice"]),
                              emos=d["emos"], opened=d["opened"], hidden=d["arrive_hidden"], sealed=False))
        else:
            t_arrive = r["arrive_at"] or (r["arrive_date"] + "T00:00:00")
            nodes.append(dict(date=d["sent_date"], kind="sent", id=d["id"], poem=None, photo=False, voice=False, emos=[], opened=False, hidden=d["arrive_hidden"], sealed=True))
            nodes.append(dict(date=t_arrive[:10], kind="future", id=d["id"], poem=None, photo=False, voice=False, emos=[], opened=False, hidden=d["arrive_hidden"], sealed=True))
    nodes.sort(key=lambda n: n["date"])
    return jsonify(nodes=nodes)


# ── 三ヶ月ごとの章（あなたの変遷）──
# 届いた便りを四半期ごとに束ね、よく使った言葉の傾向＋AIが編む章題・本文で
# 「自分がどういう人だったか」を振り返れるようにする。ログの羅列とは別のキュレーション層。

_CH_WORD_RE = re.compile(
    r"[一-鿿々ヶ]{1,8}"   # 漢字（々・ヶ含む）
    r"|[ァ-ヴー]{2,10}"        # カタカナ
    r"|[A-Za-z]{3,20}"
)
_CH_WORD_STOP = {"中", "時", "日", "事", "為", "様", "達", "今日", "明日", "自分"}


def _quarter_of(date_str):
    return f"{date_str[:4]}-Q{(int(date_str[5:7]) - 1) // 3 + 1}"


def _quarter_label(qkey):
    y, q = qkey.split("-Q")
    q = int(q)
    months = {1: "1月 – 3月", 2: "4月 – 6月", 3: "7月 – 9月", 4: "10月 – 12月"}
    seasons = {1: "冬", 2: "春", 3: "夏", 4: "秋"}
    return f"{y}年 {months[q]}", seasons[q]


def _chapter_materials(user_id):
    """届いた便りを封をした日の四半期ごとに束ねる。封の中の便りは言葉が漏れるので含めない。"""
    db = get_db()
    rows = db.execute("SELECT * FROM letters WHERE user_id=? ORDER BY sent_date, id", (user_id,)).fetchall()
    quarters = {}
    for r in rows:
        if not _is_arrived(r):
            continue
        keys = r.keys()
        qk = _quarter_of(r["sent_date"])
        q = quarters.setdefault(qk, {"poems": [], "moods": [], "sent": 0, "opened": 0, "photos": 0, "voices": 0})
        q["sent"] += 1
        if r["opened"]:
            q["opened"] += 1
        # 言葉は開封済みの便りからだけ束ねる（届いていても未開封なら、まだ封の中の言葉）
        p = (r["poem"] or "").strip()
        if p and _letter_opened(r):
            q["poems"].append((r["sent_date"], p))
        if r["photo"]:
            q["photos"] += 1
        if r["voice"]:
            q["voices"] += 1
        try:
            q["moods"].extend(json.loads(r["emos"] or "[]"))
        except Exception:
            pass
        if "open_mood" in keys and r["open_mood"]:
            q["moods"].append(r["open_mood"])
    return quarters


# 単漢字の直後がこの文字（助詞・句読点・空白）なら「語」とみなす。
# 「夜の」「海を」「雨。」は語だが、「見て」「走る」「増えた」のような動詞の語幹は拾わない。
_CH_PARTICLE_AFTER = set("のをがはにへともでや、。．，！？…・　 ")


def _top_words(poems, top=6):
    c = Counter()
    for p in poems:
        for m in _CH_WORD_RE.finditer(p):
            w = m.group(0)
            if w in _CH_WORD_STOP:
                continue
            if len(w) == 1:
                nxt = p[m.end():m.end() + 1]
                if nxt and nxt not in _CH_PARTICLE_AFTER:
                    continue
            c[w] += 1
    return [{"w": w, "n": n} for w, n in c.most_common(top)]


def _chapter_stats(user_id):
    quarters = _chapter_materials(user_id)
    stats = []
    for qk in sorted(quarters):
        q = quarters[qk]
        label, season = _quarter_label(qk)
        stats.append(dict(key=qk, label=label, season=season,
                          sent=q["sent"], opened=q["opened"],
                          words=_top_words(p for _, p in q["poems"]),
                          moods=[m for m, _ in Counter(q["moods"]).most_common(4)]))
    return stats, quarters


def _chapters_fingerprint(quarters):
    parts = []
    for qk in sorted(quarters):
        q = quarters[qk]
        parts.append(f"{qk}|{q['sent']}|" + "".join(d + p[:20] for d, p in q["poems"]))
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


CHAPTERS_PROMPT = (
    "あなたは、ある人が自分に宛てて書き溜めた言葉を、3ヶ月ごとの「章」として編む編集者です。\n"
    "各章について、次の2つを書いてください。\n"
    "・title：その時期のその人を象徴する短い章題（8〜14字。詩的だが、飾りすぎない）\n"
    "・body：その時期の言葉から読み取れる関心や心の動きを描く本文（80〜140字。"
    "前の章がある場合は、そこからの変化・更新にも触れる）\n\n"
    "― 心がけ ―\n"
    "・診断や決めつけはせず、言葉に現れていることだけを手がかりに。\n"
    "・語りかけ（二人称）にはせず、「〜だった」「〜が増えていった」のような静かな常体で書く。"
    "です・ます調は使わない。\n"
    "・言葉が残っていない章は、便りの数や写真・声の気配から、無理のない範囲で短く。\n\n"
    "出力は次の形式のJSON配列のみ。コードフェンスや説明文は付けない。\n"
    '[{"key":"2026-Q1","title":"…","body":"…"}]\n\n'
    "素材は次のとおりです。\n"
)


def _parse_chapters_json(raw):
    if not raw:
        return None
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return None
    out = {}
    for it in arr if isinstance(arr, list) else []:
        if isinstance(it, dict) and (it.get("key") or "").strip():
            out[it["key"].strip()] = {"title": (it.get("title") or "").strip(),
                                      "body": (it.get("body") or "").strip()}
    return out or None


def _generate_chapters(stats, quarters, gemini_key):
    blocks = []
    for s in stats:
        q = quarters[s["key"]]
        lines = [f"・（{d}）{p}" for d, p in q["poems"][:30]]
        mood = f"　気分タグ: {'、'.join(s['moods'])}" if s["moods"] else ""
        media = []
        if q["photos"]:
            media.append(f"写真{q['photos']}枚")
        if q["voices"]:
            media.append(f"声{q['voices']}件")
        media_line = f"　残したもの: {'・'.join(media)}" if media else ""
        body = "\n".join(lines) if lines else "（言葉は残っていない時期）"
        blocks.append(f"【{s['key']}｜{s['label']}】便り{s['sent']}通{mood}{media_line}\n{body}")
    prompt = CHAPTERS_PROMPT + "\n\n".join(blocks)
    raw = _gemini_multimodal([{"text": prompt}], gemini_key, temperature=0.7,
                             max_tokens=min(240 * len(stats) + 400, 4000), thinking_budget=0)
    return _parse_chapters_json(raw)


@app.route("/api/chapters", methods=["GET"])
@login_required
def api_get_chapters():
    stats, quarters = _chapter_stats(uid())
    row = get_db().execute("SELECT chapters FROM users WHERE id=?", (uid(),)).fetchone()
    try:
        cache = json.loads(row["chapters"]) if row and row["chapters"] else {}
    except Exception:
        cache = {}
    items = cache.get("items") or {}
    for s in stats:
        c = items.get(s["key"]) or {}
        s["title"], s["body"] = c.get("title"), c.get("body")
    stale = (cache.get("fp") != _chapters_fingerprint(quarters)) if items else bool(stats)
    return jsonify(chapters=stats, generated_at=cache.get("at"), stale=stale,
                   ai_available=bool(AI_ENABLED and NETWORK_ENABLED and os.environ.get("GEMINI_API_KEY")))


@app.route("/api/chapters", methods=["POST"])
@login_required
def api_make_chapters():
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not (AI_ENABLED and NETWORK_ENABLED and gemini_key):
        return jsonify(error="いまは章を編めません（AI接続が無効です）。"), 503
    stats, quarters = _chapter_stats(uid())
    if not stats:
        return jsonify(error="まだ材料がありません。届いた便りが増えると、章を編めるようになります。"), 400
    try:
        items = _generate_chapters(stats, quarters, gemini_key)
    except Exception as e:
        print(f"[章生成 失敗] {e}", flush=True)
        items = None
    if not items:
        return jsonify(error="章の生成に失敗しました。少し時間をおいて、もう一度お試しください。"), 502
    now_iso = datetime.now().isoformat(timespec="seconds")
    payload = json.dumps({"fp": _chapters_fingerprint(quarters), "at": now_iso, "items": items},
                         ensure_ascii=False)
    with _WRITE_LOCK:
        get_db().execute("UPDATE users SET chapters=? WHERE id=?", (payload, uid()))
        get_db().commit()
    for s in stats:
        c = items.get(s["key"]) or {}
        s["title"], s["body"] = c.get("title"), c.get("body")
    return jsonify(chapters=stats, generated_at=now_iso, stale=False)


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

ADMIN_READ_CONTENT = bool(os.environ.get("TAYORI_ADMIN_READ_CONTENT", "1"))

def _make_db_snapshot(dest_path):
    src = sqlite3.connect(DB_PATH, timeout=30)
    dst = sqlite3.connect(dest_path)
    try:
        with dst:
            src.backup(dst, pages=64, sleep=0.01)
    finally:
        dst.close()
        src.close()


def _backup_s3_config():
    ep = os.environ.get("TAYORI_BACKUP_S3_ENDPOINT")
    bk = os.environ.get("TAYORI_BACKUP_S3_BUCKET")
    ak = os.environ.get("TAYORI_BACKUP_S3_KEY")
    sk = os.environ.get("TAYORI_BACKUP_S3_SECRET")
    if ep and bk and ak and sk:
        return {"endpoint": ep, "bucket": bk, "key": ak, "secret": sk}
    return None


def _run_backup_to_s3():
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
        _t0 = time.monotonic()
        print(f"[たより] バックアップ開始 {datetime.now().strftime('%H:%M:%S')}", flush=True)
        _make_db_snapshot(tmp)
        _snap_ms = (time.monotonic() - _t0) * 1000.0
        print(f"[たより] スナップショット完了（{_snap_ms:.0f}ms）", flush=True)
        with open(tmp, "rb") as fh:
            blob = gzip.compress(fh.read())
        key = "backups/tayori-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".db.gz"
        s3 = boto3.client("s3", endpoint_url=cfg["endpoint"],
                          aws_access_key_id=cfg["key"], aws_secret_access_key=cfg["secret"])
        s3.put_object(Bucket=cfg["bucket"], Key=key, Body=blob)
        print(f"[たより] バックアップ完了 → {key}（{len(blob)} bytes）", flush=True)
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

    thread_by_user = {}
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
            if r["opened"]: opened += 1
            if r["photo"]: photo += 1
            if r["voice"]: voice += 1
            if r["from_reply"]: reply += 1
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

    def _sum(k): return sum(s[k] for s in user_stats.values())
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

    signups = {}
    for u in users:
        day = (u["created"] or "")[:10]
        if day:
            signups[day] = signups.get(day, 0) + 1
    trend = []
    cumulative_before = 0
    span_days = 14
    start = date.today() - timedelta(days=span_days - 1)
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

    max_new = max((t["new"] for t in trend), default=0)
    for t in trend:
        t["bar_h"] = int(round(t["new"] / max_new * 100)) if (max_new and t["new"]) else 0

    enriched_users = []
    for u in users:
        d = dict(u)
        d["stats"] = user_stats[u["id"]]
        d["has_location"] = bool(u["last_lat"])
        d.pop("onboarding", None)
        enriched_users.append(d)

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
        with _WRITE_LOCK:
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


# ── WSGI（gunicorn 等）経由の起動でも、DBマイグレーションと通知/永続ループを必ず立ち上げる ──
# gunicorn は `app:app` を import するだけで __main__ を実行しない。これが無いと
# start_notifier() が呼ばれず、到着通知メールが永久に送られず・天気待ち伏せ配達も発火しない
# （atexit/SIGTERM の永続化だけはモジュールレベル登録なので動き、「データは残るのにメールだけ来ない」になる）。
# init_db は _init_db_done、start_notifier は _notify_started＋スレッド名で二重起動を防ぐので冪等。
# 背景処理を止めたいときは環境変数 TAYORI_DISABLE_NOTIFIER=1。
def _ensure_started():
    try:
        init_db()
    except Exception as e:
        print(f"[たより] 起動時 init_db 失敗: {e}", flush=True)
    try:
        start_notifier()
    except Exception as e:
        print(f"[たより] 起動時 start_notifier 失敗: {e}", flush=True)


_ensure_started()


if __name__ == "__main__":
    app.run(debug=True, port=5001)
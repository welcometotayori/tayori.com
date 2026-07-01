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
from datetime import datetime, date, timedelta

from flask import Flask, request, jsonify, render_template, g, session, Response
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

_init_db_done = False

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
        ):
            try:
                db.execute(stmt)
            except sqlite3.OperationalError:
                pass

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
    try:
        data = json.loads(raw) if raw else {}
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

@app.route("/")
def index():
    return render_template("index.html", open_letter_id="")

@app.route("/open/<lid>")
def open_letter_page(lid):
    safe = lid if re.fullmatch(r"[A-Za-z0-9]{1,32}", lid or "") else ""
    return render_template("index.html", open_letter_id=safe)

@app.route("/terms")
def terms_page():
    return render_template("terms.html")

@app.route("/privacy")
def privacy_page():
    return render_template("privacy.html")

# 紙＋封筒＋朱の蝋封。実ユーザーの /favicon.ico 404 を消す（ボットの探索404とは別物）。
_FAVICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<rect width='32' height='32' rx='6' fill='#F2EBDD'/>"
    "<rect x='6' y='9' width='20' height='15' rx='2' fill='none' stroke='#3A2E25' stroke-width='1.6'/>"
    "<path d='M6.8 10.5 L16 18 L25.2 10.5' fill='none' stroke='#3A2E25' "
    "stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'/>"
    "<circle cx='16' cy='17' r='3.2' fill='#B5543A'/>"
    "</svg>"
)

@app.route("/favicon.ico")
@app.route("/favicon.svg")
def favicon():
    resp = Response(_FAVICON_SVG, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp

@app.route("/api/onboarding", methods=["GET"])
@login_required
def api_get_onboarding():
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
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        return jsonify(error="その名前はもう使われています。"), 409
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
    return jsonify(auth=True, username=u["username"],
                   email=u["email"] if "email" in u.keys() else None,
                   email_verified=bool(u["email_verified"]) if "email_verified" in u.keys() else False,
                   onboarded=bool(u["onboarded"]) if "onboarded" in u.keys() else True,
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


def _is_arrived(row):
    keys = row.keys() if hasattr(row, "keys") else []
    if "weather_event" in keys and row["weather_event"]:
        met = row["weather_met_at"] if "weather_met_at" in keys else None
        if met:
            return datetime.fromisoformat(met) <= datetime.now()
        return False
    arrive_at = row["arrive_at"] or (row["arrive_date"] + "T00:00:00")
    return datetime.fromisoformat(arrive_at) <= datetime.now()

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
    d["arrived"] = _is_arrived(row)
    
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
        "weather_event": wevent,
        "waiting_weather": bool(wevent),
        "has_photo": bool(row["photo"]),
        "has_voice": bool(row["voice"]),
        "from_reply": bool(row["from_reply"]),
    }

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
        '<div style="background:#FCFBF9;padding:30px 16px;'
        "font-family:'Hiragino Mincho ProN','Yu Mincho',serif;color:#2C2622\">"
        '<div style="max-width:480px;margin:0 auto;background:#ffffff;border:1px solid #E3DDD1;'
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
        "body{background:#FCFBF9;color:#2C2622;font-family:'Hiragino Mincho ProN',serif;"
        "display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:24px}"
        ".card{max-width:380px;text-align:center;background:#ffffff;border:1px solid #E3DDD1;"
        "border-radius:4px;padding:36px 28px;box-shadow:0 10px 30px -18px rgba(44,38,34,.35)}"
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
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
    req = urllib.request.Request(url, headers={"User-Agent": "tayori/1.0"})
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
    import urllib.request
    url = (f"https://api.openweathermap.org/data/2.5/weather"
           f"?lat={lat}&lon={lon}&units=metric&appid={api_key}")
    req = urllib.request.Request(url, headers={"User-Agent": "tayori/1.0"})
    with urllib.request.urlopen(req, timeout=4) as response:
        data = json.loads(response.read().decode())
    temp = (data.get("main") or {}).get("temp", 20.0)
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
    return {"condition": condition, "temp": temp, "tag": _temp_tag(temp)}


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
        time.sleep(grace + 8)   # persist は notifier より少し後ろにずらす
        while True:
            try:
                if _LOCAL_CACHE:
                    _persist_to_durable()
            except Exception as e:
                print(f"[たより] 永続化でエラー（継続）: {e}", flush=True)
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
                   approx=approx, city=city)


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
    poem = (data.get("poem") or "").strip()[:80]
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

    # タイプ再生（TypeTrace）の打鍵スナップショット列。JSON文字列で保存。暴走サイズは捨てる。
    trace = data.get("trace")
    if trace is not None and not isinstance(trace, str):
        trace = json.dumps(trace, ensure_ascii=False)
    if trace and len(trace) > 600_000:
        trace = None

    sent_iso = datetime.now().isoformat(timespec="seconds")
    db = get_db()
    with _WRITE_LOCK:
        db.execute(
            """INSERT INTO letters
               (id,user_id,poem,photo,voice,sent_date,arrive_date,arrive_at,arrive_label,arrive_hidden,opened,emos,from_reply,weather_event,seal_env,stamp,trace)
               VALUES (?,?,?,?,?,?,?,?,?,?,0,'[]',?,?,?,?,?)""",
            (lid, uid(), poem, photo, voice, sent_iso, arrive_date, arrive_at,
             data.get("arrive_label", ""), 1 if data.get("arrive_hidden") else 0,
             1 if data.get("from_reply") else 0, weather_event, seal_env, stamp, trace),
        )
        db.commit()
    return jsonify(id=lid, ok=True)


@app.route("/api/letters/<lid>/trace", methods=["GET"])
@login_required
def api_get_trace(lid):
    """タイプ再生用：その便りの打鍵スナップショット列を返す（到着後のみ）。"""
    row = own_letter(lid)
    if row is None:
        return jsonify(error="便りが見つかりません。"), 404
    if not _is_arrived(row):
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
    
    with _WRITE_LOCK:
        already = row["opened_at"] if "opened_at" in row.keys() else None
        if not already:
            now_iso = datetime.now().isoformat(timespec="seconds")
            get_db().execute(
                "UPDATE letters SET opened=1, open_env=?, open_mood=?, opened_at=?, "
                "reflect_count=COALESCE(reflect_count,0)+1 WHERE id=? AND user_id=?",
                (open_env, open_mood, now_iso, lid, uid()))
        else:
            if open_mood:
                get_db().execute("UPDATE letters SET opened=1, open_env=?, open_mood=? WHERE id=? AND user_id=?",
                                 (open_env, open_mood, lid, uid()))
            else:
                get_db().execute("UPDATE letters SET opened=1, open_env=? WHERE id=? AND user_id=?",
                                 (open_env, lid, uid()))
        get_db().commit()

    return jsonify(ok=True, seal_env=row["seal_env"], open_env=open_env, open_mood=open_mood)


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

    text = (request.get_json(force=True).get("text") or "").strip()
    if not text: return jsonify(error="空の返事です。"), 400

    now_iso = datetime.now().isoformat(timespec="seconds")
    
    with _WRITE_LOCK:
        get_db().execute(
            "INSERT INTO thread (letter_id,who,text,created,created_at,kind) VALUES (?,?,?,?,?,?)",
            (lid, "now", text, date.today().isoformat(), now_iso, "reply"))
        get_db().execute("UPDATE letters SET reflect_count = COALESCE(reflect_count,0)+1 WHERE id=? AND user_id=?", (lid, uid()))
        get_db().commit()
    return jsonify(ok=True)

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


def _profile_context_text(user_id, limit=3):
    row = get_db().execute("SELECT onboarding FROM users WHERE id=?", (user_id,)).fetchone()
    answers = _load_onboarding(row["onboarding"] if row else None)
    if not answers:
        return ""
    qids = [q for q in answers if 0 <= q < len(ONBOARDING_QUESTIONS)]
    random.shuffle(qids)
    lines = [f"・{ONBOARDING_QUESTIONS[q]} → {answers[q]}" for q in qids[:limit]]
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
    "あなたは、ある人を長く見守ってきた、洞察の深い聞き手です。"
    "この人が遺した言葉・写真・声、そして『初めの問い』への答えを手がかりに、"
    "「あなたという人」を一篇の人物素描として描いてください。\n\n"
    "― 大切なこと ―\n"
    "・素材は“答え合わせ”ではなく“手がかり”です。質問と答えをなぞったり、引用・列挙したり、"
    "一問ずつ感想を述べたりは絶対にしないこと。そこから滲み出る人柄を読み取り、地の文で描く。\n"
    "・個々の事実を並べるのではなく、複数の答えの“あいだ”にある共通点・矛盾・繰り返し現れる主題を見つけ、"
    "その人の核を一つの像として束ねる。\n"
    "・描くのは、価値観／ものの見方の癖／心が動く対象／人との距離の取り方、そして"
    "“この人が抱えやすい悩みや揺れ”。表面の出来事ではなく、その奥にある傾向に静かに触れる。\n"
    "・占いや性格類型の決めつけ、励まし・助言・説教はしない。診断もしない。\n"
    "・写真や声があれば、その空気感（色・光・声の温度など）も人物像の手がかりにしてよい。"
    "ただし写っているものを説明・列挙はしない。\n"
    "・二人称（「あなたは…」）で、本人へそっと差し出す手紙のように。\n"
    "・自然で読みやすい日本語で書く。凝りすぎた比喩や難解な言い回しは避け、静かで、温かく、誠実に。\n"
    "・3〜4段落、全体で500〜700字。段落の間は空行（改行を2つ）で区切る。\n"
    "・見出し・箇条書き・前置き・メタな注釈はつけず、人物素描の本文だけを書く。\n\n"
    "手がかりとなる素材は次のとおりです（これは描写の材料であって、回答する対象ではありません）。"
)


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
    ob_lines = []
    for q in sorted(a for a in (answers or {}) if 0 <= a < len(ONBOARDING_QUESTIONS)):
        ans = (answers[q] or "").strip()
        if ans:
            ob_lines.append(f"・{ONBOARDING_QUESTIONS[q]} → {ans}")

    rows = db.execute(
        "SELECT poem, photo, voice, sent_date FROM letters WHERE user_id=? ORDER BY sent_date DESC, id DESC",
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

    blocks = []
    if ob_lines:
        blocks.append("【初めの問いへの答え】\n" + "\n".join(ob_lines))
    if poems:
        blocks.append("【遺した言葉（便り）】\n" + "\n".join(poems))
    if image_parts or audio_parts:
        media_note = []
        if image_parts:
            media_note.append(f"写真{len(image_parts)}枚")
        if audio_parts:
            media_note.append(f"声{len(audio_parts)}件")
        blocks.append("（このあとに、この人が遺した" + "・".join(media_note) + "を添えます）")
    text_block = "\n\n".join(blocks) if blocks else "（素材はまだほとんどありません）"
    counts = {"onboarding": len(ob_lines), "poems": len(poems),
              "photos": len(image_parts), "voices": len(audio_parts)}
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
    if not (NETWORK_ENABLED and (gemini_key or claude_key)):
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
    ai_ok = bool(NETWORK_ENABLED and os.environ.get("GEMINI_API_KEY"))
    return jsonify(
        portrait=(row["portrait"] if row and "portrait" in row.keys() else None),
        generated_at=(row["portrait_at"] if row and "portrait_at" in row.keys() else None),
        ai_available=ai_ok)


@app.route("/api/portrait", methods=["POST"])
@login_required
def api_make_portrait():
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not (NETWORK_ENABLED and gemini_key):
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
            text = _gemini_multimodal(build(media), gemini_key)
            if text:
                break
        except Exception as e:
            last_e = e
            print(f"[肖像生成リトライ] 媒体{len(media)}件で失敗: {e}", flush=True)
            continue
    if not text:
        print(f"[肖像生成 最終失敗] {last_e}", flush=True)
        return jsonify(error="肖像の生成に失敗しました。少し時間をおいて、もう一度お試しください。"), 502

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
    if not _is_arrived(row):
        return jsonify(error="まだ封の中です。"), 403
    L = letter_to_dict(row)

    now_iso = datetime.now().isoformat(timespec="seconds")

    gemini_key = os.environ.get("GEMINI_API_KEY")
    claude_key = os.environ.get("ANTHROPIC_API_KEY")
    if NETWORK_ENABLED and (gemini_key or claude_key):
        convo = "\n".join(("今の自分: " if m["who"] == "now" else "過去の自分: ") + m["text"] for m in L["thread"])
        # 材料から生成・キャッシュした“人物プロファイル”（価値観・背景の理解）。無ければ従来の軽い文脈に。
        profile_ctx = _get_or_make_persona(uid()) or _profile_context_text(uid())
        prompt = (
            f"あなたは、ある人の「過去の自分」そのものです。下記は{L['sent_date']}に、その人が"
            "未来の自分（＝今のその人）へ宛てて書き残した便りです。あなたはその便りを書いた"
            "当時の本人になりきり、今の自分へ語りかけます。\n\n"
            f"【私（過去の自分）が書いた詩・ことば】\n{L['poem'] or '（なし）'}\n\n"
            + (f"【“私”という人の輪郭（内なる理解。口には出さず、問いの奥行きにだけ使う）】\n{profile_ctx}\n\n" if profile_ctx else "")
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
            nodes.append(dict(date=d["sent_date"], kind="sent", id=d["id"], poem=d["poem"],
                              photo=bool(d["photo"]), voice=bool(d["voice"]),
                              emos=d["emos"], opened=d["opened"], hidden=d["arrive_hidden"], sealed=False))
        else:
            t_arrive = r["arrive_at"] or (r["arrive_date"] + "T00:00:00")
            nodes.append(dict(date=d["sent_date"], kind="sent", id=d["id"], poem=None, photo=False, voice=False, emos=[], opened=False, hidden=d["arrive_hidden"], sealed=True))
            nodes.append(dict(date=t_arrive[:10], kind="future", id=d["id"], poem=None, photo=False, voice=False, emos=[], opened=False, hidden=d["arrive_hidden"], sealed=True))
    nodes.sort(key=lambda n: n["date"])
    return jsonify(nodes=nodes)


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
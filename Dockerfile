# たより — Fly.io 用のイメージ（2026-08-02）
#
# Render は render.yaml のまま動き続ける。こちらは**東京に origin を置いたら
# どうなるか**を測るための、並んで立つもう一つの器。どちらかを消す前に、
# fly.dev の仮ホストで数字を見てから決める（docs/fly-rehearsal.md）。
#
# 依存はすべてホイールで入る（Flask / gunicorn / boto3 / anthropic / numpy /
# tokenizers）。torch も onnxruntime も要らないので、コンパイラを入れる段も、
# ビルド用と実行用を分ける段も要らない。一段でいい。
FROM python:3.12-slim

# .pyc を書かない（読み取り専用の層に置いても意味が無い）／ログを溜めない
# （溜めると `fly logs` に出るのが遅れて、起動の様子が見えなくなる）。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 時刻表（tzdata）。**これを省くと宙が UTC で動く。**
# app.py は起動直後に TZ=Asia/Tokyo を立てて tzset() を呼ぶが、/usr/share/zoneinfo が
# 無い器では黙って UTC のままになる。この宙は一日の変わり目が朝4時（JST）で、季節も
# 時刻帯も air_distance の成分なので、9時間ずれたら**出会いの選び方そのもの**が狂う。
# しかも誰も気づけない（エラーにならない）。3MB の保険を惜しむ場所ではない。
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements だけ先に写して入れる。app.py を直しただけの再ビルドで、
# pip の層を作り直さないため（毎回2分待たない）。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 意味の索引（semantic/ 12MB）も種のことば（seed/ 9.3MB）もイメージに同梱する。
# .dockerignore が tayori.db（201MB）と .git（65MB）を外しているので、
# ここで入るのはコードと索引だけ。
COPY . .

# ── 器の中で決まっていること ────────────────────────────────
# 鍵と外への宛先は `fly secrets set` で入れる（ここにもリポジトリにも書かない）。
# 素の値は fly.toml の [env] に置く＝一覧で見える場所にまとめる。
ENV TAYORI_DB_PATH=/var/data/tayori.db

EXPOSE 8080

# workers 1 は Render と同じ約束。SQLite の書き手は一人、通知ループも一つ。
# Fly ではここに**マシンを1台に保つ**という二つ目の約束が要る（fly.toml）。
CMD ["gunicorn", "app:app", \
     "--bind", "0.0.0.0:8080", \
     "--workers", "1", "--threads", "8", "--timeout", "120"]

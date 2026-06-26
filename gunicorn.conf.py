"""gunicorn 設定。gunicorn は起動時にカレントの gunicorn.conf.py を自動で読むため、
Render が既定の `gunicorn app:app` で起動しても（Blueprintのstartコマンドが効かなくても）
ここの設定が必ず適用される。狙いは「1ワーカーのまま、複数リクエストを同時に捌く」こと。
"""
import os

# SQLite は複数プロセスで壊れやすく、通知スレッド(start_notifier)も多重起動になるため
# ワーカーは1固定。その代わりスレッドを増やして同時実行性を確保する。
workers = 1
# 1ワーカー内のスレッド数。1だと「重い1リクエスト」で全体が固まる（=送信中のままハング）。
# 8にして、ログインや天気取得が走っていても他のリクエストを並行で捌けるようにする。
threads = 8
worker_class = "gthread"
# 重い処理(AI生成など)で即killされないよう少し長め。だが無限には待たない。
timeout = 120
graceful_timeout = 30
# Render は $PORT（通常10000）で待ち受けることを要求する。
bind = "0.0.0.0:" + os.environ.get("PORT", "10000")
# 起動ログを見やすく
accesslog = "-"
errorlog = "-"

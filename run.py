"""
たより — ランチャー
    python3 run.py
で起動し、ブラウザが自動で開きます。
"""

import os
import threading
import webbrowser

import app as tayori

PORT = int(os.environ.get("TAYORI_PORT", "5000"))
URL = f"http://127.0.0.1:{PORT}"


def _open_browser():
    try:
        webbrowser.open(URL)
    except Exception:
        pass


if __name__ == "__main__":
    print()
    print("  たより を起動します… ブラウザが自動で開きます")
    print(f"  開かない場合はこのURLを手で開いてください → {URL}")
    print()

    tayori.init_db()
    tayori.start_notifier()

    # サーバー起動の少し後にブラウザを開く
    threading.Timer(1.0, _open_browser).start()

    # reloader を切る（二重起動・スレッド二重化を防ぐ）
    tayori.app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)

"""部屋の席（円配置）のテスト・ドライバ。

    .venv/bin/python scripts/test_room_seat.py

出荷されるコードをそのままテストするための作り：
templates/mood.html の `pure:room-seat` 区画を切り出し、scripts/test_room_seat.js を
つないで macOS 同梱の JavaScriptCore（jsc）で走らせる。テスト用の写しは作らない
——写しを持つと、片方だけ直して「通っているのに壊れている」状態が生まれる。

node は入っていない前提（このリポジトリはビルドツールを持たない）。jsc は macOS に
標準で入っているので、追加の依存は無い。jsc が無い環境では skip して 0 で終わる。
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(__file__), "..")
TEMPLATE = os.path.join(ROOT, "templates", "mood.html")
TESTJS = os.path.join(ROOT, "scripts", "test_room_seat.js")
JSC = ("/System/Library/Frameworks/JavaScriptCore.framework/Versions/Current"
       "/Helpers/jsc")

BEGIN = r"/\* ══ pure:room-seat ═+"
END = r"/\* ══ /pure:room-seat ═+"


def extract(src):
    """マーカーの内側だけを取り出す。見つからなければ失敗させる（黙って通さない）。"""
    m = re.search(BEGIN + r".*?\*/(.*?)" + END, src, re.S)
    if not m:
        raise SystemExit("NG: templates/mood.html に pure:room-seat 区画が見つかりません")
    block = m.group(1)
    for banned in ("document.", "window.", "Math.random", "Date.now", "fetch("):
        if banned in block:
            raise SystemExit(f"NG: 純関数の区画に {banned} が入っています（副作用は外へ）")
    return block


def main():
    src = open(TEMPLATE, encoding="utf-8").read()
    block = extract(src)
    test = open(TESTJS, encoding="utf-8").read()
    if not os.path.exists(JSC):
        print("skip: jsc が見つからないので JS のテストは走らせません")
        return 0
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as f:
        f.write(block + "\n" + test)
        path = f.name
    try:
        r = subprocess.run([JSC, path], capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        return r.returncode
    finally:
        os.unlink(path)


if __name__ == "__main__":
    raise SystemExit(main())

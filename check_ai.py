"""
たより — AI接続の自己診断ツール。

「定型文しか来ない」ときに、原因（通信OFF／鍵が無い／鍵が無効／モデルが使えない 等）を
ひとめで特定するためのスクリプト。アプリ本体は起動しない。

使い方：鍵を設定したのと「同じターミナル」で、続けて実行する。
    cd ~/Desktop/tayori
    export TAYORI_ENABLE_NETWORK=1
    export GEMINI_API_KEY=AIzaSy...       # 自分の鍵
    .venv/bin/python check_ai.py
"""

import os
import json
import urllib.request
import urllib.error

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv():
    """app.py と同じく、同フォルダの .env を os.environ に流し込む。
    これが無いと、アプリ本体は .env を読むのに、この診断ツールだけ『未設定』に見えてしまう。"""
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


def main():
    _load_dotenv()
    print("―――  たより AI接続チェック  ―――")
    net = os.environ.get("TAYORI_ENABLE_NETWORK")
    gkey = os.environ.get("GEMINI_API_KEY")
    ckey = os.environ.get("ANTHROPIC_API_KEY")
    model = os.environ.get("TAYORI_GEMINI_MODEL", "gemini-2.5-flash-lite")

    # ① 外部通信
    print(f"① TAYORI_ENABLE_NETWORK = {net!r}",
          "→ OK" if net else "→ ❌ 未設定。`export TAYORI_ENABLE_NETWORK=1` が必要")

    # ② 鍵
    if gkey:
        print(f"② GEMINI_API_KEY = 設定あり（先頭 {gkey[:6]}… / 長さ {len(gkey)}）")
        # プレースホルダをそのまま貼ったケース（日本語や … が混ざる）を先に弾く
        looks_placeholder = ("…" in gkey or "..." in gkey or "（" in gkey or "鍵" in gkey)
        try:
            gkey.encode("ascii")
            ascii_ok = True
        except UnicodeEncodeError:
            ascii_ok = False
        if looks_placeholder or not ascii_ok:
            print("   ❌ これは例文（プレースホルダ）のようです。")
            print("      `AIzaSy...（あなたの鍵）` をそのまま貼っていませんか？")
            print("      『（あなたの鍵）』の部分を、自分の本物の鍵に置き換えてください。")
            return
        # Geminiの鍵は従来 'AIzaSy…' 形式だが、新しく 'AQ.' で始まる形式も発行される。
        if not (gkey.startswith("AIza") or gkey.startswith("AQ.")):
            print("   ⚠️ 鍵が 'AIza' でも 'AQ.' でも始まっていません。Geminiの鍵ではない可能性大。")
            print("      Google AI Studio で発行した鍵（AIzaSy… または AQ.… で始まる）を使ってください。")
        elif len(gkey) < 30:
            print(f"   ⚠️ 鍵が短すぎます（{len(gkey)}文字）。途中で切れているかも。全部コピーできているか確認を。")
    else:
        print("② GEMINI_API_KEY = ❌ 未設定")
    print(f"   （参考）ANTHROPIC_API_KEY = {'あり' if ckey else 'なし'}")
    print(f"③ 使うモデル = {model}")

    # 早期に止まる条件
    if not net:
        print("\n→ 外部通信OFFのためAIは呼ばれません。①を設定して、同じ窓でアプリを起動してください。")
        return
    if not gkey and not ckey:
        print("\n→ 鍵が無いため定型文になります。GEMINI_API_KEY を設定してください。")
        return
    if not gkey:
        print("\n→ Geminiの鍵は無し（Claude設定）。Gemini化したいなら GEMINI_API_KEY を設定してください。")
        return

    # ④ 実際にGeminiへ短いリクエスト
    #    アプリ本体(_gemini_question)と同じく、503/429 のときは別モデルへ自動で切り替える。
    #    1モデルだけ見て「失敗」と誤判定しないため（flash-lite は混雑で503が出やすい）。
    print("\n④ Geminiへ実際に短い問い合わせを送ってみます…")
    fallbacks = ["gemini-2.5-flash-lite", "gemini-flash-lite-latest",
                 "gemini-2.0-flash-lite", "gemini-2.5-flash"]
    preferred = os.environ.get("TAYORI_GEMINI_MODEL")
    models = ([preferred] if preferred else []) + [m for m in fallbacks if m != preferred]
    body = json.dumps({"contents": [{"parts": [{"text": "こんにちは。ひとことだけ返してください。"}]}]}).encode()
    last_busy = None  # 全モデル混雑だった時の案内用

    for m in models:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{m}:generateContent")
        # 鍵は X-goog-api-key ヘッダーで渡す（新形式 AQ.… は ?key= だと 401 になる）。
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json",
                                              "X-goog-api-key": gkey}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode())
            cands = data.get("candidates") or []
            parts = (cands[0].get("content") or {}).get("parts") or [] if cands else []
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                print(f"   ✅ 成功！ Geminiが応答しました（モデル: {m}）：")
                print("      " + text)
                print("\n→ 設定は正しいです。アプリも『この同じターミナル』から")
                print("   .venv/bin/python run.py  で起動すれば、本物のAIになります。")
                return
            print(f"   ⚠️ {m}: 応答は来たが本文が空。別モデルを試します…")
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:500].replace("\n", " ")
            # 鍵・権限の問題はモデルを変えても直らないので、ここで確定的に報告して終了。
            if e.code == 400 and "API key not valid" in msg:
                print(f"   ❌ HTTP 400：鍵が無効です。Google AI Studio で取り直してください。\n      {msg}")
                return
            if e.code == 401:
                print(f"   ❌ HTTP 401：鍵が古い／失効。AI Studio で作り直し .env を更新してください。\n      {msg}")
                return
            if e.code == 403:
                print(f"   ❌ HTTP 403：権限エラー。鍵の有効化や Generative Language API の利用可否を確認。\n      {msg}")
                return
            # 一時的(429/503)や、そのモデル固有(404/未対応)は次のモデルへ。
            if e.code in (429, 503):
                last_busy = (m, e.code)
                print(f"   ⚠️ {m}: HTTP {e.code}（混雑/上限・認証は成功）。別モデルへ…")
            else:
                print(f"   ⚠️ {m}: HTTP {e.code}。別モデルへ… {msg}")
        except Exception as e:
            print(f"   ⚠️ {m}: 通信エラー {type(e).__name__} {e}。別モデルへ…")

    # ここに来た＝全モデルでテキストが取れなかった
    if last_busy:
        print(f"\n→ すべてのモデルが混雑/上限でした（最後: {last_busy[0]} = HTTP {last_busy[1]}）。")
        print("   ❗ ただし認証は成功しているので、鍵・通信は正常です。少し待って再実行を。")
        print("   （実アプリは混雑時、定型文に静かにフォールバックします）")
    else:
        print("\n→ どのモデルでも応答本文が得られませんでした。上のエラーを確認してください。")


if __name__ == "__main__":
    main()

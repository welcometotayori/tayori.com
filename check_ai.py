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


def main():
    print("―――  たより AI接続チェック  ―――")
    net = os.environ.get("TAYORI_ENABLE_NETWORK")
    gkey = os.environ.get("GEMINI_API_KEY")
    ckey = os.environ.get("ANTHROPIC_API_KEY")
    model = os.environ.get("TAYORI_GEMINI_MODEL", "gemini-1.5-flash")

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
        if not gkey.startswith("AIza"):
            print("   ⚠️ 鍵が 'AIza' で始まっていません。Geminiの鍵ではない可能性大。")
            print("      Google AI Studio の『AIzaSy…』で始まる鍵を使ってください。")
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
    print("\n④ Geminiへ実際に短い問い合わせを送ってみます…")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={gkey}")
    body = json.dumps({"contents": [{"parts": [{"text": "こんにちは。ひとことだけ返してください。"}]}]}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
        cands = data.get("candidates") or []
        parts = (cands[0].get("content") or {}).get("parts") or [] if cands else []
        text = "".join(p.get("text", "") for p in parts).strip()
        if text:
            print("   ✅ 成功！ Geminiが応答しました：")
            print("      " + text)
            print("\n→ 設定は正しいです。アプリも『この同じターミナル』から")
            print("   .venv/bin/python run.py  で起動すれば、本物のAIになります。")
        else:
            print("   ⚠️ 応答は来ましたが本文が空でした。レスポンス抜粋：")
            print("      " + json.dumps(data, ensure_ascii=False)[:300])
    except urllib.error.HTTPError as e:
        msg = e.read().decode()[:500].replace("\n", " ")
        print(f"   ❌ HTTP {e.code} エラー：{msg}")
        if e.code == 400 and "API key not valid" in msg:
            print("   → 鍵が無効です。AIzaSy… の正しい鍵を取り直してください。")
        elif e.code in (403,):
            print("   → 権限エラー。鍵の有効化や、Generative Language API の利用可否を確認。")
        elif e.code == 404 or "is not found" in msg.lower() or "not supported" in msg.lower():
            print(f"   → モデル『{model}』が使えないようです。次を試してください：")
            print("      export TAYORI_GEMINI_MODEL=gemini-2.0-flash")
        elif e.code == 429:
            print("   → 無料枠のレート上限です。少し待ってから再実行してください。")
    except Exception as e:
        print(f"   ❌ 通信エラー：{type(e).__name__} {e}")


if __name__ == "__main__":
    main()

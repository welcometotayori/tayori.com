# たより — tayori

自分宛ての遅延郵便。投函 → 封をする → 届く頃に受信へ現れる。

## フォルダ構成

```
tayori/
├── app.py              … 本体（Flask）
├── run.py              … 起動ランチャー（ブラウザ自動オープン）
├── requirements.txt
├── templates/
│   ├── index.html      … アプリ本体の画面
│   └── admin.html      … 管理ダッシュボード
└── tayori.db           … 起動時に自動生成（消してOK・初期化される）
```

`index.html` と `admin.html` は **必ず `templates/` の中**に置くこと。
直下に置くと `TemplateNotFound: index.html` で 500 になる。

## 起動

```bash
python3 run.py
```

→ http://127.0.0.1:5000 が自動で開く。
ポートを変えたいときは `TAYORI_PORT=5055 python3 run.py`。

初回起動時にデモ用アカウントが作られる：

- ユーザー: `demo` / 合言葉: `demo1234`（便りのサンプル入り）
- ユーザー: `admin` / 合言葉: `admin.welcometotayori`（管理ダッシュボードで「保護対象」扱い）

## 管理ダッシュボード

ブラウザで管理URLを開く → http://127.0.0.1:5000/admin.welcometotayori

全体統計（ユーザー数・便り数・受信/配送中/天気待ちの内訳）、登録ユーザー数の推移、
最近の便り（中身つき・クリックで全文と対話）、ユーザー一覧（投函数つき）が見られる。
各ユーザーは関連データごと削除可能。`admin` アカウントは削除不可。

便りの中身の閲覧は `TAYORI_ADMIN_READ_CONTENT` で制御（既定オン）。
公開時にプライバシーを守りたい場合は無効化する：

```bash
export TAYORI_ADMIN_READ_CONTENT=""
```

⚠️ デフォルトでは誰でも開ける。公開する場合は必ずトークンを設定する：

```bash
export TAYORI_ADMIN_TOKEN="好きな文字列"
```

設定後は `/admin.welcometotayori?token=好きな文字列` でのみアクセス可能になる。

## 任意の設定（環境変数）

| 変数 | 用途 |
|------|------|
| `TAYORI_PORT` | 起動ポート（既定 5000） |
| `TAYORI_ADMIN_TOKEN` | 管理画面のアクセストークン |
| `TAYORI_ADMIN_PASSWORD` | admin アカウントのパスワード（既定 admin.welcometotayori） |
| `TAYORI_ADMIN_READ_CONTENT` | 管理画面で便りの中身を読めるか（既定オン。`""`で無効化＝プライバシー保護） |
| `TAYORI_ENABLE_NETWORK=1` | 天気取得・メール送信などの外部通信を有効化 |
| `TAYORI_OWM_KEY` | OpenWeatherMap の API キー。設定すると天気取得に OWM を優先（未設定なら Open-Meteo） |
| `TAYORI_BASE_URL` | メールの開封リンクに使う公開URL |
| `TAYORI_SMTP_USER` / `TAYORI_SMTP_PASS` | メール通知用SMTP（Gmail等） |
| `GEMINI_API_KEY` | Google Gemini で問いを AI 生成（**無料枠・クレカ不要**。優先して使われる） |
| `TAYORI_GEMINI_MODEL` | Gemini のモデル（既定 `gemini-1.5-flash`） |
| `ANTHROPIC_API_KEY` | Claude で問いを AI 生成（有料。Gemini の鍵が無いときの代替） |
| `TAYORI_AI_MODEL` | Claude のモデル（既定 `claude-opus-4-8`。例: `claude-sonnet-4-6`） |
| `TAYORI_CHECK_INTERVAL` | 天気判定・通知チェックの間隔（秒・既定30） |

天気とメールは `TAYORI_ENABLE_NETWORK=1` を立てないと動かない（無料ホスティングの外部通信遮断対策で既定OFF）。
天気は既定で **Open-Meteo**（無料・APIキー不要）。`TAYORI_OWM_KEY` を設定すると **OpenWeatherMap** を優先し、失敗時は自動で Open-Meteo に切り替わる。

### 「過去の自分」からの問いを AI で生成する

返信を送る（＝過去の自分から問いをもらう）ときの問いを、外部AIで生成できる。
未設定でもローカルの定型生成にフォールバックするので、以下は任意。
**鍵が2種類あれば Gemini が優先**され、Gemini が無いときだけ Claude が使われる。

#### おすすめ：Gemini（無料枠・クレカ登録不要・追加ライブラリ不要）

1. [Google AI Studio](https://aistudio.google.com/) にGoogleアカウントでログイン
2. 「Get API key」→「Create API key」で `AIzaSy...` で始まる鍵を取得
3. 起動時に渡す：

```bash
cd ~/Desktop/tayori
export TAYORI_ENABLE_NETWORK=1        # 外部通信ON
export GEMINI_API_KEY=AIzaSy...       # 取得した無料の鍵（チャット等に貼らない）
# export TAYORI_GEMINI_MODEL=gemini-1.5-flash  # 任意。既定もこれ
.venv/bin/python run.py
```


## メール通知の仕組み（公開運用向け）

- **メール確認（ダブルオプトイン）**: 登録・変更時に確認メールを送る。`/verify/<token>` を
  開くまで `email_verified=0` のままで、**確認が済むまで通知は送られない**（リンクは7日間有効）。
- **配信停止**: すべての通知メール末尾に `/unsubscribe/<token>` を付与。踏むと `notify_enabled=0`
  になり以後の通知が止まる。再開はアプリの📧設定からメールを登録し直す。
- **再試行の打ち切り**: 送信失敗は `notify_attempts` を加算し、`MAX_NOTIFY_ATTEMPTS`(=5)回で
  `notify_failed=1` を立てて以後リトライしない（無効アドレスでログが埋まるのを防ぐ）。
  メールを設定し直すと、その人の便りの失敗フラグはリセットされ再挑戦する。

### 送信業者（送信上限・到達率）

`send_email` は標準SMTP。Gmailの他、**Resend / SendGrid / Amazon SES** などの SMTP でもそのまま動く。
独自ドメインを業者で認証（SPF/DKIM/DMARC）すれば、到達率と送信上限が大きく改善する。設定例（Resend）:

```bash
export TAYORI_ENABLE_NETWORK=1
export TAYORI_SMTP_HOST=smtp.resend.com
export TAYORI_SMTP_PORT=587
export TAYORI_SMTP_USER=resend
export TAYORI_SMTP_PASS="re_xxxxxxxx"          # Resend の API キー
export TAYORI_MAIL_FROM="たより <noreply@あなたのドメイン>"
export TAYORI_BASE_URL="https://あなたの公開URL"
```

## DBを作り直したいとき

```bash
rm tayori.db .secret_key
python3 run.py
```
# tayori

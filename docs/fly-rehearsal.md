# Fly.io 試しデプロイの手順書（東京に origin を置いたら何ms になるか）

最終更新: 2026-08-02

## これは何のための手順か

**引っ越しの手順書ではない。測るための手順書。**

2026-08-02 に本番を測ると、Cloudflare の edge までは 16ms（NRT）で届いているのに、
TTFB は 150〜300ms あった。差は Render の origin が Oregon（`gcp-us-west1`）に
あることから来ている。東京に origin を置けば 40〜70ms になるはず——だが、
**「はず」のために本番を動かすのは順番が逆**なので、`tayori-nrt.fly.dev` に
写しを立てて数字を見る。

数字が期待どおりでなければ `fly apps destroy` して終わり。失うものは無い。
本番の切替は、数字を見たあとに別の日として決める。

---

## 先に読む二つの赤線

この試しデプロイは**本番のDBの写し**を載せる。つまり、うっかりすると
**本物の利用者にメールが飛び**、**本物のバックアップが上書きされる**。

### 赤線 1 — 通知ループを必ず止める

```bash
fly secrets set TAYORI_DISABLE_NOTIFIER=1
```

これを入れ忘れると、写しの中の「まだ届いていない便り」を仮ホストが配達しはじめる。
利用者には本番から届いた便りと区別がつかない。`start_notifier()` はこの旗一つで
通知ループも維持ループも両方立ち上げないので、これだけで足りる。

### 赤線 2 — R2 のバックアップ鍵を絶対に入れない

`TAYORI_BACKUP_S3_ENDPOINT` / `_BUCKET` / `_KEY` / `_SECRET` の**四つが揃うと
日次バックアップが動きだす**（`_backup_s3_config()`）。仮ホストの写しが本番の
バックアップを上書きしにいく。四つのうち一つでも欠けていれば動かないので、
**一つも入れない**のがいちばん確実。赤線1でループごと止まっているが、二重に守る。

あわせて：仮ホストには本物の利用者のデータが載る。測り終わったら
`fly apps destroy tayori-nrt` で消すこと。何日も放っておかない。

---

## 手順

### 0. 用意

```bash
brew install flyctl
fly auth login
```

### 1. アプリの枠だけ作る（まだ上げない）

```bash
fly apps create tayori-nrt
```

`tayori-nrt` が取られていたら別名にして、`fly.toml` の `app =` と
`TAYORI_BASE_URL` の二か所を同じ名前に直す。

### 2. 東京にボリュームを作る

```bash
fly volumes create tayori_data --region nrt --size 3 --app tayori-nrt
```

`fly.toml` の `[[mounts]] source` と同じ名前であること。3GB は 201MB の本体と、
バックアップや永続化が作る `.tmp` が同居しても詰まらない余白。

### 3. 秘密を入れる（赤線2の四つは入れない）

```bash
fly secrets set --app tayori-nrt \
  TAYORI_DISABLE_NOTIFIER=1 \
  TAYORI_SECRET="$(python3 -c 'import secrets;print(secrets.token_hex(32))')" \
  TAYORI_ADMIN_PASSWORD='（本番とは違うものにする）' \
  GEMINI_API_KEY='（Google AI Studio の鍵）' \
  TAYORI_SMTP_PASS='（使わないが、無いと起動時に警告が出る。空でよい）'
```

`TAYORI_SECRET` は本番と別にする。同じにすると本番のセッション Cookie が
仮ホストでも通ってしまう。`TAYORI_SHADEMAP_KEY` は地図が撤去済みなので要らない。

### 4. 上げる

```bash
fly deploy --app tayori-nrt
```

初回は pip の層を作るので3〜5分。`fly logs` に

```
[たより] 通知ループは TAYORI_DISABLE_NOTIFIER=1 のため停止中
```

が出ていることを**目で確かめる**（赤線1が効いている証拠）。この時点では
`/var/data/tayori.db` は `init_db()` が作った空のDBで、宙には何も無い。

あわせて、**出ていてはいけない行**が二つある：

- `⚠️ 指定のDB保存先 /var/data/tayori.db に書き込めません` … ボリュームが
  付いていない。`_resolve_db_path()` は書けない場所を黙って諦めて一時領域へ
  逃げるので、**この一行だけが唯一の合図**。出ていたら
  `fly volumes list` と `fly.toml` の `source` の綴りを確かめる。
- 時刻が9時間ずれている … `tzdata` が入っていない。Dockerfile で入れてあるが、
  イメージを触ったときはここを見る（`fly ssh console -C "date"` が JST を返すか）。

### 5. DBの写しを運ぶ

201MB を送る。手元の `tayori.db` は本番とほぼ同じ（差は自分の書き込みだけ）
なので、測るだけならこれで足りる。厳密にやるなら R2 の最新バックアップを
落として展開したものを使う。

```bash
fly ssh sftp shell --app tayori-nrt
# sftp> put /Users/koseitsutsui/Desktop/tayori/tayori.db /var/data/tayori.db.incoming
# sftp> quit
```

置き換えて、入れ直す：

```bash
fly ssh console --app tayori-nrt -C "mv /var/data/tayori.db.incoming /var/data/tayori.db"
fly machine restart --app tayori-nrt
```

`mv` は名札を付け替えるだけで、動いているプロセスが掴んでいる古い実体は
そのまま生きている（消えるのは最後の掴み手が離したとき）。だから走ったまま
入れ替えても新しいほうは壊れない——ただし**入れ替えた直後に必ず restart**。
しないと、動いているプロセスはいつまでも古い実体を見続ける。

> **201MB の sftp が遅い／途中で切れるとき**は、機械の側から R2 の最新
> バックアップを引くほうが速い（データセンタの中で完結する）。鍵は
> `fly secrets` に入れず、その場限りの環境変数として渡すこと——**走っている
> アプリの環境とは別物**なので、`_backup_s3_config()` の四つが揃うことは無く、
> 赤線2を踏まない。
>
> ```bash
> fly ssh console --app tayori-nrt
> # 機械の中で（履歴に残したくないので、貼ったあと exit する）
> export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…
> python3 -c "import boto3,gzip,shutil;\
> s=boto3.client('s3',endpoint_url='https://…r2.cloudflarestorage.com');\
> s.download_file('tayori-backups','backups/最新のキー','/var/data/bk.gz')"
> gzip -dc /var/data/bk.gz > /var/data/tayori.db.incoming && rm /var/data/bk.gz
> ```

起動したら `fly logs` に板が積まれるのを見る：

```
[たより] 漂流物の板: 100000片 × 256次元 を N秒で積みました（102MB）
```

### 6. 測る

```bash
for i in 1 2 3; do
  curl -s -o /dev/null -w "fly  robots ttfb=%{time_starttransfer}s connect=%{time_connect}s\n" https://tayori-nrt.fly.dev/robots.txt
done
for i in 1 2 3; do
  curl -s -o /dev/null -w "rndr robots ttfb=%{time_starttransfer}s connect=%{time_connect}s\n" https://tayori-letter.com/robots.txt
done
```

同じことを `/api/rooms` でもやる。**必ず同じ回線・同じ時間帯で交互に**測ること
（別の日に測った数字どうしを比べない）。

| | connect | TTFB /robots.txt | TTFB /api/rooms |
|---|---|---|---|
| Render（Oregon・2026-08-02 実測） | 16ms | 153 / 284 / 296ms | 150 / 264ms |
| Fly（nrt） | | | |

### 7. もう一つだけ測る — `TAYORI_DB_LOCAL_CACHE`

`fly.toml` では `0`（ボリュームの上で直に動かす）にしてある。Render で `1` に
なっているのは、あちらの `/var/data` がネットワーク越しで遅いから。Fly の
ボリュームは機械に直付けの NVMe なので迂回は要らない——**はずだが、これも測る**。

```bash
fly secrets set --app tayori-nrt TAYORI_DB_LOCAL_CACHE=1   # 入れ直して測る
fly secrets unset --app tayori-nrt TAYORI_DB_LOCAL_CACHE   # 0（fly.toml の値）に戻す
```

差が無ければ `0` を採る。`0` のほうが：

- 201MB を30秒ごとに二度コピーしなくなる（`_persist_to_durable`）
- **落ちても直近30秒の書き込みが消えない**（いまの Render にはこの窓がある）

### 8. 片付ける

```bash
fly apps destroy tayori-nrt
```

数字を残して、器は消す。**本番の切替はここから先の別の話**で、
そのときに必要なのは (a) 書き込みを止める15〜30分の窓、(b) DNS の TTL を
事前に60秒へ、(c) `fly certs add tayori-letter.com`、の三つ。

---

## この構成で壊れやすいところ（切替を決めたときに読む）

1. **マシンは1台のまま** — SQLite の書き手は一人、通知ループも一つ。
   `fly scale count 1` を毎回確かめる。`fly.toml` の `strategy = "immediate"` は
   新旧が並んで立たないようにするためで、ここを `bluegreen` にすると
   二台目がボリュームを取りにいって失敗する（失敗するだけまだ良い）。
2. **`auto_stop_machines = false` を外さない** — 止まると通知ループも探すの板も
   消える。次の一人が数十秒待つ。
3. **メモリは 1GB** — 512MB にしない（2026-08-01 に Render で 597MB まで
   膨らんで落ちた実績がある）。
4. **前段の Cloudflare が無くなる** — いま `/cdn-cgi/trace` が返るのは Render の
   ものであって、こちらが張ったものではない。盾が要るなら自分で被せる。
   7/31 に踏んだ JP→SEA は「Render の CF」の話なので、自分で被せて NRT colo →
   Fly nrt にする分にはまっすぐ。
5. **`.dockerignore` を消さない** — `*.db` を外し忘れると、201MB の古い DB が
   イメージに焼かれて、デプロイのたびにボリュームの本体を上書きしにいく。

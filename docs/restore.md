# 控えから戻す

2026-08-12。8/6 の公開前点検で残っていた最後の宿題——**控えがあることと、戻せることは別**——を、
実際に通してから書いた紙。落ちてから読む紙なので、上から順に打てば戻るように書いてある。

## 何が、どこに在るか

| | |
|---|---|
| 上げているもの | live DB の丸ごと（`.db.gz`）。実測 201MB → 135MB |
| 置き場 | Cloudflare R2 の `backups/tayori-YYYYMMDD-HHMMSS.db.gz` |
| いつ | 起動の5分後に一本、以後24時間ごと（＝デプロイのたびに一本増える） |
| 何本 | 14世代（`TAYORI_BACKUP_KEEP`）。古いものから消える |
| 動く条件 | R2 の4変数が**4つとも**入っている時だけ。起動ログの「オフサイトBK有効」で分かる |

守りたいのは **人のことば（数MB）** で、201MB の大半は青空文庫＝作り直せる（`scripts/ingest_aozora.py`）。

## 失う窓は、最大で「直近24時間 ＋ 30秒」

- 控えは**日に一度**。前の控えから落ちるまでの分は、控えには無い。
- そのうえ live DB は `/tmp/tayori-live.db` に在り、30秒ごとに `/var/data/tayori.db` へ写している
  （`TAYORI_DB_LOCAL_CACHE`・既定で有効）。OOM kill は SIGKILL なので atexit も走らず、
  **直近30秒は永続側にも無い**。
- だから順番は必ず「①永続ディスクの `/var/data/tayori.db` が生きていないか」→「②R2 の控え」。
  ①のほうが新しい。①が壊れている時だけ②へ行く。

## 平時にやること（月に一度）

```bash
python scripts/restore_backup.py drill
```

鍵が要らない稽古。いまのDBから本番と同じ手順で控えを一本作り、詰めて・ほどいて・開いて・数える。
`戻せます。` が出れば、詰めて戻す経路は生きている。

本番の箱の中（Render のダッシュボードの Shell、または `render ssh`）なら、R2 側も検められる:

```bash
python scripts/restore_backup.py list
```

最新の一本が2日より古ければ警告が出る＝バックアップが止まっている。

## 戻す手順（Render）

**走っている本番で `/var/data/tayori.db` を手で差し替えても戻らない。**
実体は `/tmp/tayori-live.db` のほうで、30秒ごとにそれが上から書かれる——置いたものは次の30秒で消える。
戻す道は「置く → 旗を立てる → 一度立て直す」の三手だけ。
旗を見るのは `app.py` の `_restore_from_upload()` 一箇所で、**まだ誰もDBを開いていない起動直後**に働く。

### ① 落として、検めて、永続ディスクの上に置く

本番の箱の中で（R2 の4変数はそこに在る）:

```bash
python scripts/restore_backup.py fetch latest --to /var/data
```

`/tmp` ではなく **`/var/data`** に置くこと（`/tmp` は立て直すと消える）。
`integrity_check: ok` と、人・ことばの数が出る。**ここで数を見て、この控えでいいかを決める。**
一本古いのを見たい時は `list` で名前を見て `fetch backups/tayori-….db.gz --to /var/data`。

### ② 旗を立てる

Render のダッシュボード → Environment に、落ちてきたファイル名で:

```
TAYORI_RESTORE_FROM=/var/data/tayori-20260812-031500.db.gz
```

### ③ 立て直す（Manual Deploy / Restart）

起動ログにこれが出れば戻っている:

```
[たより] ★控えから戻します: /var/data/tayori-….db.gz
[たより] 控えの中身: 人=7 ことば=410
[たより] いまのDBを退けました → /var/data/tayori.db.before-restore-20260812-133346
[たより] ★戻しました。指し先は ….restored-20260812-133346 へ改名済み
```

控えが検めを通らなければ（`integrity_check` が ok でない／人が0）**何も置き換えずに**
いまのDBのまま起きる。その時は一本古い控えで①からやり直す。

### ④ 戻ったら、片づける

1. 宙が見えること・自分のことばが在ることを目で確かめる。
2. `TAYORI_RESTORE_FROM` を**消す**（指し先は改名済みなので二度は戻らないが、旗は残さない）。
3. ディスクは1GB。`/var/data` の `tayori.db.before-restore-*` と `*.restored-*` を消す
   （合わせて 340MB ほど。確かめる前に消さないこと）。
4. 失った窓（控えの「最後のことば」の時刻から落ちた時刻まで）を、お知らせに書くかどうかを決める。

## 手元でひらいて中身だけ見たい時

```bash
python scripts/restore_backup.py verify ~/Downloads/tayori-20260812-031500.db.gz --keep-db
```

`--keep-db` でほどいた `.db` が残る。`sqlite3` でも `python run.py` でも開ける。
このスクリプトは**検めるだけ**で、live DB には指一本触れない（`TAYORI_DB_PATH` と同じ道を指すと止まる）。

## 通したときの実測（2026-08-12・手元 / 201MB）

| | |
|---|---|
| 控えを作る（sqlite の backup API） | 0.6秒 |
| 詰める（1MBずつ流しながら） | 7.4秒 / 201MB → 128MB |
| ほどく | 0.7秒 |
| `integrity_check` | 2.0秒 |

稽古で通したのは4つ:
戻したDBでアプリが起きて `/` と `/about` が 200 を返すこと、
戻す前のDBが `.before-restore-*` に残ること、
旗を立てたまま二度目を起こしても**戻し直さない**こと、
壊れたファイルを指した時に**何も置き換えずに**起きること。

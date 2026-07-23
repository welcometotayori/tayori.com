# tayori — Mood Visualization 実装 Spec（SQLite 反映版 / v3.12）

**元 spec**: Claude Code 向けに書かれた「独立2機能 + 共通描画基盤」の設計書（Postgres 前提）。
**この版**: 実コード（**SQLite**）に合わせて翻案し、**2026-07-23 に実装した範囲と決定事項**を記録したもの。
以後の判断はこの版を基準にする。

> 名前は残す: A の呼称は **「気分の宙 / mood space」**（`templates/mood.html` の見出し）。
> B の呼称は **「気分の地図 / Mood Night Map」**（`/night`・設定・告知の文言）。

---

## 0. 元 spec との最大の差 ── DB は SQLite（Postgres ではない）

元 spec は `TIMESTAMPTZ` / `MATERIALIZED VIEW` / `ROUND(x::numeric,1)` / `_PGConn 互換レイヤー` を
前提にしていたが、**たよりの DB は純 SQLite**（`app.py` は `sqlite3` + `?` プレースホルダ、
`get_db()` / `_connect()`）。`_PGConn` は存在しない。したがって B は全面的に翻案した。

| 元 spec（Postgres） | この実装（SQLite） |
|---|---|
| `TIMESTAMPTZ` の `created_at` / `sealed_at` / `deliver_at` | TEXT の ISO 文字列 `sent_date` / `arrive_at` / `opened_at`（既存流儀） |
| `is_demo` | `demo_mode`（0/1） |
| `ALTER TABLE ... ADD COLUMN ... NOT NULL DEFAULT` | try/except の `ADD COLUMN`（`init_db` の移行リスト）。SQLite なので `NOT NULL` は付けず `DEFAULT 0` |
| `ROUND(area_lat::numeric,1)` で grid_id 生成 | **Python 側**で丸め（`_compute_grid_id`）。SQLite の `CAST(ROUND(x,1) AS TEXT)` は `"33.600000000000001"` の桁化けを起こすため使わない |
| `MATERIALIZED VIEW mood_grid` + `REFRESH CONCURRENTLY` | 普通の **テーブル** `mood_grid` を日次で作り直す（`_refresh_mood_grid`）。近傍量子化(`_mood_index`)が要るので集計も Python 側 |
| パレット 7 色 | **手染め 9 スウォッチ**（`_MOOD_SWATCH_HEX`。元 spec の「7色」は古い。mood.html は既に 9 色で正しい） |
| 「デモアカウントでダミー返却」 | **デモ*アカウント*の概念は無い**。`demo_mode=1` の*手紙*を除外するだけ。A も B もダミーは返さない（現状維持） |

---

## 1. 実装した範囲（2026-07-23）

元依頼の4点すべてに対応した。

### (A) 既存 A「気分の宙」の修正
- `/api/mood-space` の WHERE に `COALESCE(excluded_from_aggregate,0)=0` を追加。
  A・B 共通のオプトアウトを宙も尊重する（元 spec §2.1 の注に対応）。
- 描画・座標・文言はズレていなかったため変更なし（既に 9 色で spec より実態に忠実）。

### (B) Mood Night Map — Phase 1 + 1.5（今週ぶん）
1. **カラム追加**（`init_db` の移行リスト）
   - `letters.grid_id TEXT`
   - `letters.excluded_from_aggregate INTEGER DEFAULT 0`
   - `users.aggregate_opt_out INTEGER DEFAULT 0`（ユーザー単位の意思）
   - `users.night_map_notice_seen_at TEXT`（告知の既読）
2. **grid_id バックフィル** — `_backfill_grid_ids(db)`。既存レコードを Python で丸め、
   `_compute_grid_id` と完全一致させる（丸め式を1本化）。
3. **新規付与** — 手紙 INSERT（`/api/letters`）で `grid_id` を計算し、投函者の
   `aggregate_opt_out` を `excluded_from_aggregate` に写す（同一トランザクション）。
4. **オプトアウト API** — `GET/POST /api/settings/aggregate-opt-out`。ON で
   ユーザーの既存・今後すべての手紙を除外（既存は一括 UPDATE、今後は投函時に写す）。
5. **リリース告知（事後同意）** — `POST /api/settings/night-map-notice-seen`。
   `/api/me` が `night_map_notice`（未読か）と `aggregate_opt_out` を返す。
   フロント（index.html）: boot 時のみ一度きりの告知モーダル（文言は §B.1.5）と、
   設定内のオプトアウト・トグル。
6. **集計テーブル** — `mood_grid (grid_id,mood,n,latest,lat,lng)`。
   `_refresh_mood_grid(db)` が **しきい値 10** 未満のセルを落として作り直す。
   `maintenance_loop` に**日次**で相乗り（`last_mood_grid`、起動直後に一度）。
   即時反映しない（「観測されている」感を薄める意図）。

### スコープ外（元 spec 通り「後日」）
- **B Phase 2**: `GET /api/mood-map`（`mood_grid` を返す公開 API）
- **B Phase 3**: `static/js/mood_night_map.js`（Leaflet + canvas の夜間光描画）、`/night` ページ
- `demo_mood_cells.json`: Phase 2 のデモ用。Phase 2 に着手する時に作る（今は宙ぶらりんの
  ファイルを置かない判断）。
- 4色リブランド、既存 `/map` の調整、ホバー/クリック詳細、統計数値表示（元 spec §4 のまま）。

---

## B.1.5 設定・告知の文言（そのまま使う・実装済み）

> **気分の地図に含める**
> あなたの手紙の「気分の色」だけが、10通以上まとまった地域の光として地図に加わります。
> 手紙の内容・場所の詳細・書いた日時は使いません。

チェック = 含める（既定 ON）。外すと `opt_out=true`、反映は翌日以降。

---

## 2. 変わらない禁止事項（元 spec §0.2）

- レスポンス・集計に `user_id` / `letter_id` / 本文 / 件名 / 生座標を載せない。
  `/api/mood-space` も `mood_grid` も列を指で数えて SELECT する（`SELECT *` 禁止）。
- 個別の手紙へのリンク・ホバー・クリック詳細を作らない。**光は光のまま**。
- **しきい値 10 を下げない**（`MOOD_GRID_THRESHOLD`）。母数が小さいセルは色が個人を指す。
- ダミーで見た目を埋めない（デモを除く）。

---

## 3. 方針変更の記録（元 spec §0.1 の追認）

地図の設計憲法（生座標を共有ビューに出さない）を、B の導入で意図的に変更した。
**共有ビューに位置を出してよい。ただし 3 条件すべてを満たす場合に限る**:
1. 0.1 度グリッドへの丸め（約 11km 四方 = `grid_id`）
2. セルあたり 10 通以上でのみ表示（`HAVING/フィルタ COUNT >= 10`）
3. ユーザーが集計から抜けられる（`excluded_from_aggregate` / `aggregate_opt_out`）

既存データも含める（新規のみだと 10 通到達に数ヶ月かかり機能が死ぬため）。同意はリリース時の
告知 + オプトアウト導線で担保（事後同意）。この 3 条件のいずれかが崩れる変更を将来加える時は、
方針そのものを再検討する。

---

## 4. 調整予定のマジックナンバー（実データを見てから）

A（`templates/mood.html` の `TUNE`）は既存のまま。B は Phase 3 着手時に詰める。

| 変数 | 対象 | 初期値 | 備考 |
|---|---|---|---|
| `MOOD_GRID_THRESHOLD` | B | `10` | **下げない** |
| リフレッシュ間隔 | B | 86400s（日次） | `maintenance_loop` |
| `BASE_RADIUS` / `zoomFactor` / alpha 係数 / 彩度 1.4 / ノイズ 13 | B Phase3 | 未実装 | 元 spec §5 参照 |

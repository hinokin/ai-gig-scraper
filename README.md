# ai-gig-scraper

CrowdWorks と Lancers から、**自分の強みに合う案件だけ**を毎日自動収集してスプレッドシートに貯めるやつ。

## 何が変わったか（Claude 2nd版からの差分）

| 項目 | 旧 | 新 |
|---|---|---|
| コンペ除外 | ❌ なし | ✅ 除外 |
| 正社員求人の除外 | ❌ なし | ✅ 除外（業務委託は残す） |
| 対象キーワード | AI系のみ | AI/自動化/SNS/マーケ/システム開発/シナリオ/ディレクション 等に拡張 |
| 金額パース | 全数字のmax（閲覧数等を誤取得しがち） | 「◯円/◯万円」だけを抽出＋時間単価は別基準 |
| 収集の置き場 | CW=Actions / Lancers=GAS に分散 | **収集は全部Python(Actions)に集約**、GASは表示だけ |
| テスト | なし | フィルタ32件＋つなぎ5件の自動テスト |

**設計思想**: 「どの案件を残すか」の判断を `filters.py` に全部集約して単体テスト済みにした。壊れやすいスクレイピング（副作用）と、壊れちゃいけない判断ロジックを分離してある。

---

## 構成

```
【毎朝 08:00 JST】GitHub Actions
   └ scraper.py（Playwrightで CW & Lancers を巡回）
        └ filters.py で絞り込み → results.json をコミット

【毎朝 09:00 JST】GAS（表示係）
   └ results.json を fetch してスプレッドシートに追記＋色分け
```

```
ai-gig-scraper/
├─ .github/workflows/scrape.yml   … 毎日実行の設定
├─ scraper.py                     … 収集本体（両サイト巡回）
├─ filters.py                     … 絞り込みロジック（テスト済みの核）
├─ test_filters.py                … フィルタの単体テスト（32件）
├─ test_pipeline.py               … 抽出→整形の結合テスト（5件）
├─ gas_presenter.js               … GAS（results.jsonをシートに表示）
├─ requirements.txt
├─ results.json                   … 収集結果（初期はサンプル入り）
└─ seen_urls.json                 … 取得済みURL（重複防止）
```

---

## 絞り込み基準（`filters.py` 冒頭で変更可）

- **募集終了**: 除外
- **応募期日が3時間未満**（`MIN_HOURS_LEFT`）: 除外 — 「あと◯時間」「あと◯日」「本日締切」を締切テキストから時間換算し、実質応募できない案件を弾く
- **コンペ形式**: 除外
- **タスク形式**: 除外（`EXCLUDE_TASKS=False` で復活可）
- **正社員求人**: 除外（`業務委託`は残す）
- **強みキーワード**: AI/生成AI/ChatGPT/自動化/RPA/SNS/Instagram/マーケ/MEO/SEO/システム開発/Python/シナリオ/台本/ディレクション/PM 等のいずれかに一致
- **報酬**: 明らかに安すぎるものだけ除外（固定報酬1万円未満・時間単価1,000円未満）。母数を確保するため、ここでの足切りはあえて緩め

数字を変えたければ `filters.py` の上部（`MIN_FIXED_PRICE` / `MIN_HOURS_LEFT` など）と `STRENGTH_KEYWORDS` を触るだけ。

### 優先度（除外ではなく「目立たせる」ための分類）

上の基準を通った案件はすべて残したうえで、`優先度` 列を付けて一目で分かるようにしている:

| 優先度 | 条件 |
|---|---|
| 🔥⭐ 高単価×スキルアップ | 固定報酬15万円以上（or 時間単価3,000円以上）**かつ** AI/自動化/開発系キーワードに一致 |
| 🔥 高単価 | 固定報酬15万円以上（or 時間単価3,000円以上） |
| ⭐ スキルアップ | AI/自動化/開発系キーワードに一致 |
| 通常 | 上記以外（応募可能ではあるが特筆なし） |

`優先度`のしきい値は `filters.py` の `HIGH_PAY_FIXED` / `HIGH_PAY_HOURLY` / `SKILL_UP_KEYWORDS` で調整可能。新着はこの優先スコア順（高→低）で並ぶ。

### 募集終了の除外（3段構え）

「取得したデータが全部募集終了だった」問題への対策として、収集元URL・カード単位・判定ロジックの3層で除外する:

1. **収集元URLで除外**（`scraper.py` の `CW_TARGETS` / `LANCERS_TARGETS`）
   - CrowdWorks: `hide_expired=true` — サイトの「募集終了を隠す」チェックボックスと同じパラメータ。実測で該当数が **350,818件 → 8,408件** に激減することを確認済み。
     - ※ 当初案の `keep_open=1` は CrowdWorks に存在しないパラメータで、付けても無視されるだけなので不採用にした。
   - Lancers: `open=1` — 外すと「募集終了」バッジ付きカードが混入することを実測で確認済み。既存のままで機能している。
2. **カード単位でのバッジ物理検知**（`scraper.py` の `card_to_job` / `CLOSED_BADGE_SELECTORS`）
   - Lancers: 実サイトのDOMを調査し、カード内の `.p-search-job-media__time--end`（募集終了）/`--open`（募集中）というクラスを確認済み。バッジ要素が見つかった時点で `should_include` に回さず即除外する。
   - CrowdWorks: 一覧カードに恒常的なバッジ用クラス名が見当たらなかったため、後述のテキストマーカー検知のみで対応。
3. **テキストマーカー検知**（`filters.py` の `contains_closed_marker`）— 「募集終了」「受付終了」等の文言をカード全文から検知。①②をすり抜けた場合の最終防波堤。

### 既知の注意点：results.json が更新されないケース

`scraper.py` は「新着 + 既存」をURLで重複排除して書き出す方式のため、**その日にフィルタを1件も通過しなかった場合、`results.json` は前回のまま変化しない**。GitHub Actions実行時にこれが続くと、初期投入したサンプルデータ（架空のURL）がいつまでも残ってしまう。今回、`results.json` / `seen_urls.json` を空配列にリセット済みなので、次回Actions実行時にクリーンな状態から実データが入る。

---

## セットアップ

### 1. リポジトリに反映
既存の `hinokin/ai-gig-scraper` に、このフォルダの中身をそのまま置いて push（コマンドは会話側の手順を参照）。**Publicのままにする**（GASがJSONをfetchするため）。

### 2. Actionsを動かす
1. リポジトリの **Actions** タブ → ワークフローを有効化
2. **Run workflow** で手動実行
3. ログを確認。`results.json` が更新されていればOK
   - もし「抽出0件」で `debug_first_card.txt` が生成されていたら、**そのファイルの中身を貼って**くれれば5分でセレクタ直す（後述）

### 3. GASを設定
`getOrCreateSpreadsheet_()` は `SpreadsheetApp.getActiveSpreadsheet()` を返すだけなので、
**スプレッドシートに紐づくコンテナバインドスクリプト**として設定する（standalone の
script.google.com プロジェクトでは `getActiveSpreadsheet()` が `null` になり動かない）。

1. 書き込み先にしたいスプレッドシートを開く（新規でも既存でもOK）
2. そのシートのメニューから **拡張機能 → Apps Script**
3. `gas_presenter.js` の中身を貼り付け
4. `GITHUB_JSON_URL` を自分のRAW URLに:
   `https://raw.githubusercontent.com/hinokin/ai-gig-scraper/main/results.json`
5. `setupTrigger()` を1回実行（権限承認）
5. `testRun()` を実行 → ログのスプレッドシートURLを開く

### 色分け
優先度を最優先で適用し、`通常`の案件だけ締切の近さで色分けする。

- 🟡 金: 🔥⭐ 高単価×スキルアップ
- 🟨 薄黄: 🔥 高単価
- 🟢 緑: ⭐ スキルアップ
- 🔴 赤: （優先度が通常の案件のうち）残り3日以内
- 🟠 オレンジ: （同）残り4〜7日
- ⬜ グレー: （同）締切不明（報酬応相談など要確認案件を含む）

---

## つまずきポイント

**JSONが読み込まれない** → リポジトリがPublicか / URLが `results.json` を指しているか確認

**Actionsが落ちる** → Actionsログを確認。`permissions: contents: write` はymlに入れてある。Settings→Actions→Workflow permissions が "Read repository contents" だけになっていたら "Read and write" に変更

**新着が0のまま** → `seen_urls.json` を `[]` にして再実行すると全件取り直し

**抽出0件 / `debug_first_card.txt` が出た** → 両サイトはHTML構造を時々変える。`scraper.py` の `CARD_SELECTORS`・`LINK_SELECTORS`・報酬セレクタを、ダンプされた実HTMLに合わせて直せばよい（判断ロジック側は無傷）

---

## 正直な前提（重要）

- **利用規約**: CrowdWorks・Lancersとも規約で自動アクセスを制限している。このツールは「ログインせず・1日1回・間隔を空けて・普通のUA」で個人の案件探し用途に留めている（アカウントに紐づかない匿名アクセス）。頻度を上げたりログイン状態で回すのはやめとくのが安全。ここは自己判断の領域。
- **セレクタは実運用で要確定**: 一覧ページはJSレンダリングなので、初回のActions実行で実際に取れるか必ず確認すること。取れなければ `debug_first_card.txt` を見て直す設計にしてある。
- 収集ロジック（金額・締切・コンペ/正社員判定・キーワード）はテスト済み。壊れるとしたらセレクタ側なので、そこだけ直せば復活する。

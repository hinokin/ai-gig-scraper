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

- **固定報酬制**: 上限が **5万円以上**
- **時間単価制**: **2,000円/時 以上**（継続案件を拾うため別枠で通す）
- **コンペ形式**: 除外
- **タスク形式**: 除外（`EXCLUDE_TASKS=False` で復活可）
- **正社員求人**: 除外（`業務委託`は残す）
- **募集終了**: 除外
- **強みキーワード**: AI/生成AI/ChatGPT/自動化/RPA/SNS/Instagram/マーケ/MEO/SEO/システム開発/Python/シナリオ/台本/ディレクション/PM 等のいずれかに一致

数字を変えたければ `filters.py` の上部（`MIN_FIXED_PRICE` など）と `STRENGTH_KEYWORDS` を触るだけ。

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
1. https://script.google.com/ で新規プロジェクト
2. `gas_presenter.js` を貼り付け
3. `GITHUB_JSON_URL` を自分のRAW URLに:
   `https://raw.githubusercontent.com/hinokin/ai-gig-scraper/main/results.json`
4. `setupTrigger()` を1回実行（権限承認）
5. `testRun()` を実行 → ログのスプレッドシートURLを開く

### 色分け
- 🔴 赤: 残り3日以内
- 🟠 オレンジ: 残り4〜7日
- ⬜ グレー: 締切不明（報酬応相談など要確認案件を含む）

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

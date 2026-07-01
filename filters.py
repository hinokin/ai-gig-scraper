"""
filters.py — 案件フィルタリングの純粋ロジック（副作用なし・テスト可能）

このモジュールには「どの案件を残すか」の判断ロジックだけを置く。
スクレイピング（副作用）は scraper.py 側に隔離してあるので、
ここは test_filters.py で単体テストできる。

判定基準（Hinokinの要件）:
  1. 締切がまだ有効＆応募に間に合う状態（募集終了・締切間近を除外）
  2. コンペ形式を除外
  3. 正社員前提の求人を除外
  4. 強み（AI業務/SNS/マーケ/システム開発/生成AI/シナリオ/ディレクション等）に合致
  5. 報酬は極端に低いものだけ除外し、高単価・スキルアップ案件は「優先度」で目立たせる
     （母数を確保するため、価格での足切りは緩め。優先度判定は compute_priority が担う）
"""

import re

# ============================================================
# 設定値（ここだけ触れば挙動を変えられる）
# ============================================================

MIN_FIXED_PRICE = 10_000      # 固定報酬制の最低ライン（円）— 明らかな安すぎ案件だけ除外
MIN_HOURLY_RATE = 1_000       # 時間単価制の最低ライン（円/時）— 同上
EXCLUDE_TASKS = True          # タスク形式（軽作業）を除外するか。要件のコンペ除外に準じた既定
KEEP_UNKNOWN_PRICE = True     # 報酬が読めない案件を「要確認」として残すか（Falseで切り捨て）

MIN_HOURS_LEFT = 3            # 締切までこの時間未満なら「応募間に合わない」として除外

# 優先度判定（除外ではなく「目立たせる」ための基準）
HIGH_PAY_FIXED = 150_000      # 固定報酬でこれ以上なら「高単価」
HIGH_PAY_HOURLY = 3_000       # 時間単価でこれ以上なら「高単価」

# スキルアップ判定用キーワード（AI・自動化・開発など、経験値が積める領域）
SKILL_UP_KEYWORDS = [
    "AI", "生成AI", "ChatGPT", "GPT", "Claude", "Gemini", "Copilot",
    "LLM", "機械学習", "ディープラーニング", "RAG", "エージェント",
    "プロンプト", "Dify", "n8n",
    "自動化", "業務効率化", "効率化", "RPA", "API連携",
    "システム開発", "Python", "Webアプリ", "アプリ開発", "開発",
    "プログラミング", "ツール開発", "自動生成", "ChatBot", "チャットボット",
]

# 強みキーワード（OR判定・大小文字無視）。増やしたければここに足す。
STRENGTH_KEYWORDS = [
    # --- AI / 生成AI ---
    "AI", "生成AI", "ChatGPT", "GPT", "Claude", "Gemini", "Copilot",
    "LLM", "機械学習", "ディープラーニング", "RAG", "エージェント",
    "プロンプト", "Dify", "n8n", "Dify",
    # --- 自動化 / 業務効率化 ---
    "自動化", "業務効率化", "効率化", "RPA", "GAS", "スクレイピング",
    "API連携", "ワークフロー", "Zapier", "Make", "Notion",
    # --- SNS ---
    "SNS", "Instagram", "インスタ", "TikTok", "ティックトック",
    "YouTube", "ユーチューブ", "X（Twitter）", "Twitter", "運用代行", "投稿作成",
    # --- マーケティング / 集客 ---
    "マーケティング", "マーケ", "MEO", "SEO", "集客", "広告運用",
    "LP", "ランディングページ", "リスティング", "コンテンツマーケ",
    # --- システム開発 ---
    "システム開発", "Python", "Webアプリ", "アプリ開発", "開発",
    "プログラミング", "スクリプト", "ツール開発", "自動生成", "ChatBot",
    "チャットボット", "業務システム",
    # --- シナリオ / コンテンツ ---
    "シナリオ", "脚本", "台本", "構成案", "動画制作", "生成AI動画",
    "コンテンツ制作", "ナレーション原稿",
    # --- ディレクション / PM ---
    "ディレクション", "ディレクター", "PM", "プロジェクトマネ",
    "進行管理", "プロデュース", "コンサル",
]

# 募集終了・受付終了を示す語（これがあれば締切切れ扱い）
CLOSED_MARKERS = [
    "募集終了", "受付終了", "募集を終了", "この募集は", "掲載を終了",
    "募集は締め切", "応募を締め切", "選考は終了",
]

# 正社員前提の求人を示す語（あれば除外）。※「業務委託」は除外しない
EMPLOYMENT_MARKERS = [
    "正社員", "契約社員", "アルバイト", "パート募集", "月給", "年俸",
    "賞与", "社会保険完備", "雇用形態", "入社日", "試用期間",
    "正社員登用", "福利厚生", "みなし残業",
]


# ============================================================
# 報酬パース
# ============================================================

def extract_yen_values(text: str) -> list:
    """
    テキストから「円」で終わる金額だけを抽出する。
    「5万円」「50,000円」両対応。閲覧数・残り日数・実績件数などの
    無関係な数字は拾わない（円/万が直後にある数字のみ対象）。
    """
    if not text:
        return []
    values = []
    # 万円 / 万 表記（例: 5万円, 10万, 1.5万円）
    for m in re.findall(r"([\d,]+(?:\.\d+)?)\s*万", text):
        try:
            values.append(int(float(m.replace(",", "")) * 10_000))
        except ValueError:
            pass
    # プレーンな 円 表記（例: 50,000円）。直前が万のケースは上で処理済みで二重取りしない
    for m in re.findall(r"([\d,]+)\s*円", text):
        try:
            v = int(m.replace(",", ""))
            if v > 0:
                values.append(v)
        except ValueError:
            pass
    return values


def detect_reward_type(reward_text: str, job_type_label: str = "", body_text: str = "") -> str:
    """
    報酬形態を判定: 'competition' | 'task' | 'hourly' | 'fixed' | 'unknown'
    ラベル・報酬欄・本文の順で手掛かりを見る。
    """
    hay = " ".join([job_type_label or "", reward_text or "", body_text or ""])
    if "コンペ" in hay:
        return "competition"
    if "タスク" in hay or "作業単価" in hay or "件単価" in hay:
        return "task"
    if any(k in hay for k in ["時間単価", "時給", "/時", "／時", "/ 時", "時間あたり"]):
        return "hourly"
    if "固定報酬" in hay:
        return "fixed"
    # ラベルが無くても金額があれば固定報酬とみなす
    if extract_yen_values(reward_text):
        return "fixed"
    return "unknown"


def price_bounds(reward_text: str):
    """(min_yen, max_yen) を返す。読めなければ (None, None)。"""
    vals = extract_yen_values(reward_text)
    if not vals:
        return (None, None)
    return (min(vals), max(vals))


# ============================================================
# 個別判定
# ============================================================

def contains_closed_marker(text: str) -> bool:
    """
    テキストに募集終了系マーカーが含まれるか。
    scraper.py 側でカード抽出直後（should_include に渡す前）に、
    バッジ/カード全文へ直接かけて即除外するための単発チェック用。
    """
    return any(m in (text or "") for m in CLOSED_MARKERS)


def is_open(deadline_text: str, body_text: str) -> bool:
    """募集中かどうか。明示的な終了マーカーがあれば False。"""
    blob = " ".join([deadline_text or "", body_text or ""])
    return not contains_closed_marker(blob)


def days_left(deadline_text: str):
    """締切までの残り日数を int で返す。取れなければ None。"""
    if not deadline_text:
        return None
    if "本日締切" in deadline_text or "まもなく終了" in deadline_text:
        return 0
    m = re.search(r"(?:あと|残り)\s*(\d+)\s*日", deadline_text)
    if m:
        return int(m.group(1))
    return None


def hours_left(deadline_text: str):
    """
    締切までの残り時間を float（時間）で返す。取れなければ None。
    「あと3時間」「残り5時間」→ そのまま。
    「あと2日」「残り5日」→ 日数 × 24。
    「本日締切」「まもなく終了」→ 0
      （当日中で正確な残り時間が読めないため、応募に間に合わない前提で安全側に倒す）。
    """
    if not deadline_text:
        return None
    if "本日締切" in deadline_text or "まもなく終了" in deadline_text:
        return 0.0
    m = re.search(r"(?:あと|残り)\s*(\d+)\s*時間", deadline_text)
    if m:
        return float(m.group(1))
    d = days_left(deadline_text)
    if d is not None:
        return float(d) * 24
    return None


def is_employment_post(title: str, body_text: str) -> bool:
    """正社員前提の求人なら True（＝除外対象）。業務委託は除外しない。"""
    blob = " ".join([title or "", body_text or ""])
    if "業務委託" in blob and not any(
        k in blob for k in ["正社員", "契約社員", "アルバイト", "パート募集"]
    ):
        return False
    return any(m in blob for m in EMPLOYMENT_MARKERS)


def matched_keywords(title: str, body_text: str, keywords=None) -> list:
    """ヒットした強みキーワードの一覧（重複除去・元の表記で返す）。"""
    if keywords is None:
        keywords = STRENGTH_KEYWORDS
    blob = (str(title) + " " + str(body_text)).lower()
    hits = []
    for kw in keywords:
        if kw.lower() in blob and kw not in hits:
            hits.append(kw)
    return hits


def price_ok(reward_type: str, min_yen, max_yen) -> bool:
    """報酬形態に応じた金額基準を満たすか。"""
    if reward_type == "fixed":
        if max_yen is None:
            return KEEP_UNKNOWN_PRICE
        return max_yen >= MIN_FIXED_PRICE
    if reward_type == "hourly":
        if max_yen is None:
            return KEEP_UNKNOWN_PRICE
        return max_yen >= MIN_HOURLY_RATE
    if reward_type == "unknown":
        return KEEP_UNKNOWN_PRICE
    # competition / task はここに来ないはず（should_include 側で先に弾く）
    return False


# ============================================================
# 総合判定
# ============================================================

def should_include(job: dict):
    """
    案件 dict を受け取り (残すか: bool, 理由: str) を返す。
    理由は除外時のデバッグログ用。
    期待キー: title, url, reward_text, deadline_text, body_text, job_type_label
    """
    title = job.get("title", "")
    reward_text = job.get("reward_text", "")
    deadline_text = job.get("deadline_text", "")
    body_text = job.get("body_text", "")
    label = job.get("job_type_label", "")

    # 1. 募集中か（明示的な終了マーカー）
    if not is_open(deadline_text, body_text):
        return (False, "募集終了")

    # 1.5 応募期日まで十分な時間が残っているか（数時間未満は除外）
    hl = hours_left(deadline_text)
    if hl is not None and hl < MIN_HOURS_LEFT:
        return (False, f"応募期日間近(残り{hl:g}時間)")

    # 2. 正社員求人でないか
    if is_employment_post(title, body_text):
        return (False, "正社員求人")

    # 3. 強みに合致するか
    hits = matched_keywords(title, body_text)
    if not hits:
        return (False, "キーワード不一致")

    # 4. 報酬形態
    rtype = detect_reward_type(reward_text, label, body_text)
    if rtype == "competition":
        return (False, "コンペ形式")
    if rtype == "task" and EXCLUDE_TASKS:
        return (False, "タスク形式")

    # 5. 金額基準（明らかに安すぎるものだけ除外。母数確保のため足切りは緩め）
    lo, hi = price_bounds(reward_text)
    if not price_ok(rtype, lo, hi):
        return (False, f"金額不足({rtype}:{hi})")

    return (True, "OK")


# ============================================================
# 優先度判定（除外はしない。目立たせるための分類）
# ============================================================

def is_high_pay(rtype: str, hi) -> bool:
    """高単価案件か（優先度判定用。除外基準の MIN_* とは別枠）。"""
    if hi is None:
        return False
    if rtype == "fixed":
        return hi >= HIGH_PAY_FIXED
    if rtype == "hourly":
        return hi >= HIGH_PAY_HOURLY
    return False


def is_skill_up(title: str, body_text: str) -> bool:
    """AI・自動化・開発系など、経験値が積める案件か。"""
    return len(matched_keywords(title, body_text, SKILL_UP_KEYWORDS)) > 0


def compute_priority(rtype: str, hi, title: str, body_text: str):
    """
    (優先度ラベル, 優先スコア) を返す。スコアが高いほど優先表示。
    高単価 かつ スキルアップ につながる案件を最優先で目立たせる。
    """
    high_pay = is_high_pay(rtype, hi)
    skill_up = is_skill_up(title, body_text)
    if high_pay and skill_up:
        return ("🔥⭐高単価×スキルアップ", 3)
    if high_pay:
        return ("🔥高単価", 2)
    if skill_up:
        return ("⭐スキルアップ", 1)
    return ("通常", 0)


def build_record(job: dict) -> dict:
    """
    残すと決まった案件を、出力用の整形済み dict に変換する。
    GAS 側はこれをそのままシートに書くだけでよい。
    """
    title = job.get("title", "")
    reward_text = job.get("reward_text", "")
    deadline_text = job.get("deadline_text", "")
    body_text = job.get("body_text", "")
    label = job.get("job_type_label", "")

    rtype = detect_reward_type(reward_text, label, body_text)
    lo, hi = price_bounds(reward_text)
    dl = days_left(deadline_text)

    type_ja = {
        "fixed": "固定報酬", "hourly": "時間単価",
        "task": "タスク", "competition": "コンペ", "unknown": "要確認",
    }.get(rtype, "要確認")

    if lo is None:
        reward_disp = reward_text.strip() or "報酬要確認"
    elif lo == hi:
        reward_disp = f"{hi:,}円"
    else:
        reward_disp = f"{lo:,}〜{hi:,}円"

    priority_label, priority_score = compute_priority(rtype, hi, title, body_text)

    return {
        "取得日": job.get("fetched_at", ""),
        "ソース": job.get("source", ""),
        "案件名": str(title)[:120],
        "優先度": priority_label,
        "報酬種別": type_ja,
        "報酬": reward_disp,
        "締切": deadline_text or "",
        "残日数": "" if dl is None else dl,
        "URL": job.get("url", ""),
        "ヒット語": " / ".join(matched_keywords(title, body_text)[:6]),
        "優先スコア": priority_score,
    }

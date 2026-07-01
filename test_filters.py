"""
test_filters.py — filters.py の単体テスト
実行: python -m pytest test_filters.py -v   （pytest不要なら python test_filters.py）
"""

import filters as F


# ------------------------------------------------------------
# 金額パース
# ------------------------------------------------------------
def test_extract_yen_plain():
    assert F.extract_yen_values("固定報酬制 50,000円 〜 100,000円") == [50000, 100000]

def test_extract_yen_man():
    # 「5万円〜10万円」
    assert F.extract_yen_values("報酬 5万円 〜 10万円") == [50000, 100000]

def test_extract_yen_ignores_noise():
    # 閲覧数218回・残り5日・実績100件 などは拾わない
    vals = F.extract_yen_values("閲覧218回 あと5日 実績100件 報酬 80,000円")
    assert vals == [80000]

def test_extract_yen_empty():
    assert F.extract_yen_values("報酬応相談") == []


# ------------------------------------------------------------
# 報酬形態判定
# ------------------------------------------------------------
def test_type_competition():
    assert F.detect_reward_type("コンペ", "コンペ", "ロゴ募集") == "competition"

def test_type_task():
    assert F.detect_reward_type("タスク 作業単価 30円", "タスク", "") == "task"

def test_type_hourly():
    assert F.detect_reward_type("時間単価制 2,500円", "", "") == "hourly"

def test_type_fixed_by_label():
    assert F.detect_reward_type("固定報酬制 80,000円", "", "") == "fixed"

def test_type_fixed_by_amount_only():
    assert F.detect_reward_type("100,000円", "", "") == "fixed"

def test_type_unknown():
    assert F.detect_reward_type("報酬応相談", "", "") == "unknown"


# ------------------------------------------------------------
# 締切・残日数
# ------------------------------------------------------------
def test_days_left_ato():
    assert F.days_left("あと5日") == 5

def test_days_left_today():
    assert F.days_left("本日締切") == 0

def test_days_left_none():
    assert F.days_left("") is None

def test_is_open_true():
    assert F.is_open("あと3日", "AIツール開発の募集です") is True

def test_is_open_false():
    assert F.is_open("", "この募集は2024年に終了しました") is False


# ------------------------------------------------------------
# 正社員求人除外
# ------------------------------------------------------------
def test_employment_seishain():
    assert F.is_employment_post("Webエンジニア募集", "正社員／月給30万円／賞与あり") is True

def test_employment_gyoumu_itaku_ok():
    # 業務委託はOK（除外しない）
    assert F.is_employment_post("AI開発", "業務委託でお願いします。継続希望") is False

def test_employment_gyoumu_itaku_but_seishain():
    # 業務委託と書いてても正社員募集が混在すれば除外
    assert F.is_employment_post("開発", "まずは業務委託、その後正社員登用あり") is True


# ------------------------------------------------------------
# キーワード一致
# ------------------------------------------------------------
def test_keywords_hit():
    hits = F.matched_keywords("生成AIを使った業務自動化", "ChatGPTでの効率化")
    assert "生成AI" in hits and "自動化" in hits

def test_keywords_miss():
    assert F.matched_keywords("庭の草むしり代行", "土日に草をむしる作業") == []


# ------------------------------------------------------------
# 総合判定 should_include
# ------------------------------------------------------------
def _job(**kw):
    base = dict(title="", url="https://x/1", reward_text="",
                deadline_text="", body_text="", job_type_label="")
    base.update(kw)
    return base

def test_include_good_fixed_ai():
    ok, why = F.should_include(_job(
        title="生成AIチャットボット開発",
        reward_text="固定報酬制 100,000円 〜 300,000円",
        deadline_text="あと6日",
        body_text="ChatGPT APIを使った社内向けボット。継続あり。",
    ))
    assert ok is True, why

def test_exclude_low_price():
    ok, why = F.should_include(_job(
        title="AI記事のリライト",
        reward_text="固定報酬制 3,000円 〜 5,000円",
        deadline_text="あと6日",
        body_text="ChatGPTで記事を書き直す",
    ))
    assert ok is False and "金額" in why

def test_exclude_competition():
    ok, why = F.should_include(_job(
        title="AIサービスのロゴコンペ",
        reward_text="コンペ 50,000円",
        deadline_text="あと10日",
        body_text="生成AIサービスのロゴ募集",
        job_type_label="コンペ",
    ))
    assert ok is False and why == "コンペ形式"

def test_exclude_employment():
    ok, why = F.should_include(_job(
        title="マーケティング担当",
        reward_text="月給 300,000円",
        deadline_text="あと20日",
        body_text="正社員／SNSマーケ担当／社会保険完備",
    ))
    assert ok is False and why == "正社員求人"

def test_exclude_closed():
    ok, why = F.should_include(_job(
        title="AI自動化ツール開発",
        reward_text="固定報酬制 200,000円",
        deadline_text="",
        body_text="この募集は締め切りました",
    ))
    assert ok is False and why == "募集終了"

def test_exclude_keyword_miss():
    ok, why = F.should_include(_job(
        title="お弁当の盛り付けスタッフ",
        reward_text="固定報酬制 80,000円",
        deadline_text="あと5日",
        body_text="毎日お弁当を詰める作業です",
    ))
    assert ok is False and why == "キーワード不一致"

def test_include_hourly_good():
    ok, why = F.should_include(_job(
        title="SNS運用ディレクション",
        reward_text="時間単価制 3,000円 〜 4,000円",
        deadline_text="あと7日",
        body_text="Instagram運用の進行管理をお願いします",
    ))
    assert ok is True, why

def test_exclude_hourly_junk():
    ok, why = F.should_include(_job(
        title="SNS投稿作成",
        reward_text="時間単価制 800円",
        deadline_text="あと7日",
        body_text="Instagramの投稿を作る簡単な作業",
    ))
    assert ok is False and "金額" in why

def test_include_man_yen_format():
    ok, why = F.should_include(_job(
        title="マーケ自動化のシステム開発",
        reward_text="固定報酬制 10万円 〜 30万円",
        deadline_text="あと14日",
        body_text="Pythonで集客の自動化ツールを作る",
    ))
    assert ok is True, why

def test_include_unknown_price_kept():
    ok, why = F.should_include(_job(
        title="AIエージェント開発のディレクション",
        reward_text="報酬応相談",
        deadline_text="あと9日",
        body_text="LLMエージェントのPM。予算は応相談。",
    ))
    assert ok is True, why  # KEEP_UNKNOWN_PRICE=True のため残る

def test_exclude_task():
    ok, why = F.should_include(_job(
        title="AIツールの動作チェック（タスク）",
        reward_text="タスク 作業単価 50円",
        deadline_text="あと3日",
        body_text="ChatGPTの回答を1件ずつ確認する軽作業",
        job_type_label="タスク",
    ))
    assert ok is False and why == "タスク形式"


# ------------------------------------------------------------
# build_record 整形
# ------------------------------------------------------------
def test_build_record_fields():
    rec = F.build_record(_job(
        title="生成AI動画のシナリオ作成＆ディレクション",
        reward_text="固定報酬制 80,000円 〜 150,000円",
        deadline_text="あと2日",
        body_text="生成AI動画の構成・台本・進行管理",
        source="crowdworks",
        fetched_at="2026-07-01",
    ))
    assert rec["報酬種別"] == "固定報酬"
    assert rec["報酬"] == "80,000〜150,000円"
    assert rec["残日数"] == 2
    assert rec["ソース"] == "crowdworks"
    assert "シナリオ" in rec["ヒット語"] or "ディレクション" in rec["ヒット語"]


if __name__ == "__main__":
    # pytestが無くても走る簡易ランナー
    import inspect, sys
    fns = [(n, f) for n, f in globals().items()
           if n.startswith("test_") and inspect.isfunction(f)]
    passed = failed = 0
    for name, fn in fns:
        try:
            fn()
            passed += 1
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}  -> {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}  -> {type(e).__name__}: {e}")
    print(f"\n==== {passed} passed, {failed} failed / {len(fns)} total ====")
    sys.exit(1 if failed else 0)

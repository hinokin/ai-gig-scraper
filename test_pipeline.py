"""
test_pipeline.py — Playwrightのカードを模したモックで、
card_to_job → should_include → build_record → 重複排除 の一連を検証する。
（実ブラウザは使わず、抽出〜出力の“つなぎ”が正しいかを確認する）
"""

import asyncio
import json
import scraper as S
import filters as F


class FakeElement:
    """query_selector で返すリンク/報酬要素のモック。"""
    def __init__(self, text="", href=None):
        self._text = text
        self._href = href
    async def inner_text(self):
        return self._text
    async def get_attribute(self, name):
        return self._href if name == "href" else None
    async def evaluate(self, _js):
        return "<li>mock</li>"


class FakeCard:
    """カード要素のモック。innerText と、セレクタ→子要素のマップを持つ。"""
    def __init__(self, inner_text, link_href=None, link_text=None, reward_text=None):
        self._text = inner_text
        self._link = FakeElement(text=link_text or "", href=link_href) if link_href else None
        self._reward = FakeElement(text=reward_text) if reward_text else None
    async def inner_text(self):
        return self._text
    async def query_selector(self, sel):
        if "href" in sel:  # LINK_SELECTORS は a[href*=...]
            return self._link
        # 報酬セレクタ群
        if any(k in sel for k in ["payment", "reward", "price"]):
            return self._reward
        return None
    async def evaluate(self, _js):
        return "<li>mock outerHTML</li>"


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_card_to_job_basic():
    card = FakeCard(
        inner_text="固定報酬制 100,000円 〜 200,000円\nあと5日\n生成AIチャットボット開発",
        link_href="/public/jobs/12345",
        link_text="生成AIチャットボット開発",
        reward_text="固定報酬制 100,000円 〜 200,000円",
    )
    job = run(S.card_to_job(None, card, "crowdworks", "2026-07-01", set()))
    assert job is not None
    assert job["url"] == "https://crowdworks.jp/public/jobs/12345"
    assert job["title"] == "生成AIチャットボット開発"
    ok, why = F.should_include(job)
    assert ok, why
    rec = F.build_record(job)
    assert rec["報酬"] == "100,000〜200,000円"
    assert rec["残日数"] == 5


def test_card_no_link_skipped():
    card = FakeCard(inner_text="何かのテキスト", link_href=None)
    job = run(S.card_to_job(None, card, "crowdworks", "2026-07-01", set()))
    assert job is None


def test_card_dedup_by_seen():
    seen = {"https://crowdworks.jp/public/jobs/999"}
    card = FakeCard(
        inner_text="固定報酬制 80,000円 AI開発 あと3日",
        link_href="/public/jobs/999?ref=abc",
        link_text="AI開発",
    )
    job = run(S.card_to_job(None, card, "crowdworks", "2026-07-01", seen))
    assert job is None  # クエリを除いた正規化URLが seen に一致


def test_lancers_url_normalization():
    card = FakeCard(
        inner_text="時間単価制 3,000円 SNS運用ディレクション あと7日",
        link_href="https://www.lancers.jp/work/detail/55555?utm=x",
        link_text="SNS運用ディレクション",
    )
    job = run(S.card_to_job(None, card, "lancers", "2026-07-01", set()))
    assert job["url"] == "https://www.lancers.jp/work/detail/55555"
    ok, why = F.should_include(job)
    assert ok, why  # 時間単価3000円 → 通過


def test_full_merge_dedup():
    # 既存1件 + 新着2件（うち1件は既存とURL重複）→ 最終2件、新着が先頭
    existing = [{"URL": "https://crowdworks.jp/public/jobs/1", "案件名": "旧AI案件"}]
    new_records = [
        {"URL": "https://crowdworks.jp/public/jobs/2", "案件名": "新SNS案件"},
        {"URL": "https://crowdworks.jp/public/jobs/1", "案件名": "旧AI案件(重複)"},
    ]
    merged = new_records + existing
    seen_in_merge, deduped = set(), []
    for item in merged:
        u = item.get("URL")
        if u and u not in seen_in_merge:
            deduped.append(item)
            seen_in_merge.add(u)
    assert len(deduped) == 2
    assert deduped[0]["案件名"] == "新SNS案件"  # 新着が先頭
    # JSONシリアライズ可能か
    json.dumps(deduped, ensure_ascii=False)


if __name__ == "__main__":
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

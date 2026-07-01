"""
scraper.py — クラウドワークス & ランサーズ 案件収集（GitHub Actions上で毎日実行）

方針:
  - 一覧ページは両サイトともJSレンダリング前提なので Playwright で取得
  - 「どの案件を残すか」の判断は filters.py に集約（単体テスト済み）
  - セレクタが将来変わっても壊れにくいよう、複数セレクタを順に試行
  - 抽出0件のときは最初のカードのHTMLを debug_first_card.txt に保存
    （構造が変わっていたらこれを見てセレクタを直せる）

出力:
  - results.json      … 整形済みの案件一覧（新着が先頭）
  - seen_urls.json    … 取得済みURL（重複防止）
  - debug_first_card.txt … 抽出0件時のみ生成されるデバッグ情報
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

import filters as F

# ============================================================
# 収集対象URL
# ============================================================
# CrowdWorks: 新着順の全件ページを起点にして filters で絞る方式。
#   カテゴリのslug指定に依存しないぶん壊れにくい（毎日回すので新着数ページで十分）。
#   hide_expired=true … 実サイトの「募集終了を隠す」チェックボックスと同じパラメータ。
#   実測で確認済み: 該当数が 350,818件 → 8,408件 に激減する（＝募集終了を確実に除外）。
#   ※ 当初案の keep_open=1 は CrowdWorks に存在しないパラメータで、
#      付けても何も起こらない（サーバ側で無視される）ため不採用。
CW_TARGETS = [
    "https://crowdworks.jp/public/jobs?hide_expired=true&order=new",
]
# Lancers: 募集中(open=1)の一覧を新着順で。こちらも filters で絞る。
#   open=1 は実測で動作確認済み（外すと「募集終了」バッジ付きカードが混入することを確認）。
LANCERS_TARGETS = [
    "https://www.lancers.jp/work/search?open=1&sort=started",
]

MAX_PAGES = 12         # 1ターゲットあたり最大ページ数（母数確保のため広めに巡回）
CARD_WAIT_MS = 12_000  # カード描画待ちのタイムアウト
PER_PAGE_SLEEP = 1.2   # ページ間の待機（相手サーバへの礼儀＆bot検知回避）

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

OUT_FILE = Path("results.json")
SEEN_FILE = Path("seen_urls.json")
DEBUG_FILE = Path("debug_first_card.txt")

# サイトごとのカード/リンク セレクタ候補（上から順に試す）
# lancers の .p-search-job-media は実サイトのDOMを直接確認して追加した実クラス名
# （2026-07時点。CSS Modulesでハッシュ化されているcrowdworks側は安定クラス名が
#  取れないため、汎用セレクタ＋リンク有無フィルタでのフォールバックに頼る設計のまま）。
CARD_SELECTORS = {
    "crowdworks": ["li.job_offering", ".c-job-list__item",
                   "[data-testid='job-list-item']", "article", "li"],
    "lancers":    [".p-search-job-media", ".p-search-job__item", ".c-media",
                   "[data-qa='job-item']", "article", "li"],
}
LINK_SELECTORS = {
    "crowdworks": "a[href*='/public/jobs/']",
    "lancers":    "a[href*='/work/detail/']",
}
BASE_URL = {
    "crowdworks": "https://crowdworks.jp",
    "lancers":    "https://www.lancers.jp",
}
DETAIL_HINT = {
    "crowdworks": "/public/jobs/",
    "lancers":    "/work/detail/",
}

# 募集終了バッジのセレクタ（実サイトのDOMで確認済み）。
# lancers: 実際に .p-search-job-media__time--end / --open の2種類のクラスで
#   募集中/募集終了が切り替わることをDOM調査で確認済み（テキストだけに頼らない物理検知）。
# crowdworks: 一覧ページのカードに恒常的なバッジ用クラス名が確認できなかったため
#   空リスト＝テキストマーカー検知（contains_closed_marker）のみで対応。
CLOSED_BADGE_SELECTORS = {
    "crowdworks": [],
    "lancers": [".p-search-job-media__time--end"],
}


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


async def extract_cards(page, source: str):
    """ページ内のカード要素を取れたセレクタで返す。"""
    for sel in CARD_SELECTORS[source]:
        cards = await page.query_selector_all(sel)
        # 「li」など汎用セレクタで大量にヒットした場合も、後段でリンク有無で弾く
        if cards:
            return cards, sel
    return [], None


async def card_to_job(page, card, source: str, fetched_at: str, seen: set):
    """カード要素1つを正規化job dictに変換。詳細リンクが無ければ None。"""
    # 詳細リンク
    link = await card.query_selector(LINK_SELECTORS[source])
    if not link:
        return None
    href = (await link.get_attribute("href")) or ""
    if DETAIL_HINT[source] not in href:
        return None
    if href.startswith("/"):
        href = BASE_URL[source] + href
    href = href.split("?")[0]  # クエリ除去で正規化
    if href in seen:
        return None

    title = (await link.inner_text()).strip()
    card_text = (await card.inner_text()).strip()

    # 募集終了カードを should_include に回す前に物理的に除外する
    # （① CSSバッジで確定検知できるサイトはバッジ優先、② それ以外は全文のテキストマーカーで検知）
    for badge_sel in CLOSED_BADGE_SELECTORS.get(source, []):
        if await card.query_selector(badge_sel):
            return None
    if F.contains_closed_marker(card_text):
        return None

    if not title or len(title) < 4:
        # リンクテキストが空なら、カード内の見出しっぽい最初の行をタイトルに
        first_line = next((l.strip() for l in card_text.splitlines() if l.strip()), "")
        title = first_line[:120]
    if not title:
        return None

    # 報酬テキストは専用要素が取れれば優先、無ければカード全文にフォールバック
    reward_text = ""
    for rsel in [".payment", ".job_offering_payment", ".c-media__reward",
                 "[data-testid='reward']", ".p-search-job__price"]:
        el = await card.query_selector(rsel)
        if el:
            reward_text = (await el.inner_text()).strip()
            break
    if not reward_text:
        reward_text = card_text

    return {
        "source": source,
        "title": title,
        "url": href,
        "reward_text": reward_text,
        "deadline_text": card_text,     # 「あと◯日」抽出は days_left が担当（十分に限定的）
        "body_text": card_text,         # キーワード判定用
        "job_type_label": card_text,    # コンペ/タスク/時間単価の検知用
        "fetched_at": fetched_at,
    }


async def scrape_source(page, source: str, targets: list, seen: set, fetched_at: str):
    """1サイトを巡回して、フィルタ通過した整形済みレコードのリストを返す。"""
    records = []
    debug_saved = False

    for base in targets:
        print(f"\n===== [{source}] {base} =====")
        for pnum in range(1, MAX_PAGES + 1):
            sep = "&" if "?" in base else "?"
            url = base if pnum == 1 else f"{base}{sep}page={pnum}"
            print(f"  [{pnum}/{MAX_PAGES}] {url}")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            except Exception as e:
                print(f"    ページ取得エラー: {e}")
                break

            # カード描画待ち（失敗しても後続でトライ）
            try:
                await page.wait_for_selector(
                    ", ".join(CARD_SELECTORS[source][:3]) + f", {LINK_SELECTORS[source]}",
                    timeout=CARD_WAIT_MS,
                )
            except Exception:
                pass
            await asyncio.sleep(PER_PAGE_SLEEP)

            cards, used_sel = await extract_cards(page, source)
            print(f"    カード候補: {len(cards)}件 (selector={used_sel})")

            kept_here = 0
            examined = 0
            for card in cards:
                job = await card_to_job(page, card, source, fetched_at, seen)
                if not job:
                    continue
                examined += 1
                ok, why = F.should_include(job)
                if ok:
                    records.append(F.build_record(job))
                    seen.add(job["url"])
                    kept_here += 1
                    print(f"      OK  {job['title'][:42]}")
            print(f"    詳細リンク有: {examined}件 / 通過: {kept_here}件")

            # 抽出が全くできていない場合、最初のカードHTMLをダンプ（初回のみ）
            if examined == 0 and cards and not debug_saved and not DEBUG_FILE.exists():
                try:
                    html = await cards[0].evaluate("el => el.outerHTML")
                    txt = await cards[0].inner_text()
                    DEBUG_FILE.write_text(
                        f"# source={source} url={url}\n"
                        f"# selector={used_sel}\n\n"
                        f"---- innerText ----\n{txt}\n\n"
                        f"---- outerHTML ----\n{html}\n",
                        encoding="utf-8",
                    )
                    debug_saved = True
                    print(f"    ⚠ 抽出0件。デバッグ用に {DEBUG_FILE} を保存しました。")
                except Exception:
                    pass

            # 次ページが無さそうなら早期終了（カード0＝これ以上ない）
            if not cards:
                print("    カードなし → このターゲット終了")
                break
            await asyncio.sleep(0.6)

    return records


async def main():
    fetched_at = datetime.now().strftime("%Y-%m-%d")
    seen = set(load_json(SEEN_FILE, []))
    existing = load_json(OUT_FILE, [])
    print(f"既存URL数: {len(seen)} / 既存レコード数: {len(existing)}")

    all_new = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
        )
        page = await context.new_page()

        # トップに一度アクセスしてCookie等を確立
        for src, top in [("crowdworks", "https://crowdworks.jp/"),
                         ("lancers", "https://www.lancers.jp/")]:
            try:
                await page.goto(top, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(1.5)
            except Exception as e:
                print(f"[{src}] トップ接続で警告: {e}")

        try:
            all_new += await scrape_source(page, "crowdworks", CW_TARGETS, seen, fetched_at)
        except Exception as e:
            print(f"[crowdworks] 収集中に例外: {e}")
        try:
            all_new += await scrape_source(page, "lancers", LANCERS_TARGETS, seen, fetched_at)
        except Exception as e:
            print(f"[lancers] 収集中に例外: {e}")

        await browser.close()

    # 新着は優先スコアが高い順（高単価×スキルアップが目立つように）
    all_new.sort(key=lambda r: r.get("優先スコア", 0), reverse=True)

    # 新着を先頭に結合し、URLで重複排除
    merged = all_new + existing
    seen_in_merge = set()
    deduped = []
    for item in merged:
        u = item.get("URL")
        if u and u not in seen_in_merge:
            deduped.append(item)
            seen_in_merge.add(u)

    OUT_FILE.write_text(json.dumps(deduped, ensure_ascii=False, indent=2), encoding="utf-8")
    SEEN_FILE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✅ 完了: 新着 {len(all_new)}件 / 累計 {len(deduped)}件")
    print(f"   出力: {OUT_FILE}")
    if DEBUG_FILE.exists():
        print(f"   ⚠ {DEBUG_FILE} が生成されています（セレクタ要確認）")


if __name__ == "__main__":
    asyncio.run(main())

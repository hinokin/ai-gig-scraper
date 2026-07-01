/**
 * ============================================================
 * AI案件ダッシュボード（GAS = 表示係）
 * ============================================================
 * このGASは “スクレイピングしない”。GitHub Actions が作った
 * results.json を読み込んで、スプレッドシートに追記するだけ。
 * （収集・フィルタリングは全部 Python 側 = 一箇所に集約）
 *
 * 【セットアップ】
 *  1. https://script.google.com/ で新規プロジェクト作成
 *  2. このファイルの中身を貼り付け
 *  3. 下の GITHUB_JSON_URL を、自分のリポジトリの results.json のRAW URLに変更
 *     例: https://raw.githubusercontent.com/hinokin/ai-gig-scraper/main/results.json
 *  4. setupTrigger() を一度だけ実行（権限承認が出るので許可）
 *  5. testRun() を実行 → ログに出るスプレッドシートURLを開いて確認
 *
 *  ※ GitHub Actions が 8:00 JST にコミットするので、GASは 9:00 に回して
 *    “その日の最新JSON” を読む設定にしてある（setupTrigger内）。
 * ============================================================
 */

// ★ここだけ書き換える★
const GITHUB_JSON_URL =
  "https://raw.githubusercontent.com/hinokin/ai-gig-scraper/main/results.json";

const SHEET_NAME = "案件一覧";
// results.json のキー順に対応
const HEADERS = ["取得日", "ソース", "案件名", "優先度", "報酬種別", "報酬", "締切", "残日数", "URL", "ヒット語", "優先スコア"];

// 色設定（締切の近さ）
const COLOR_URGENT      = "#FFCDD2"; const COLOR_URGENT_TEXT  = "#B71C1C"; // 残3日以内
const COLOR_SOON        = "#FFF3E0"; const COLOR_SOON_TEXT    = "#E65100"; // 残4〜7日
const COLOR_UNKNOWN     = "#EEEEEE"; const COLOR_UNKNOWN_TEXT = "#757575"; // 残日数不明
const COLOR_NORMAL_TEXT = "#000000";

// 色設定（優先度）— 締切の色より優先して適用し、高単価・スキルアップ案件を一目で目立たせる
const COLOR_PRIORITY_TOP  = "#FFD54F"; // 🔥⭐ 高単価×スキルアップ（金色）
const COLOR_PRIORITY_HIGH = "#FFECB3"; // 🔥 高単価
const COLOR_PRIORITY_SKILL= "#C8E6C9"; // ⭐ スキルアップ

// ============================================================
// メイン（トリガーから毎日呼ばれる）
// ============================================================
function checkJobs() {
  const ss = getOrCreateSpreadsheet_();
  const sheet = getOrCreateSheet_(ss, SHEET_NAME);

  let jobs;
  try {
    const res = UrlFetchApp.fetch(GITHUB_JSON_URL, {
      muteHttpExceptions: true,
      headers: { "Cache-Control": "no-cache" },
    });
    if (res.getResponseCode() !== 200) {
      Logger.log("⚠️ JSON取得失敗 code=" + res.getResponseCode() +
                 " （URLとリポジトリがPublicか確認）");
      return;
    }
    jobs = JSON.parse(res.getContentText());
  } catch (e) {
    Logger.log("⚠️ JSON取得/パースエラー: " + e);
    return;
  }
  if (!Array.isArray(jobs)) { Logger.log("⚠️ JSONが配列でない"); return; }

  const existing = loadExistingUrls_(sheet);
  const newRows = [];
  for (const j of jobs) {
    const url = j.URL || j.url;
    if (!url || existing.has(url)) continue;
    newRows.push(HEADERS.map(h => (j[h] !== undefined && j[h] !== null) ? j[h] : ""));
    existing.add(url);
  }

  if (newRows.length > 0) {
    prependRows_(sheet, newRows);
    Logger.log("🎉 新着 " + newRows.length + "件を追加");
  } else {
    Logger.log("新着なし（JSON未更新か、全件取得済み）");
  }

  applyFormatting_(sheet);
  Logger.log("✅ 完了: " + ss.getUrl());
}

// ============================================================
// スプレッドシート管理
// ============================================================
function getOrCreateSpreadsheet_() {
  return SpreadsheetApp.getActiveSpreadsheet();
}

function getOrCreateSheet_(ss, name) {
  let sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
    sheet.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS]);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, HEADERS.length).setFontWeight("bold");
    sheet.setColumnWidth(3, 380); // 案件名
    sheet.setColumnWidth(4, 170); // 優先度
    sheet.setColumnWidth(9, 300); // URL
    sheet.setColumnWidth(10, 260); // ヒット語
  }
  const def = ss.getSheetByName("シート1") || ss.getSheetByName("Sheet1");
  if (def && ss.getSheets().length > 1) ss.deleteSheet(def);
  return sheet;
}

function loadExistingUrls_(sheet) {
  const last = sheet.getLastRow();
  if (last < 2) return new Set();
  const col = HEADERS.indexOf("URL") + 1;
  const vals = sheet.getRange(2, col, last - 1, 1).getValues();
  return new Set(vals.map(r => r[0]).filter(Boolean));
}

function prependRows_(sheet, rows) {
  sheet.insertRowsBefore(2, rows.length);
  sheet.getRange(2, 1, rows.length, HEADERS.length).setValues(rows);
}

/**
 * 色分けルール（優先度を最優先で適用し、高単価・スキルアップ案件を一目で目立たせる）:
 *   1. 優先度が「高単価×スキルアップ」「高単価」「スキルアップ」→ それぞれの色で塗る
 *   2. 優先度「通常」の行だけ、残日数で赤（残3日以内）/オレンジ（4〜7日）/グレー（不明）に塗る
 */
function applyFormatting_(sheet) {
  const last = sheet.getLastRow();
  if (last < 2) return;
  const numRows = last - 1;
  const daysCol = HEADERS.indexOf("残日数") + 1;
  const priorityCol = HEADERS.indexOf("優先度") + 1;
  const days = sheet.getRange(2, daysCol, numRows, 1).getValues();
  const priorities = sheet.getRange(2, priorityCol, numRows, 1).getValues();
  const full = sheet.getRange(2, 1, numRows, HEADERS.length);
  full.setBackground(null).setFontColor(COLOR_NORMAL_TEXT);

  for (let i = 0; i < days.length; i++) {
    const row = sheet.getRange(2 + i, 1, 1, HEADERS.length);
    const priority = String(priorities[i][0] || "");

    if (priority.indexOf("高単価") !== -1 && priority.indexOf("スキルアップ") !== -1) {
      row.setBackground(COLOR_PRIORITY_TOP).setFontColor(COLOR_NORMAL_TEXT);
      continue;
    }
    if (priority.indexOf("高単価") !== -1) {
      row.setBackground(COLOR_PRIORITY_HIGH).setFontColor(COLOR_NORMAL_TEXT);
      continue;
    }
    if (priority.indexOf("スキルアップ") !== -1) {
      row.setBackground(COLOR_PRIORITY_SKILL).setFontColor(COLOR_NORMAL_TEXT);
      continue;
    }

    const v = days[i][0];
    if (v === "" || v === null || isNaN(Number(v))) {
      row.setBackground(COLOR_UNKNOWN).setFontColor(COLOR_UNKNOWN_TEXT);
    } else {
      const d = Number(v);
      if (d <= 3)      row.setBackground(COLOR_URGENT).setFontColor(COLOR_URGENT_TEXT);
      else if (d <= 7) row.setBackground(COLOR_SOON).setFontColor(COLOR_SOON_TEXT);
    }
  }
}

// ============================================================
// トリガー
// ============================================================
function setupTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === "checkJobs") ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger("checkJobs")
    .timeBased().everyDays(1).atHour(9).nearMinute(0).create(); // GitHub(8:00)の後に読む
  Logger.log("✅ 毎朝9時の自動実行トリガーを登録");
  Logger.log("スプレッドシートURL: " + getOrCreateSpreadsheet_().getUrl());
}

function removeTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === "checkJobs") {
      ScriptApp.deleteTrigger(t);
      Logger.log("トリガー削除");
    }
  });
}

// 手動テスト用
function testRun() { checkJobs(); }

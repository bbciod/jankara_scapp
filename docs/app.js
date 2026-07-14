// ===== マスタ定義 =====

// 会員区分（比較軸）。app.py の member_types と同順。既定は「会員」。
const MEMBER_TYPES = ["一般", "会員", "学生", "学生会員", "シニア"];

// 北海道から沖縄までの都道府県コード順（JIS X 0401）。地方ごとにまとまる。
const PREF_ORDER = [
  "北海道",
  "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
  "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
  "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
  "岐阜県", "静岡県", "愛知県", "三重県",
  "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
  "鳥取県", "島根県", "岡山県", "広島県", "山口県",
  "徳島県", "香川県", "愛媛県", "高知県",
  "福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県",
  "沖縄県",
];

// プラン種別の表示順（classifyPlan の出力順）。存在するものだけ使う。
const PLAN_TYPE_ORDER = [
  "30分利用", "朝フリータイム", "昼フリータイム", "夕方フリータイム", "夜フリータイム",
  "深夜・ナイトパック", "開店～閉店 (エンドレス)", "その他フリータイム", "その他",
];

const TOP_N_CARDS = 50;

let allShops = [];
let flatRows = []; // ロング形式: 1行 = 店舗×料金プラン
let viewMode = "card"; // "card" | "table"

// ===== プラン種別の分類（app.py classify_plan の移植）=====

function classifyPlan(plan) {
  if (!plan) return "その他";
  if (plan.includes("30分")) return "30分利用";
  if (plan.includes("朝フリー")) return "朝フリータイム";
  if (plan.includes("昼フリー")) return "昼フリータイム";
  if (plan.includes("夕方フリー")) return "夕方フリータイム";
  if (plan.includes("夜フリー")) return "夜フリータイム";
  if (plan.includes("深夜フリー") || plan.includes("ナイトパック")) return "深夜・ナイトパック";
  if (plan.includes("開店～閉店")) return "開店～閉店 (エンドレス)";
  return "その他フリータイム";
}

// 金額型の文字列か（防衛フィルタ用）
function looksLikeMoney(str) {
  if (typeof str !== "string") return false;
  return /[¥￥]/.test(str) || /^[\d,，]+$/.test(str.trim());
}

// ===== データ読み込み・フラット化 =====

async function init() {
  const res = await fetch("data/shops_data.json");
  allShops = await res.json();

  flatRows = buildFlatRows(allShops);

  const latest = latestScrapedAt(allShops);
  document.getElementById("updatedAt").textContent =
    latest ? new Date(latest).toLocaleDateString("ja-JP") : "—";

  // 会員区分セレクタ（既定は「会員」= index 1）
  const memberSel = document.getElementById("memberType");
  memberSel.innerHTML = MEMBER_TYPES.map(m => `<option value="${m}">${m}</option>`).join("");
  memberSel.value = "会員";
  memberSel.addEventListener("change", onFilterChange);

  setupExclusiveChipGroup("prefFilters", buildPrefOptions(), onFilterChange);
  setupExclusiveChipGroup("sectionFilters", buildSectionOptions(), onFilterChange);
  setupExclusiveChipGroup("planTypeFilters", buildPlanTypeOptions(), onFilterChange);
  setupExclusiveChipGroup("dayFilters", buildDayOptions(), onFilterChange);

  document.getElementById("priceMax").addEventListener("input", onFilterChange);
  document.getElementById("modeCardBtn").addEventListener("click", () => setViewMode("card"));
  document.getElementById("modeTableBtn").addEventListener("click", () => setViewMode("table"));

  renderAll();
}

// データ衛生: status=OK のみ。店舗×price_table を「1行=比較単位」に展開。
// [Fix2] 数字のみのプラン詳細を除外 / [Fix5] 曜日が金額の行を除外。
function buildFlatRows(shops) {
  const rows = [];
  for (const s of shops) {
    if (s.status !== "OK") continue;
    const name = s.name || `店舗ID: ${s.shop_id} (名前取得エラー)`;
    for (const p of s.price_table || []) {
      const planDetail = p.plan || "";
      if (/^\d+$/.test(planDetail)) continue;                 // [Fix2]
      if (looksLikeMoney(p.day_type)) continue;               // [Fix5]

      rows.push({
        shop_id: s.shop_id,
        store_name: name,
        pref: s.prefecture || "",
        url: s.url || "#",
        section: p.section || "",
        plan_detail: planDetail,
        plan_type: classifyPlan(planDetail),
        day_type: p.day_type || "",
        prices: {
          "一般": p["一般"], "会員": p["会員"], "学生": p["学生"],
          "学生会員": p["学生会員"], "シニア": p["シニア"],
        },
      });
    }
  }
  return rows;
}

function latestScrapedAt(shops) {
  let latest = null;
  for (const s of shops) {
    if (s.status === "OK" && s.scraped_at && (!latest || s.scraped_at > latest)) latest = s.scraped_at;
  }
  return latest;
}

// 選択中の会員区分での表示料金（学生会員が無ければ学生で代用: app.py [Fix3]）
function displayPrice(row, member) {
  let v = row.prices[member];
  if (member === "学生会員" && (v == null)) v = row.prices["学生"];
  return (v == null) ? null : v;
}

function getMemberType() {
  return document.getElementById("memberType").value;
}

// ===== フィルタ選択肢の構築 =====

function buildPrefOptions() {
  const present = new Set(allShops.filter(s => s.status === "OK").map(s => s.prefecture).filter(Boolean));
  const ordered = PREF_ORDER.filter(p => present.has(p));
  for (const p of present) if (!PREF_ORDER.includes(p)) ordered.push(p);
  return ordered.map(p => ({ value: p, label: p }));
}

function distinctInOrder(getter) {
  const seen = new Set();
  const out = [];
  for (const r of flatRows) {
    const v = getter(r);
    if (v && !seen.has(v)) { seen.add(v); out.push(v); }
  }
  return out;
}

function buildSectionOptions() {
  return distinctInOrder(r => r.section).map(v => ({ value: v, label: v }));
}

function buildPlanTypeOptions() {
  const present = new Set(flatRows.map(r => r.plan_type));
  const ordered = PLAN_TYPE_ORDER.filter(p => present.has(p));
  for (const p of present) if (!PLAN_TYPE_ORDER.includes(p)) ordered.push(p);
  return ordered.map(v => ({ value: v, label: v }));
}

function buildDayOptions() {
  return distinctInOrder(r => r.day_type).map(v => ({ value: v, label: v }));
}

// ===== 「すべて」排他ロジック付き複数選択チップ（kaikatu 流用）=====

function setupExclusiveChipGroup(containerId, options, onChange) {
  const wrap = document.getElementById(containerId);
  wrap.innerHTML = "";

  const allChip = document.createElement("label");
  allChip.className = "chip chip-all";
  allChip.innerHTML = `<input type="checkbox" value="__ALL__" checked> すべて`;
  wrap.appendChild(allChip);

  for (const opt of options) {
    const label = document.createElement("label");
    label.className = "chip";
    label.innerHTML = `<input type="checkbox" value="${escapeHtml(opt.value)}"> ${escapeHtml(opt.label)}`;
    wrap.appendChild(label);
  }

  wrap.addEventListener("change", (e) => {
    const allCheckbox = wrap.querySelector('input[value="__ALL__"]');
    const individualCheckboxes = [...wrap.querySelectorAll('input:not([value="__ALL__"])')];

    if (e.target === allCheckbox) {
      if (allCheckbox.checked) individualCheckboxes.forEach(cb => { cb.checked = false; });
    } else if (e.target.checked) {
      allCheckbox.checked = false;
    }
    // 個別が1つも選ばれていない状態は「すべて」と同義
    if (!individualCheckboxes.some(cb => cb.checked)) allCheckbox.checked = true;

    onChange();
  });
}

function getSelectedValues(containerId) {
  const wrap = document.getElementById(containerId);
  const allChecked = wrap.querySelector('input[value="__ALL__"]').checked;
  if (allChecked) return []; // 空配列 = フィルタしない
  return [...wrap.querySelectorAll('input:checked')].map(cb => cb.value);
}

function onFilterChange() { renderAll(); }

function setViewMode(mode) {
  viewMode = mode;
  document.getElementById("modeCardBtn").classList.toggle("active", mode === "card");
  document.getElementById("modeTableBtn").classList.toggle("active", mode === "table");
  document.getElementById("cardResults").classList.toggle("hidden", mode !== "card");
  document.getElementById("cardNote").classList.toggle("hidden", mode !== "card");
  document.getElementById("tableResults").classList.toggle("hidden", mode !== "table");
  renderResults();
}

// ===== 検索・比較ロジック（固定順）=====
// 1.比較軸(会員区分) 2.カテゴリ絞り込み 3.null除外 4.しきい値 5.昇順ソート 6.サマリー

function buildFilteredRows() {
  const member = getMemberType();
  const prefs = getSelectedValues("prefFilters");
  const sections = getSelectedValues("sectionFilters");
  const planTypes = getSelectedValues("planTypeFilters");
  const days = getSelectedValues("dayFilters");
  const priceMaxRaw = document.getElementById("priceMax").value.trim();
  const priceMax = priceMaxRaw ? Number(priceMaxRaw) : null;

  const rows = [];
  for (const r of flatRows) {
    if (prefs.length && !prefs.includes(r.pref)) continue;
    if (sections.length && !sections.includes(r.section)) continue;
    if (planTypes.length && !planTypes.includes(r.plan_type)) continue;
    if (days.length && !days.includes(r.day_type)) continue;

    const price = displayPrice(r, member);
    if (price == null) continue;                          // 表示値null除外
    if (priceMax != null && price > priceMax) continue;   // しきい値

    rows.push({ ...r, _price: price });
  }
  rows.sort((a, b) => a._price - b._price);               // 昇順固定
  return rows;
}

function buildSummary(rows) {
  if (rows.length === 0) return { min: null, count: 0, avg: null };
  const prices = rows.map(r => r._price);
  const min = Math.min(...prices);
  const avg = Math.round(prices.reduce((a, b) => a + b, 0) / prices.length);
  return { min, count: rows.length, avg };
}

// ===== 条件チップ表示 =====

function renderConditionChips() {
  const member = getMemberType();
  const chips = [];

  // 会員区分は常に表示（比較軸なので×は付けない）
  chips.push({ text: `会員区分: ${member}`, fixed: true });

  const addGroup = (containerId, label) => {
    for (const v of getSelectedValues(containerId)) {
      chips.push({ text: `${label}: ${v}`, clear: () => uncheckOne(containerId, v) });
    }
  };
  addGroup("prefFilters", "都道府県");
  addGroup("sectionFilters", "時間帯");
  addGroup("planTypeFilters", "プラン");
  addGroup("dayFilters", "曜日");

  const priceMax = document.getElementById("priceMax").value.trim();
  if (priceMax) chips.push({ text: `予算: ¥${Number(priceMax).toLocaleString()}以下`, clear: () => { document.getElementById("priceMax").value = ""; onFilterChange(); } });

  const wrap = document.getElementById("conditionChips");
  const noFilter = chips.every(c => c.fixed);
  wrap.innerHTML = chips.map((c, i) => {
    if (c.fixed) return `<span class="condition-chip fixed">${escapeHtml(c.text)}</span>`;
    return `<span class="condition-chip" data-i="${i}">${escapeHtml(c.text)} <button type="button" aria-label="解除">×</button></span>`;
  }).join("") + (noFilter ? '<span class="no-condition">（エリア・条件で絞り込めます）</span>' : "");

  [...wrap.querySelectorAll(".condition-chip button")].forEach((btn) => {
    const i = Number(btn.closest(".condition-chip").dataset.i);
    btn.addEventListener("click", () => chips[i].clear());
  });
}

function uncheckOne(containerId, value) {
  const wrap = document.getElementById(containerId);
  const cb = wrap.querySelector(`input[value="${CSS.escape(value)}"]`);
  if (cb) {
    cb.checked = false;
    cb.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

// ===== 描画 =====

function renderAll() {
  renderConditionChips();
  renderResults();
}

function renderResults() {
  const rows = buildFilteredRows();
  const summary = buildSummary(rows);

  document.getElementById("summaryMin").textContent = summary.min != null ? `¥${summary.min.toLocaleString()}` : "—";
  document.getElementById("summaryCount").textContent = `${summary.count.toLocaleString()} 件`;
  document.getElementById("summaryAvg").textContent = summary.avg != null ? `¥${summary.avg.toLocaleString()}` : "—";
  document.getElementById("resultCount").textContent = `${rows.length.toLocaleString()} 件（安い順）`;

  if (viewMode === "card") {
    renderCardResults(rows);
  } else {
    renderTableResults(rows);
  }
}

function otherMembersText(row, member) {
  const parts = [];
  for (const m of MEMBER_TYPES) {
    if (m === member) continue;
    const v = row.prices[m];
    if (v != null) parts.push(`${m} ¥${v.toLocaleString()}`);
  }
  return parts.join(" / ");
}

function renderCardResults(rows) {
  const member = getMemberType();
  const top = rows.slice(0, TOP_N_CARDS);
  const note = document.getElementById("cardNote");
  note.textContent = rows.length > TOP_N_CARDS
    ? `上位 ${TOP_N_CARDS} 件を表示しています（条件を絞ると全件に近づきます）。`
    : "";

  const wrap = document.getElementById("cardResults");
  if (top.length === 0) {
    wrap.innerHTML = '<p class="empty-note">条件に一致する結果がありません。条件をゆるめてみてください。</p>';
    return;
  }
  wrap.innerHTML = top.map((r, i) => {
    const tags = [r.plan_type, r.day_type, r.plan_detail]
      .filter(Boolean)
      .map(t => `<span class="tag">${escapeHtml(t)}</span>`).join("");
    const others = otherMembersText(r, member);
    return `
      <div class="result-card-outer">
        <div class="result-card">
          <div class="rank-badge">${i + 1}</div>
          <div class="card-body">
            <h3>${escapeHtml(r.store_name)}</h3>
            <p class="card-sub">${escapeHtml(r.pref)}${r.section ? "・" + escapeHtml(r.section) : ""}</p>
            <div class="tag-list">${tags}</div>
          </div>
          <div class="card-price">
            <div class="price-main">¥${r._price.toLocaleString()}</div>
            <div class="price-label">${escapeHtml(member)}</div>
            ${others ? `<div class="price-others">${escapeHtml(others)}</div>` : ""}
            <a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">店舗ページ ↗</a>
          </div>
        </div>
      </div>`;
  }).join("");
}

function renderTableResults(rows) {
  const member = getMemberType();
  const others = MEMBER_TYPES.filter(m => m !== member);
  const wrap = document.querySelector("#tableResults .table-scroll");

  if (rows.length === 0) {
    wrap.innerHTML = '<p class="empty-note">条件に一致する結果がありません。</p>';
    return;
  }

  const head = ["都道府県", "店舗名", "時間帯", "プラン種別", "プラン詳細", "曜日", `★${member}`, ...others, ""];
  let html = "<table><thead><tr>" + head.map(h => `<th>${escapeHtml(h)}</th>`).join("") + "</tr></thead><tbody>";
  for (const r of rows) {
    const cells = [
      escapeHtml(r.pref),
      escapeHtml(r.store_name),
      escapeHtml(r.section),
      escapeHtml(r.plan_type),
      escapeHtml(r.plan_detail),
      escapeHtml(r.day_type),
      `¥${r._price.toLocaleString()}`,
      ...others.map(m => r.prices[m] != null ? `¥${r.prices[m].toLocaleString()}` : '<span class="na">-</span>'),
      `<a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">店舗ページ ↗</a>`,
    ];
    html += "<tr>" + cells.map(c => `<td>${c}</td>`).join("") + "</tr>";
  }
  html += "</tbody></table>";
  wrap.innerHTML = html;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

init();

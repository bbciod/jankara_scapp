import streamlit as st
import pandas as pd
import json
import html
from pathlib import Path

# ページ設定
st.set_page_config(page_title="ジャンカラ料金比較", page_icon="🎤", layout="wide")

# 「すべて」選択肢用のラベル定数
ALL_OPTION = "（すべて）"

# 表示モード（ページ上部のトグルで切替）
CARD_MODE = "🗂 カード表示"
TABLE_MODE = "📋 一覧表（PC向け）"

# 都道府県の並び順（標準の都道府県コード順＝北海道→東北→関東→…→九州・沖縄）。
# この順序にすることで地方ごとにまとまった、北からの並びになる。
PREFECTURE_ORDER = [
    "北海道",
    "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
    "岐阜県", "静岡県", "愛知県", "三重県",
    "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
    "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県",
    "福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]
_PREF_RANK = {p: i for i, p in enumerate(PREFECTURE_ORDER)}

# プランの表記ゆれを吸収して、きれいなカテゴリーに分類する関数（変更不可）
def classify_plan(plan_str):
    if not plan_str:
        return "その他"
    
    if "30分" in plan_str:
        return "30分利用"
    elif "朝フリー" in plan_str:
        return "朝フリータイム"
    elif "昼フリー" in plan_str:
        return "昼フリータイム"
    elif "夕方フリー" in plan_str:
        return "夕方フリータイム"
    elif "夜フリー" in plan_str:
        return "夜フリータイム"
    elif "深夜フリー" in plan_str or "ナイトパック" in plan_str:
        return "深夜・ナイトパック"
    elif "開店～閉店" in plan_str:
        return "開店～閉店 (エンドレス)"
    else:
        return "その他フリータイム"

@st.cache_data(ttl=3600)
def load_data():
    data_path = Path("data/shops_data.json")
    if not data_path.exists():
        return pd.DataFrame(), None
    
    with open(data_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)
        
    rows = []
    latest_time = None
    for shop in raw_data:
        if shop.get("status") != "OK":
            continue
        
        # nameがnullの場合は店舗IDを代わりに使用（変更不可）
        shop_name = shop.get("name") or f"店舗ID: {shop.get('shop_id')} (名前取得エラー)"
        
        scraped_at = shop.get("scraped_at")
        if scraped_at:
            latest_time = scraped_at
            
        for plan in shop.get("price_table", []):
            raw_plan_name = plan.get("plan", "")
            
            rows.append({
                "都道府県": shop.get("prefecture"),
                "店舗名": shop_name,
                "時間帯": plan.get("section"),
                "プラン種別": classify_plan(raw_plan_name),  # きれいに分類した名前（変更不可）
                "プラン詳細": raw_plan_name,                 # 元の細かい時間表記（変更不可）
                "曜日": plan.get("day_type"),
                "学生会員": plan.get("学生会員"),
                "学生": plan.get("学生"),
                "会員": plan.get("会員"),
                "シニア": plan.get("シニア"),
                "一般": plan.get("一般"),
                "URL": shop.get("url"),
            })

    df = pd.DataFrame(rows)

    # [Fix 2] 数字のみのプラン詳細を持つ行を除外（スクレイパー誤解析の防衛的フィルタ）
    if not df.empty:
        df = df[~df["プラン詳細"].str.fullmatch(r"\d+", na=False)]

    # [Fix 5] 曜日が金額（¥1,930 等）になっている列ズレ行を除外。
    # 原因はスクレイパーの DAY_TYPE_RE に「日祝」が無く、日祝行で料金が day_type に
    # 押し出されたこと（scraper.py 側で修正済み。再取得までの旧データ防衛フィルタ）。
    if not df.empty:
        df = df[~df["曜日"].astype(str).str.contains(r"[¥￥]|^[\d,，]+$", na=False, regex=True)]

    return df, latest_time


# ──────────────────────────────────────────────────────────
# [Fix 4] 「（すべて）」をデフォルトにするmultiselect ヘルパー
# ──────────────────────────────────────────────────────────
def _enforce_all_exclusive(key: str):
    """
    「（すべて）」と個別選択肢の排他制御（multiselect の on_change コールバック）。
    multiselect は新しくクリックされた項目を末尾に追加するため、末尾を見て
    どちらが最後に選ばれたかを判定する。
      - 末尾が「（すべて）」     → ユーザーは全体を選び直した → 「（すべて）」のみに
      - 末尾が個別選択肢        → ユーザーは個別を選んだ     → 「（すべて）」を外す
    """
    sel = st.session_state[key]
    if ALL_OPTION in sel and len(sel) > 1:
        if sel[-1] == ALL_OPTION:
            st.session_state[key] = [ALL_OPTION]
        else:
            st.session_state[key] = [o for o in sel if o != ALL_OPTION]


def make_multiselect(label: str, options: list, sort: bool = True, key: str | None = None) -> list | None:
    """
    「（すべて）」が選択されている（またはなにも選択されていない）場合は
    None を返す → 呼び出し元でフィルターをスキップ。
    それ以外は選択された実際の値のリストを返す。

    「（すべて）」と47都道府県などの個別選択は排他（_enforce_all_exclusive）。
    """
    if sort:
        options = sorted(options)
    opts = [ALL_OPTION] + options
    key = key or f"ms_{label}"

    # 初期値は「（すべて）」。default= ではなく session_state で初期化することで
    # on_change コールバックによる値の上書きと両立させる。
    if key not in st.session_state:
        st.session_state[key] = [ALL_OPTION]

    # st.multiselect は呼び出し時のコンテキスト（with st.expander / with col）に描画される。
    selected = st.multiselect(
        label, opts, key=key,
        on_change=_enforce_all_exclusive, args=(key,),
    )

    # 「（すべて）」が含まれている or 何も選んでいない → フィルター不要
    if ALL_OPTION in selected or not selected:
        return None
    return selected


# ──────────────────────────────────────────────────────────
# カードUIのスタイル（チップ＋カード型 / 案②）
# Streamlit はカスタムCSS変数を公開しないため、独自変数を定義し
# prefers-color-scheme でライト/ダーク（OS設定追従）両対応にする。
# ──────────────────────────────────────────────────────────
CARD_CSS = """
<style>
:root{
  --jk-primary:#FF4B4B; --jk-card-bg:#F0F2F6;
  --jk-soft:rgba(0,0,0,0.07); --jk-soft-strong:rgba(0,0,0,0.10);
}
@media (prefers-color-scheme: dark){
  :root{ --jk-card-bg:#262730; --jk-soft:rgba(255,255,255,0.10); --jk-soft-strong:rgba(255,255,255,0.14); }
}
.jk-chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 4px;}
.jk-chip{font-size:13px;padding:3px 12px;border-radius:999px;
  border:1px solid var(--jk-primary);color:var(--jk-primary);white-space:nowrap;}
.jk-chip.muted{border-color:rgba(128,128,128,0.45);color:inherit;opacity:0.7;}
.jk-card{display:flex;justify-content:space-between;align-items:center;gap:12px;
  flex-wrap:wrap;background:var(--jk-card-bg);
  border-radius:12px;padding:12px 16px;margin-bottom:10px;}
.jk-left{display:flex;align-items:center;gap:12px;flex:1 1 60%;min-width:200px;}
.jk-rank{flex:none;width:30px;height:30px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;font-weight:600;font-size:15px;
  background:var(--jk-soft-strong);}
.jk-rank.top{background:var(--jk-primary);color:#fff;}
.jk-name{font-size:16px;font-weight:600;line-height:1.3;}
.jk-sub{font-size:12px;opacity:0.65;margin-top:2px;}
.jk-tags{display:flex;flex-wrap:wrap;gap:5px;margin-top:6px;}
.jk-tag{font-size:11px;padding:1px 8px;border-radius:6px;background:var(--jk-soft);}
.jk-right{text-align:right;flex:0 0 auto;}
.jk-price{font-size:22px;font-weight:700;color:var(--jk-primary);line-height:1.1;}
.jk-price-lbl{font-size:11px;opacity:0.6;}
.jk-others{font-size:11px;opacity:0.65;margin-top:2px;}
.jk-link{font-size:12px;text-decoration:none;}
</style>
"""

# ──────────────────────────────────────────────────────────
# メイン画面
# ──────────────────────────────────────────────────────────
st.title("🎤 ジャンカラ料金比較アプリ")
st.markdown(CARD_CSS, unsafe_allow_html=True)

# 表示モード切替（メインはカード、PCでは一覧表に切替可能）
view_mode = st.radio(
    "表示モード",
    [CARD_MODE, TABLE_MODE],
    horizontal=True,
    key="view_mode",
)

df, update_time = load_data()

if df.empty:
    st.warning("データが見つかりません。スクレイピングを実行して `data/shops_data.json` を作成してください。")
    st.stop()

member_types = ["一般", "会員", "学生", "学生会員", "シニア"]

# 各フィルターの選択肢
# 都道府県は北から地方順（都道府県コード順）に並べる。未知の値は末尾へ。
prefs = sorted(
    df["都道府県"].dropna().unique().tolist(),
    key=lambda p: _PREF_RANK.get(p, len(PREFECTURE_ORDER)),
)
sections = df["時間帯"].dropna().unique().tolist()
plan_types = df["プラン種別"].dropna().unique().tolist()
days = df["曜日"].dropna().unique().tolist()

# ──────────────────────────────────────────────────────────
# 検索条件（画面上部の expander 内。スマホでも条件が隠れない）
# ──────────────────────────────────────────────────────────
with st.expander("🔍 検索条件", expanded=True):
    top1, top2 = st.columns(2)
    with top1:
        # ★ 会員区分は料金列を決める主役なので最前面に
        selected_member = st.selectbox("★ あなたの会員区分", member_types, index=1)
    with top2:
        max_price = st.slider("上限金額 (円)", min_value=0, max_value=10000, value=5000, step=100)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        # prefs は既に北→南（地方順）に整列済みなので sort=False で維持
        selected_prefs = make_multiselect("都道府県", prefs, sort=False, key="ms_pref")
    with c2:
        selected_sections = make_multiselect("時間帯", sections, sort=False, key="ms_section")
    with c3:
        selected_plans = make_multiselect("プラン種別", plan_types, key="ms_plan")
    with c4:
        selected_days = make_multiselect("曜日", days, sort=False, key="ms_day")

# ──────────────────────────────────────────────────────────
# データのフィルタリング
# ──────────────────────────────────────────────────────────
filtered_df = df.copy()

if selected_prefs is not None:
    filtered_df = filtered_df[filtered_df["都道府県"].isin(selected_prefs)]
if selected_sections is not None:
    filtered_df = filtered_df[filtered_df["時間帯"].isin(selected_sections)]
if selected_plans is not None:
    filtered_df = filtered_df[filtered_df["プラン種別"].isin(selected_plans)]
if selected_days is not None:
    filtered_df = filtered_df[filtered_df["曜日"].isin(selected_days)]

# ──────────────────────────────────────────────────────────
# [Fix 3] 「学生会員」フォールバック処理
# 学生会員が未設定(NaN)の店舗は「学生」料金で代用して比較・ソート
# ──────────────────────────────────────────────────────────
filtered_df = filtered_df.copy()
if selected_member == "学生会員":
    filtered_df["_表示料金"] = filtered_df["学生会員"].fillna(filtered_df["学生"])
    member_note = "（※学生料金で代用含む）"
else:
    filtered_df["_表示料金"] = filtered_df[selected_member]
    member_note = ""

# 料金が null のものを除外 & 上限金額以下に絞る & 安い順ソート
filtered_df = filtered_df.dropna(subset=["_表示料金"])
filtered_df = filtered_df[filtered_df["_表示料金"] <= max_price]
filtered_df = filtered_df.sort_values(by="_表示料金", ascending=True).reset_index(drop=True)

# ──────────────────────────────────────────────────────────
# 選択中の条件チップ（一目で現在の絞り込みがわかる）
# ──────────────────────────────────────────────────────────
def _chip(text: str, muted: bool = False) -> str:
    cls = "jk-chip muted" if muted else "jk-chip"
    return f'<span class="{cls}">{html.escape(text)}</span>'

chips = [
    _chip(f"会員区分: {selected_member}"),
    _chip(f"予算: 〜¥{max_price:,}"),
]
if selected_prefs:
    chips.append(_chip("都道府県: " + "・".join(selected_prefs)))
if selected_sections:
    chips.append(_chip("時間帯: " + "・".join(selected_sections)))
if selected_plans:
    chips.append(_chip("プラン: " + "・".join(selected_plans)))
if selected_days:
    chips.append(_chip("曜日: " + "・".join(selected_days)))
if not any([selected_prefs, selected_sections, selected_plans, selected_days]):
    chips.append(_chip("エリア・条件: すべて", muted=True))

st.markdown('<div class="jk-chips">' + "".join(chips) + "</div>", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────
# サマリー（最安・件数・平均）
# ──────────────────────────────────────────────────────────
if filtered_df.empty:
    st.info("条件に合う料金プランが見つかりませんでした。条件をゆるめてみてください。")
    st.caption(f"最終データ更新: {update_time}")
    st.stop()

m1, m2, m3 = st.columns(3)
m1.metric("最安料金", f"¥{int(filtered_df['_表示料金'].min()):,}")
m2.metric("該当件数", f"{len(filtered_df):,} 件")
m3.metric("平均料金", f"¥{int(filtered_df['_表示料金'].mean()):,}")

# ──────────────────────────────────────────────────────────
# 結果表示（モードで分岐：カード / 一覧表）
# ──────────────────────────────────────────────────────────
st.subheader(f"検索結果: {len(filtered_df):,} 件 (安い順){member_note}")

other_members = [m for m in member_types if m != selected_member]


def _render_card(rank: int, row: pd.Series) -> str:
    price = int(row["_表示料金"])
    rank_cls = "jk-rank top" if rank <= 3 else "jk-rank"

    sub_parts = [p for p in [row.get("都道府県"), row.get("時間帯")] if p]
    sub = "・".join(html.escape(str(p)) for p in sub_parts)

    tags = []
    for val in [row.get("プラン種別"), row.get("曜日"), row.get("プラン詳細")]:
        if val and str(val) != "nan":
            tags.append(f'<span class="jk-tag">{html.escape(str(val))}</span>')
    tags_html = f'<div class="jk-tags">{"".join(tags)}</div>' if tags else ""

    # 他会員区分の料金（比較用）
    others = []
    for m in other_members:
        v = row.get(m)
        if pd.notna(v):
            others.append(f"{m} ¥{int(v):,}")
    others_html = f'<div class="jk-others">{html.escape(" / ".join(others))}</div>' if others else ""

    url = row.get("URL") or "#"
    link_html = f'<a class="jk-link" href="{html.escape(str(url))}" target="_blank">店舗ページ ↗</a>'

    return (
        '<div class="jk-card">'
        '<div class="jk-left">'
        f'<div class="{rank_cls}">{rank}</div>'
        '<div>'
        f'<div class="jk-name">{html.escape(str(row.get("店舗名", "")))}</div>'
        f'<div class="jk-sub">{sub}</div>'
        f'{tags_html}'
        '</div></div>'
        '<div class="jk-right">'
        f'<div class="jk-price">¥{price:,}</div>'
        f'<div class="jk-price-lbl">{html.escape(selected_member)}</div>'
        f'{others_html}'
        f'{link_html}'
        '</div></div>'
    )


if view_mode == TABLE_MODE:
    # ── 一覧表モード（PC向け・全件をテーブル表示） ──
    price_col = f"★ {selected_member}" + ("（学生料金代用含む）" if selected_member == "学生会員" else "")
    table_df = filtered_df.copy()
    table_df[price_col] = table_df["_表示料金"].astype("Int64")
    # 他会員料金も小数点なしの整数表示に
    for m in other_members:
        if m in table_df.columns:
            table_df[m] = table_df[m].astype("Int64")

    cols_to_show = ["都道府県", "店舗名", "時間帯", "プラン種別", "プラン詳細", "曜日", price_col]
    cols_to_show += other_members
    cols_to_show.append("URL")
    cols_to_show = [c for c in cols_to_show if c in table_df.columns]

    st.dataframe(
        table_df[cols_to_show],
        column_config={"URL": st.column_config.LinkColumn("店舗リンク")},
        use_container_width=True,
        hide_index=True,
    )
else:
    # ── カードモード（既定・ランキング、上位50件） ──
    MAX_CARDS = 50
    if len(filtered_df) > MAX_CARDS:
        st.caption(f"上位 {MAX_CARDS} 件を表示しています（条件を絞ると全件に近づきます）。")

    cards_html = "".join(
        _render_card(i + 1, row)
        for i, row in filtered_df.head(MAX_CARDS).iterrows()
    )
    st.markdown(cards_html, unsafe_allow_html=True)

st.caption(f"最終データ更新: {update_time}")
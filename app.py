import streamlit as st
import pandas as pd
import json
from pathlib import Path

# ページ設定
st.set_page_config(page_title="ジャンカラ料金比較", page_icon="🎤", layout="wide")

# 「すべて」選択肢用のラベル定数
ALL_OPTION = "（すべて）"

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

    return df, latest_time


# ──────────────────────────────────────────────────────────
# [Fix 4] 「（すべて）」をデフォルトにするmultiselect ヘルパー
# ──────────────────────────────────────────────────────────
def make_multiselect(label: str, options: list, sort: bool = True) -> list | None:
    """
    「（すべて）」が選択されている（またはなにも選択されていない）場合は
    None を返す → 呼び出し元でフィルターをスキップ。
    それ以外は選択された実際の値のリストを返す。
    """
    if sort:
        options = sorted(options)
    opts = [ALL_OPTION] + options
    selected = st.sidebar.multiselect(label, opts, default=[ALL_OPTION])

    # 「（すべて）」が含まれている or 何も選んでいない → フィルター不要
    if ALL_OPTION in selected or not selected:
        return None
    return selected


# ──────────────────────────────────────────────────────────
# メイン画面
# ──────────────────────────────────────────────────────────
st.title("🎤 ジャンカラ料金比較アプリ")

df, update_time = load_data()

if df.empty:
    st.warning("データが見つかりません。スクレイピングを実行して `data/shops_data.json` を作成してください。")
    st.stop()

# --- サイドバー (検索条件) ---
st.sidebar.header("🔍 検索条件")

# 都道府県フィルター
prefs = df["都道府県"].dropna().unique().tolist()
selected_prefs = make_multiselect("都道府県", prefs)

# 時間帯フィルター
sections = df["時間帯"].dropna().unique().tolist()
selected_sections = make_multiselect("時間帯 (朝/昼/夜など)", sections, sort=False)

# プラン種別フィルター（classify_plan によって整理済みの値を使用）
plan_types = df["プラン種別"].dropna().unique().tolist()
selected_plans = make_multiselect("プラン種別", plan_types)

# 曜日フィルター
days = df["曜日"].dropna().unique().tolist()
selected_days = make_multiselect("曜日 (平日/土日祝など)", days, sort=False)

st.sidebar.divider()

# 会員種別選択 (ソート・強調用)
member_types = ["一般", "会員", "学生", "学生会員", "シニア"]
selected_member = st.sidebar.selectbox("★ あなたの会員区分", member_types, index=1)

# 上限金額
max_price = st.sidebar.number_input("上限金額 (円)", min_value=0, max_value=10000, value=5000, step=100)

# --- データのフィルタリング ---
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
if selected_member == "学生会員":
    filtered_df = filtered_df.copy()
    filtered_df["_表示料金"] = filtered_df["学生会員"].fillna(filtered_df["学生"])
    display_col_label = "★ 学生会員 (※学生料金で代用含む)"
else:
    filtered_df = filtered_df.copy()
    filtered_df["_表示料金"] = filtered_df[selected_member]
    display_col_label = f"★ {selected_member}"

# 料金が null のものを除外 & 上限金額以下に絞る & ソート
filtered_df = filtered_df.dropna(subset=["_表示料金"])
filtered_df = filtered_df[filtered_df["_表示料金"] <= max_price]
filtered_df = filtered_df.sort_values(by="_表示料金", ascending=True)

# --- 表示の整形 ---
st.subheader(f"検索結果: {len(filtered_df)} 件 (安い順)")

display_df = filtered_df.copy()
display_df[display_col_label] = display_df["_表示料金"]

# 表示するカラムの順番を整理
cols_to_show = ["都道府県", "店舗名", "時間帯", "プラン種別", "プラン詳細", "曜日", display_col_label]
other_members = [m for m in member_types if m != selected_member]
cols_to_show.extend(other_members)
cols_to_show.append("URL")

# 存在しないカラムを安全に除外
cols_to_show = [c for c in cols_to_show if c in display_df.columns]

st.dataframe(
    display_df[cols_to_show],
    column_config={
        "URL": st.column_config.LinkColumn("店舗リンク")
    },
    use_container_width=True,
    hide_index=True
)

st.caption(f"最終データ更新: {update_time}")
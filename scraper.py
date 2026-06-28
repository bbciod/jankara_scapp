"""
ジャンカラ 料金表スクレイパー — Playwright版
=============================================
背景:
  jankara.ne.jp はNext.js/React製のSPAのため、requests でHTMLを取得しても
  h1・料金表などのコンテンツはJavaScriptで後から注入される。
  → Playwright（ヘッドレスChromium）でJS実行後のDOMを取得することで解決。

動作環境:
  - ローカル: Python 3.11+
  - GitHub Actions: ubuntu-latest + playwright install chromium --with-deps

倫理・保守方針:
  - 起動時に robots.txt を確認し Disallow なら即座に終了
  - ページ読み込み後に SLEEP_SEC 秒のウェイトを挿入
  - 抽出ロジックは extract_basic_info() / extract_price_table() に集約
  - パース失敗は None / "PARSE_ERROR" として記録（推測しない）
"""

import re
import json
import asyncio
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

# ──────────────────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────────────────
BASE_URL           = "https://jankara.ne.jp"
ROBOTS_URL         = f"{BASE_URL}/robots.txt"
SHOP_URL_TEMPLATE  = f"{BASE_URL}/shop/{{shop_id}}/"
RESULT_URL         = f"{BASE_URL}/shop/result/?a={{area_id}}"

UA_NAME  = "JankaraDataCollector/3.0 (research; non-commercial)"
# Playwright には一般的なブラウザUAを使用（robots.txtチェック用のみ専用UA）
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# リクエスト間ウェイト（秒）—— ページ描画完了後に追加で待機
SLEEP_SEC   = 4.0

# 料金表テーブルが描画されるまでの最大待機時間（ms）
PRICE_TABLE_TIMEOUT = 15_000   # 15秒

# エリアID一覧（公式 /shop/ ページより）
AREA_IDS = list(range(1, 29))  # a=1〜28

# 出力
OUTPUT_DIR  = Path("data")
OUTPUT_FILE = OUTPUT_DIR / "shops_data.json"

MEMBER_COLS   = ["学生会員", "学生", "会員", "シニア", "一般"]
# 「日祝」(日曜・祝日) を追加。これが欠けていると、日祝行の先頭セルがプラン名と
# 誤判定され、料金が day_type に押し出される列ズレが発生する（096/174/187 で確認）。
DAY_TYPE_RE   = re.compile(r"^(平日|土日祝?|日祝|金土(日)?祝(祝前)?|金土祝前|祝前)$")
PREF_RE       = re.compile(
    r"(北海道|青森県|岩手県|宮城県|秋田県|山形県|福島県|"
    r"茨城県|栃木県|群馬県|埼玉県|千葉県|東京都|神奈川県|"
    r"新潟県|富山県|石川県|福井県|山梨県|長野県|岐阜県|"
    r"静岡県|愛知県|三重県|滋賀県|京都府|大阪府|兵庫県|"
    r"奈良県|和歌山県|鳥取県|島根県|岡山県|広島県|山口県|"
    r"徳島県|香川県|愛媛県|高知県|福岡県|佐賀県|長崎県|"
    r"熊本県|大分県|宮崎県|鹿児島県|沖縄県)"
)

# [Fix 2] 数字のみのプラン名を検出する正規表現（カンマ区切り数値も対象）
NUMERIC_ONLY_RE = re.compile(r"^[\d\s,，]+$")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
# robots.txt チェック（同期）
# ──────────────────────────────────────────────────────────
_robots_checked = False   # 起動時に1回だけチェック
_robots_allowed = True

def check_robots_once() -> bool:
    """
    robots.txt を起動時に1回だけ取得・確認する。
    Disallow であれば False を返しプログラム全体を停止させる。
    取得失敗（ネットワーク断等）は警告のうえ続行。
    """
    global _robots_checked, _robots_allowed
    if _robots_checked:
        return _robots_allowed
    _robots_checked = True

    rp = RobotFileParser()
    rp.set_url(ROBOTS_URL)
    try:
        rp.read()
        _robots_allowed = rp.can_fetch(UA_NAME, f"{BASE_URL}/shop/")
        if not _robots_allowed:
            log.error(
                f"❌ robots.txt により /shop/ へのアクセスは Disallow されています。"
                f"処理を中断します。"
            )
    except Exception as e:
        log.warning(f"robots.txt 取得失敗（ネットワーク等）: {e} → 続行します")
        _robots_allowed = True

    return _robots_allowed


# ──────────────────────────────────────────────────────────
# 全店舗URL収集（Playwright版: 一覧ページもCSRのため）（変更不可）
# ──────────────────────────────────────────────────────────
async def collect_all_shop_urls(page: Page) -> list[dict]:
    """
    エリア別一覧ページ（/shop/result/?a=N）から全店舗のURLとIDを収集する。
    一覧ページもNext.js/CSR構成のため Playwright で JS 描画後に DOM を取得する。

    待機戦略:
      1. networkidle で初期JS完了を待つ（変更不可）
      2. /shop/XXX/ 形式のリンクが1件以上現れるまで最大 LIST_TIMEOUT ms 待機（変更不可）
      3. それでも0件なら WARN を出してスキップ（推測しない）
    """
    # 店舗一覧リンクが描画されるまでの最大待機時間（ms）
    LIST_TIMEOUT    = 12_000
    # エリア間のウェイト（秒）— 一覧取得は店舗ページより軽いので短め
    LIST_SLEEP_SEC  = 2.5
    # /shop/XXX/ リンクを特定するCSSセレクタ（変更不可）
    SHOP_LINK_SEL   = "a[href*='/shop/']"

    shops: dict[str, dict] = {}

    for area_id in AREA_IDS:
        url = RESULT_URL.format(area_id=area_id)
        log.info(f"エリア a={area_id} 取得中: {url}")
        try:
            # networkidle で初期JS完了を待つ（変更不可）
            await page.goto(url, wait_until="networkidle", timeout=30_000)

            # 店舗リンクが現れるまで待機（変更不可）
            try:
                await page.wait_for_selector(SHOP_LINK_SEL, timeout=LIST_TIMEOUT)
            except PWTimeout:
                log.warning(f"  a={area_id}: 店舗リンクが時間内に描画されませんでした（該当なしの可能性）")

            # DOM が完全に落ち着くまで追加で少し待つ
            await asyncio.sleep(1.0)

            html = await page.content()
            soup = BeautifulSoup(html, "lxml")

            count_before = len(shops)
            for a_tag in soup.find_all("a", href=True):
                m = re.search(r"/shop/(\d{3,})/", a_tag["href"])
                if m:
                    sid = m.group(1)
                    if sid not in shops:
                        shops[sid] = {
                            "shop_id": sid,
                            "url": f"{BASE_URL}/shop/{sid}/",
                        }
            log.info(f"  → このエリアで {len(shops) - count_before} 件追加 / 累計 {len(shops)} 店舗")

        except Exception as e:
            log.warning(f"  a={area_id} 取得失敗: {e}")

        await asyncio.sleep(LIST_SLEEP_SEC)

    result = sorted(shops.values(), key=lambda x: x["shop_id"])
    log.info(f"✅ 全 {len(result)} 店舗のURLを収集しました")
    return result


# ──────────────────────────────────────────────────────────
# 基本情報の抽出
# ──────────────────────────────────────────────────────────
def extract_basic_info(soup: BeautifulSoup, url: str) -> dict:
    info = {"url": url, "name": None, "prefecture": None, "address": None, "phone": None}

    # [Fix 1] 店舗名: クラス名 shopinfo__heading で特定し、空のh1を取得しないようにする。
    # クラス名で見つからない場合のみ汎用h1にフォールバック。
    h1 = soup.find("h1", class_="shopinfo__heading")
    if not h1:
        h1 = soup.find("h1")
    if h1:
        name = h1.get_text(strip=True)
        if name:
            info["name"] = name

    # 住所・都道府県・電話: 〒 を含む要素
    for elem in soup.find_all(string=re.compile(r"〒")):
        text = elem.parent.get_text(" ", strip=True) if elem.parent else str(elem)
        info["address"] = text
        m = PREF_RE.search(text)
        if m:
            info["prefecture"] = m.group(1)
        break

    # 電話番号
    for elem in soup.find_all(string=re.compile(r"0\d{1,4}[-ー]\d{2,4}[-ー]\d{4}")):
        m = re.search(r"0\d{1,4}[-ー]\d{2,4}[-ー]\d{4}", str(elem))
        if m:
            info["phone"] = m.group(0)
            break

    return info


# ──────────────────────────────────────────────────────────
# 料金表の抽出（保守ポイント: サイト変更時はここを修正）
# ──────────────────────────────────────────────────────────
def extract_price_table(soup: BeautifulSoup) -> list[dict]:
    results: list[dict] = []

    # ── 料金表の見出しを探す ───────────────────────────────
    price_heading = None
    
    # 1. id="price" の div を探す（これが一番確実）
    price_section = soup.find("div", id="price")
    
    if price_section:
        # id="price" があれば、その中の h3 などを起点にする
        price_heading = price_section.find(["h2", "h3", "h4", "h5", "div"])
    else:
        # 念のため、imgのalt属性に「料金表」が含まれる見出しも探す（変更不可）
        for tag in soup.find_all(["h2", "h3", "h4", "h5"]):
            img = tag.find("img")
            if img and "料金表" in img.get("alt", ""):   # ← 変更不可
                price_heading = tag
                break
            elif "料金表" in tag.get_text():
                price_heading = tag
                break

    if not price_heading:
        log.warning("料金表セクションが見つかりませんでした")
        return results

    # ── 見出し以降〜次の大見出しまでのテーブルを収集 ──────
    tables: list = []
    for sibling in price_heading.find_next_siblings():
        if sibling.name in ["h2", "h3", "h4"] and any(
            kw in sibling.get_text()
            for kw in ["飲み放題コース", "フードメニュー", "機種", "求人"]
        ):
            break
        if sibling.name == "table":
            tables.append(sibling)
        else:
            tables.extend(sibling.find_all("table"))

    # ── 各テーブルを解析 ────────────────────────────────────
    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue

        # ヘッダー行
        headers = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        if not headers:
            continue

        section_name = headers[0]

        # 会員カラムのインデックス
        col_idx: dict[str, int | None] = {
            col: (headers.index(col) if col in headers else None)
            for col in MEMBER_COLS
        }
        # 会員カラムが1列も見つからない → 料金外テーブルをスキップ
        if not any(v is not None for v in col_idx.values()):
            continue

        current_plan: str | None = None

        for row in rows[1:]:
            cells = row.find_all(["th", "td"])
            texts = [c.get_text(strip=True) for c in cells]
            if len(texts) < 2 or not any(texts):
                continue

            col0 = texts[0]
            col1 = texts[1] if len(texts) > 1 else ""

            # ── プラン名 / 日タイプ 判定 ─────────────────
            # DAY_TYPE_RE に完全一致 → 日タイプ行（プラン名は前行を引き継ぐ）
            # それ以外 → 最初のセルがプラン名
            if col0 and not DAY_TYPE_RE.fullmatch(col0):
                # [Fix 2] 数字のみのセルはプラン名として扱わずスキップ
                # （colspan区切り行・注意書き行の誤解析を防ぐ）
                if NUMERIC_ONLY_RE.fullmatch(col0):
                    log.debug(f"数字のみのプラン名をスキップ: '{col0}'")
                    continue
                current_plan = col0
                plan_name    = col0
                day_type     = col1
            else:
                plan_name = current_plan or "不明"
                day_type  = col0 if DAY_TYPE_RE.fullmatch(col0) else col1

            if not plan_name or not day_type:
                continue

            # ── 価格抽出 ─────────────────────────────────
            prices: dict[str, int | None] = {}
            for col_name, idx in col_idx.items():
                if idx is None or idx >= len(texts):
                    prices[col_name] = None
                    continue
                raw = re.sub(r"[¥￥,\s]", "", texts[idx])
                m   = re.fullmatch(r"\d+", raw)
                prices[col_name] = int(m.group()) if m else None

            results.append({
                "section":  section_name,
                "plan":     re.sub(r"\u3000", " ", plan_name).strip(),
                "day_type": day_type,
                **prices,
            })

    return results

# ──────────────────────────────────────────────────────────
# Playwright で1店舗をスクレイプ（非同期）
# ──────────────────────────────────────────────────────────
async def scrape_shop_pw(page: Page, shop_id: str) -> dict:
    url = SHOP_URL_TEMPLATE.format(shop_id=shop_id)
    base = {
        "shop_id":    shop_id,
        "url":        url,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        # ページ遷移 —— networkidle でJS完了まで待機
        await page.goto(url, wait_until="networkidle", timeout=30_000)

        # 料金表テーブルが現れるまで待機（最大 PRICE_TABLE_TIMEOUT ms）
        try:
            await page.wait_for_selector("table", timeout=PRICE_TABLE_TIMEOUT)
        except PWTimeout:
            log.warning(f"[{shop_id}] table が時間内に描画されませんでした")

        # JS描画が完全に落ち着くまで追加で少し待つ
        await asyncio.sleep(1.5)

        html  = await page.content()
        soup  = BeautifulSoup(html, "lxml")
        basic = extract_basic_info(soup, url)
        prices = extract_price_table(soup)

        status = "OK" if prices else "PARSE_ERROR"
        log.info(
            f"[{shop_id}] {basic.get('name','?')} ({basic.get('prefecture','?')}) "
            f"→ {status} / {len(prices)} 行"
        )
        return {**base, "status": status, **basic, "price_table": prices}

    except Exception as e:
        log.error(f"[{shop_id}] スクレイプ失敗: {e}")
        return {**base, "status": "FETCH_ERROR", "price_table": []}


# ──────────────────────────────────────────────────────────
# メイン（非同期）
# ──────────────────────────────────────────────────────────
async def main_async(shop_list_hint: list[dict] | None, merge: bool = False):
    """
    shop_list_hint:
      - None          → Playwright でエリア一覧を巡回して全店舗URLを収集してから取得
      - list[dict]    → 収集済みリスト（--shop-ids 指定時）をそのまま使用
    merge:
      - False → 取得結果で data/shops_data.json を全置換（全件スクレイプ時）
      - True  → 取得結果を既存JSONに shop_id でマージ（特定店舗の再取得時。
                既存データを消さずに該当店舗だけ更新する）
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    all_data: list[dict] = []
    ok_count = error_count = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",           # GitHub Actions / Docker 環境で必要
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        ctx = await browser.new_context(
            user_agent=BROWSER_UA,
            locale="ja-JP",
            extra_http_headers={"Accept-Language": "ja-JP,ja;q=0.9"},
        )
        page = await ctx.new_page()

        # 画像・フォント・メディアをブロック（高速化・帯域節約）
        await page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "media", "font")
            else route.continue_(),
        )

        # ── フェーズ1: 全店舗URLの収集（--shop-ids 未指定時のみ） ──────
        if shop_list_hint is None:
            log.info("=== フェーズ1: 全店舗URL収集（Playwright） ===")
            shop_list = await collect_all_shop_urls(page)
            if not shop_list:
                log.error("店舗URLを1件も収集できませんでした。処理を中断します。")
                await browser.close()
                return []
        else:
            shop_list = shop_list_hint
            log.info(f"=== フェーズ1: スキップ（{len(shop_list)} 店舗を直接指定） ===")

        log.info(f"=== フェーズ2: 料金表取得（{len(shop_list)} 店舗） ===")

        for i, shop in enumerate(shop_list, 1):
            sid = shop["shop_id"]
            log.info(f"[{i}/{len(shop_list)}] 店舗ID: {sid}")

            record = await scrape_shop_pw(page, sid)
            all_data.append(record)

            if record["status"] == "OK":
                ok_count += 1
            else:
                error_count += 1

            # 途中保存（10件ごと）
            if i % 10 == 0:
                _save(all_data, merge=merge)
                log.info(f"  中間保存: {i} 件完了")

            # 次のリクエストまで待機
            if i < len(shop_list):
                await asyncio.sleep(SLEEP_SEC)

        await browser.close()

    saved_total = _save(all_data, merge=merge)
    log.info(
        f"\n{'='*55}\n"
        f"✅ 完了: 取得 {len(all_data)} 店舗"
        f"{f'（既存にマージ → 全 {saved_total} 店舗）' if merge else ''}\n"
        f"   OK: {ok_count} / エラー: {error_count}\n"
        f"   保存先: {OUTPUT_FILE}\n"
        f"{'='*55}"
    )
    return all_data


def _save(data: list[dict], merge: bool = False) -> int:
    """
    data を data/shops_data.json に保存する。
    merge=True かつ既存ファイルがある場合は shop_id 単位でマージし、
    今回取得分だけを上書き更新する（他店舗は温存）。
    戻り値は保存後の総店舗数。
    """
    OUTPUT_DIR.mkdir(exist_ok=True)

    if merge and OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception as e:
            log.warning(f"既存JSONの読み込みに失敗（マージをスキップし全置換）: {e}")
            existing = []
        by_id: dict[str, dict] = {s.get("shop_id"): s for s in existing}
        for rec in data:
            by_id[rec.get("shop_id")] = rec   # 今回取得分で上書き／追加
        out = sorted(by_id.values(), key=lambda x: str(x.get("shop_id")))
    else:
        out = data

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return len(out)


# ──────────────────────────────────────────────────────────
# エントリーポイント
# ──────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="ジャンカラ料金表スクレイパー（Playwright版）")
    parser.add_argument(
        "--shop-ids", nargs="*",
        help="特定の店舗IDのみ処理（例: --shop-ids 062 176）。"
             "この場合、結果は既存JSONに shop_id でマージされる（他店舗は温存）。"
    )
    parser.add_argument(
        "--no-merge", action="store_true",
        help="--shop-ids 指定時でもマージせず全置換する（通常は使わない。"
             "既存データを消すため注意）。"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="店舗URL収集のみ。料金取得はスキップ。"
    )
    args = parser.parse_args()

    # robots.txt チェック（プログラム起動時に1回）
    if not check_robots_once():
        raise SystemExit(1)

    # 対象店舗リストを決定
    # --shop-ids 指定時のみ事前にリストを組み立て、それ以外は main_async 内で収集
    if args.shop_ids:
        shop_list_hint: list[dict] | None = [
            {"shop_id": sid, "url": SHOP_URL_TEMPLATE.format(shop_id=sid)}
            for sid in args.shop_ids
        ]
        log.info(f"指定店舗のみ処理: {args.shop_ids}")
    else:
        shop_list_hint = None   # main_async 内で Playwright を使って収集

    if args.dry_run:
        # dry-run 時は URL 収集だけ行って表示（料金取得なし）
        async def _dry():
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage","--disable-gpu"])
                ctx  = await browser.new_context(user_agent=BROWSER_UA, locale="ja-JP")
                page = await ctx.new_page()
                result = await collect_all_shop_urls(page)
                await browser.close()
                return result
        collected = asyncio.run(_dry())
        log.info(f"[Dry Run] {len(collected)} 店舗を収集。料金取得はスキップ。")
        print(json.dumps(collected, ensure_ascii=False, indent=2))
        return

    # --shop-ids 指定時は既存JSONへマージ（全件スクレイプ時は全置換）
    merge = bool(args.shop_ids) and not args.no_merge
    asyncio.run(main_async(shop_list_hint, merge=merge))


if __name__ == "__main__":
    main()
# 🎤 ジャンカラ料金比較アプリ

全国のジャンカラ（カラオケ店）の料金を、エリア・会員区分・予算などで横断的に比較できる Web アプリです。
[jankara.ne.jp](https://jankara.ne.jp) の公開料金表を定期的にスクレイピングし、Streamlit で見やすく表示します。

## 主な機能

- **チップ＆カード型UI（既定）** — 選択中の条件をチップで常時表示し、結果を安い順のランキングカードで表示。スマホでも見やすいレスポンシブ設計。
- **一覧表モード（PC向け）** — ページ上部のトグルで従来の表形式に切替。全件をテーブルで一覧。
- **絞り込み** — 都道府県 / 時間帯 / プラン種別 / 曜日（複数選択、「すべて」と個別選択は排他）。
- **会員区分・予算** — 会員区分を選ぶと料金列が切り替わり、上限金額以下を安い順に表示。
- **サマリー** — 該当条件の最安・件数・平均をひと目で確認。

## 構成

```
[scraper.py]  ──>  data/shops_data.json  ──>  [app.py / Streamlit]
 取得＋パース           データ（中間成果物）           UI（閲覧・比較）
      ▲
 [scrape.yml] 週次でCI実行 → JSON を自動コミット
```

データは静的な JSON としてリポジトリに保存され、UI とスクレイパーはこの JSON を介して疎結合になっています。

## 技術スタック

| 層 | 使用技術 |
| --- | --- |
| UI | Streamlit, pandas |
| スクレイパー | Playwright（ヘッドレス Chromium）, BeautifulSoup, lxml |
| 自動化 | GitHub Actions（週次 cron） |
| ホスティング | Streamlit Community Cloud |

> 補足: 対象サイトは Next.js/React 製の SPA で、`requests` では料金表が取得できないため、JS 実行後の DOM を Playwright で取得しています。

## ローカルで動かす

### 1. UI（閲覧アプリ）

```bash
pip install -r requirements.txt
streamlit run app.py
```

ブラウザで http://localhost:8501 が開きます。`data/shops_data.json` があればそのまま表示されます。

### 2. スクレイパー（データ取得）

```bash
pip install -r requirements_scraper.txt
playwright install chromium

# 全店舗を取得
python scraper.py

# 特定店舗のみ取得
python scraper.py --shop-ids 062 176

# 店舗URLの収集のみ（料金取得はスキップ）
python scraper.py --dry-run
```

取得結果は `data/shops_data.json` に保存されます。

## 定期スクレイピング

[scrape.yml](scrape.yml) が以下を自動実行します（※ GitHub Actions で有効化するには `.github/workflows/` 配下への配置が必要）。

- **スケジュール**: 毎週月曜 AM2時 JST（cron `0 17 * * 0`）
- **手動実行**: GitHub の Actions タブから起動可能（店舗ID指定可）
- **処理**: 全店舗を取得 → `data/shops_data.json` を更新 → 変更があれば自動コミット＆プッシュ

倫理・保守方針として、起動時に robots.txt を確認し、リクエスト間にウェイトを挿入しています。

## デプロイ（Streamlit Community Cloud）

1. [share.streamlit.io](https://share.streamlit.io) に GitHub でログイン
2. 「Create app」→ リポジトリ・ブランチ `main`・メインファイル `app.py` を指定
3. Deploy

スクレイパーが新しいデータをコミットすると、その push を検知して Streamlit Cloud が自動で再デプロイ＝アプリのデータが自動更新されます。

## データ構造

`data/shops_data.json` は店舗オブジェクトの配列です。

```jsonc
{
  "shop_id": "062",
  "url": "https://jankara.ne.jp/shop/062/",
  "scraped_at": "2025-05-24T00:23:00+00:00",
  "status": "OK",                  // OK | PARSE_ERROR | FETCH_ERROR
  "name": "梅田芝田町店",
  "prefecture": "大阪府",
  "address": "...",
  "phone": "06-xxxx-xxxx",
  "price_table": [
    {
      "section": "昼ドリンク飲み放題",
      "plan": "昼フリータイム10時～19時",
      "day_type": "平日",            // 平日 / 土日祝 / 日祝 / 金土祝前 など
      "学生会員": 1620,              // 価格は整数 or null
      "学生": null,
      "会員": 1620,
      "シニア": 1620,
      "一般": 2390
    }
  ]
}
```

## ライセンス

[LICENSE](LICENSE) を参照してください。

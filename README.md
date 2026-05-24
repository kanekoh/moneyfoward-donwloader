# MoneyForward 資産管理スクリプト

- **資産推移 CSV のダウンロード** → Google Drive へ自動アップロード（`download_csv.py`）
- **手入力口座の時価更新** → Google Spreadsheet の保有量 × 実行時の株価・為替で MoneyForward を更新（`update_portfolio.py`）

## 必要なもの

- Python 3.11 以上
- Chromium（ARM 環境のみ: `sudo apt install -y chromium`）

## セットアップ

### 1. ライブラリのインストール

```bash
pip3 install playwright google-api-python-client google-auth-oauthlib
python3 -m playwright install chromium
```

ARM（Raspberry Pi）の場合：

```bash
pip3 install --break-system-packages playwright google-api-python-client google-auth-oauthlib
sudo apt install -y chromium libnspr4 libnss3 libasound2t64
```

### 2. 環境変数の設定

`.env.example` をコピーして編集：

```bash
cp .env.example .env
```

| 変数 | 説明 |
|------|------|
| `MF_EMAIL` | MoneyForward のメールアドレス |
| `MF_PASSWORD` | MoneyForward のパスワード |
| `GDRIVE_FOLDER_ID` | アップロード先の Google Drive フォルダ ID（フォルダ URL の末尾） |

### 3. 設定ファイルの作成

`config.example.json` をコピーして編集：

```bash
cp config.example.json config.json
```

`config.json` の主な設定項目：

**資産推移 CSV ダウンロード用グループ設定**

```json
"groups": [
  { "label": "no_group", "group_id": "0", "drive_filename": "資産推移_グループ選択なし.csv" },
  { "label": "my_group", "group_id": "YOUR_GROUP_ID_HASH", "drive_filename": "資産推移_マイグループ.csv" }
]
```

`group_id` は `./update.sh --list-groups` で確認できます。

**手入力口座の時価更新設定**

```json
"spreadsheet_id": "スプレッドシートのID（URLの /d/ と /edit の間）",
"sheet_gid": 0,
"manual_accounts": [
  { "mf_name": "Choice",   "type": "fx",       "currency": "AUD", "cell": "B2" },
  { "mf_name": "IBM",      "type": "stock_us",  "ticker": "IBM",   "cell": "C4" },
  { "mf_name": "金・銀・プラチナ", "type": "value_jpy",              "cell": "H7" }
]
```

| フィールド | 説明 |
|-----------|------|
| `mf_name` | MoneyForward のポートフォリオページに表示される口座名 |
| `type` | `fx`（外貨）/ `stock_us`（米国株）/ `value_jpy`（円額をそのまま使用） |
| `currency` | `fx` の場合の通貨コード（`AUD`, `USD` 等） |
| `ticker` | `stock_us` の場合のティッカーシンボル（`IBM`, `KD` 等） |
| `cell` | スプレッドシートのセルアドレス（保有数量 or 円額） |
| `sheet_gid` | スプレッドシートのシート ID（URL の `gid=` の値） |

株価・為替は Yahoo Finance から実行時に自動取得します（`fx`: `{通貨}JPY=X`、`stock_us`: ティッカー + `USDJPY=X`）。

### 4. Google 認証（初回のみ）

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. **Google Drive API** と **Google Sheets API** を有効化
3. OAuth2 認証情報（デスクトップアプリ）を作成し `credentials.json` として保存
4. OAuth 同意画面の「テストユーザー」に自分のメールアドレスを追加

初回実行時に認証 URL が表示されるのでブラウザで開いて認証します：

- `./update.sh` 初回実行 → `.gdrive_token.json` を生成（Drive 書き込み用）
- `python3 update_portfolio.py --dry-run` 初回実行 → `.sheets_token.json` を生成（Sheets 読み取り用）

## 使い方

すべての操作は `update.sh` から行います。

```bash
# 両方実行（デフォルト）: ポートフォリオ更新 → CSV ダウンロード
./update.sh

# ポートフォリオ更新のみ
./update.sh --portfolio-only

# CSV ダウンロードのみ
./update.sh --csv-only

# ポートフォリオの計算確認のみ（MoneyForward は更新しない）、CSV は通常実行
./update.sh --dry-run

# Drive へのアップロードをスキップ（CSV のみに適用）
./update.sh --no-upload

# MoneyForward セッションをリセットして再ログイン（両方に適用）
./update.sh --clear-session

# 利用可能なグループ一覧を表示（config.json の group_id 確認用）
./update.sh --list-groups
```

実行例（`./update.sh`）：

```
>>> ポートフォリオ更新
Google Spreadsheet から保有データを読み込んでいます...

市場価格・為替レートを取得しています（Yahoo Finance）...
  AUDJPY=X: 113.4510
  IBM: 253.8400
  KD: 12.2900
  USDJPY=X: 159.1550

計算結果:
  Choice: 7,249.10 AUD × 113.4510 = 822,418 円
  eSaver: 120,505.80 AUD × 113.4510 = 13,671,504 円
  IBM: 892.80 株 × $253.84 × ¥159.1550 = 36,069,035 円
  Kyndryl: 33.19 株 × $12.29 × ¥159.1550 = 64,920 円
  金・銀・プラチナ: 2,323,836 円

更新完了。

>>> CSV ダウンロード
...
```

## cron 設定例

```cron
# 毎日 8:00 にポートフォリオ更新 + CSV ダウンロード
0 8 * * * /home/hiroyuki/moneyforward/update.sh >> /home/hiroyuki/moneyforward/update.log 2>&1
```

## 外部化されている設定一覧

| 設定項目 | ファイル | 備考 |
|---------|---------|------|
| MoneyForward メール・パスワード | `.env` | Git 管理外 |
| Google Drive フォルダ ID | `.env` | Git 管理外 |
| グループ ID | `config.json` | Git 管理外 |
| スプレッドシート ID・シート ID | `config.json` | Git 管理外 |
| 口座名・通貨・ティッカー・セル位置 | `config.json` | Git 管理外 |

スクリプト本体（`download_csv.py`, `update_portfolio.py`）にユーザー固有の値は含まれていません。

## ファイル構成

```
.
├── download_csv.py       # 資産推移 CSV ダウンロード + Drive アップロード
├── update_portfolio.py   # 手入力口座の時価更新
├── update.sh             # cron 用ラッパースクリプト
├── config.example.json   # 設定テンプレート（Git 管理）
├── config.json           # 設定ファイル（Git 管理外・要作成）
├── .env.example          # 環境変数テンプレート（Git 管理）
├── .env                  # 環境変数（Git 管理外・要作成）
├── credentials.json      # Google OAuth2 認証情報（Git 管理外）
├── .mf_session.json      # MoneyForward セッション（Git 管理外）
├── .gdrive_token.json    # Google Drive トークン（Git 管理外）
├── .sheets_token.json    # Google Sheets トークン（Git 管理外）
└── downloads/            # ダウンロードした CSV（Git 管理外）
```

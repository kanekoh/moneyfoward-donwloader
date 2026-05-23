# MoneyForward 資産推移 CSV ダウンローダー

MoneyForward ME の資産推移ページから CSV をダウンロードし、Google Drive へ自動アップロードするスクリプト。

## 必要なもの

- Python 3.11 以上
- Chromium（ARM環境のみ: `sudo apt install -y chromium`）

## セットアップ

### 1. ライブラリのインストール

```bash
pip3 install playwright google-api-python-client google-auth-oauthlib
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

```env
MF_EMAIL=your-email@example.com
MF_PASSWORD=yourpassword
GDRIVE_FOLDER_ID=your-google-drive-folder-id
```

`GDRIVE_FOLDER_ID` は Google Drive のフォルダ URL に含まれる ID（`https://drive.google.com/drive/folders/<ここ>`）。

### 3. グループ設定

`config.example.json` をコピーして編集：

```bash
cp config.example.json config.json
```

`config.json` の `group_id` を実際の値に書き換えます。グループ ID は以下のコマンドで確認できます：

```bash
./update.sh --list-groups
```

実行するとログイン後にグループ一覧が表示されます：

```
利用可能なグループ一覧（config.json の group_id に貼り付けてください）:

  group_id: '0'                                         名前: グループ選択なし
  group_id: '3Gln_SsOc325l1U-RPYMfQ'                   名前: 資産管理用
  ...
```

表示された `group_id` を `config.json` の対応する項目に貼り付けてください。

### 4. Google Drive 認証（初回のみ）

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. Google Drive API を有効化
3. OAuth2 認証情報（デスクトップアプリ）を作成し `credentials.json` として保存
4. OAuth 同意画面の「テストユーザー」に自分のメールアドレスを追加

初回実行時にブラウザで認証を行うと `.gdrive_token.json` が生成され、以降は自動更新されます。

## 使い方

```bash
# 通常実行（ダウンロード + Google Drive アップロード）
./update.sh

# アップロードをスキップ
./update.sh --no-upload

# セッションをリセットして再ログイン
./update.sh --clear-session

# 利用可能なグループ一覧を表示（config.json 設定時に使用）
./update.sh --list-groups
```

## cron 設定例

```cron
# 毎日 8:00 に実行
0 8 * * * /home/hiroyuki/moneyforward/update.sh >> /home/hiroyuki/moneyforward/update.log 2>&1
```

## ダウンロード対象

`config.json` に定義したグループ分の CSV をダウンロードします。ローカルには最新5世代を保持し、古いファイルは自動削除されます。Google Drive 上のファイル名は `config.json` の `drive_filename` で指定します。

## ファイル構成

```
.
├── download_csv.py       # メインスクリプト
├── update.sh             # 実行シェルスクリプト
├── config.example.json   # グループ設定テンプレート（Git 管理）
├── config.json           # グループ設定（Git 管理外・要作成）
├── .env.example          # 環境変数テンプレート（Git 管理）
├── .env                  # 環境変数（Git 管理外・要作成）
├── credentials.json      # Google OAuth2 認証情報（Git 管理外）
├── .mf_session.json      # MoneyForward セッション（Git 管理外）
├── .gdrive_token.json    # Google Drive トークン（Git 管理外）
└── downloads/            # ダウンロードした CSV（Git 管理外）
```

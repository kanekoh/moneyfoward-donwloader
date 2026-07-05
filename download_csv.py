"""
MoneyForward 資産推移 CSV ダウンローダー + Google Drive アップロード

Usage:
    python3 download_csv.py [--output DIR] [--clear-session] [--no-upload]

環境変数:
    MF_EMAIL    MoneyForward のメールアドレス
    MF_PASSWORD MoneyForward のパスワード

Google Drive アップロードには credentials.json が必要（初回のみブラウザ認証）。
取得方法: https://console.cloud.google.com/ → Drive API 有効化 → OAuth2 デスクトップ認証情報を作成。

ラズパイ等 ARM 環境では Playwright 同梱の Chromium が使えないため、
システムの Chromium を自動検出して使用する。
"""

import argparse
import json
import os
import platform
import shutil
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

_print = print
def print(*args, **kwargs):
    _print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}]", *args, **kwargs)


LOGIN_URL = "https://moneyforward.com/sign_in"
HISTORY_URL = "https://moneyforward.com/bs/history"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
SESSION_FILE    = Path(__file__).parent / ".mf_session.json"
CONFIG_FILE     = Path(__file__).parent / "config.json"

# config.json を読み込む
def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"config.json が見つかりません: {CONFIG_FILE}", file=sys.stderr)
        sys.exit(1)
    return json.loads(CONFIG_FILE.read_text())

CONFIG = _load_config()

# ダウンロードするグループ: (ローカルファイル名サフィックス, group_id_hash の value, Drive固定ファイル名)
DOWNLOAD_GROUPS = [
    (g["label"], g["group_id"], g["drive_filename"])
    for g in CONFIG["groups"]
]
KEEP_GENERATIONS = CONFIG.get("keep_generations", 5)

# Google Drive
GDRIVE_FOLDER_ID   = os.environ.get("GDRIVE_FOLDER_ID", "")
GDRIVE_CREDENTIALS = Path(__file__).parent / "credentials.json"
GDRIVE_TOKEN       = Path(__file__).parent / ".gdrive_token.json"
GDRIVE_SCOPES      = ["https://www.googleapis.com/auth/drive.file"]


def _find_chromium() -> str | None:
    """ARM など Playwright 同梱 Chromium が使えない環境でシステム Chromium を探す。"""
    candidates = [
        "chromium-browser",
        "chromium",
        "google-chrome",
        "google-chrome-stable",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]
    for c in candidates:
        path = shutil.which(c) or (c if Path(c).exists() else None)
        if path:
            return path
    return None


def _make_context(pw, session_file: Path | None = None):
    is_arm = platform.machine().startswith(("aarch64", "armv"))
    launch_kwargs: dict = {"headless": True}

    if is_arm:
        exe = _find_chromium()
        if exe:
            print(f"ARM 環境: システム Chromium を使用します ({exe})")
            launch_kwargs["executable_path"] = exe
        else:
            print("警告: ARM 環境ですがシステム Chromium が見つかりませんでした。", file=sys.stderr)
            print("  sudo apt install chromium-browser  を実行してください。", file=sys.stderr)
            sys.exit(1)

    browser = pw.chromium.launch(**launch_kwargs)
    context_kwargs = dict(
        accept_downloads=True,
        user_agent=UA,
        viewport={"width": 1280, "height": 800},
        locale="ja-JP",
    )
    if session_file and session_file.exists():
        context_kwargs["storage_state"] = str(session_file)

    return browser, browser.new_context(**context_kwargs)


def _is_logged_in(page) -> bool:
    """moneyforward.com トップにアクセスして認証済みか確認する。"""
    try:
        page.goto("https://moneyforward.com/", wait_until="commit", timeout=15_000)
        page.wait_for_timeout(1000)
    except Exception:
        pass
    url = page.url
    return "moneyforward.com" in url and "id.moneyforward.com" not in url


def _login(page, email: str, password: str) -> None:
    """ログインフローを実行する（2FA 対応）。"""
    print("ログインページを開いています...")
    page.goto(LOGIN_URL, wait_until="networkidle")
    print(f"  [URL] {page.url}")

    page.fill('input[name="mfid_user[email]"]', email)
    page.keyboard.press("Enter")
    try:
        page.wait_for_selector('input[name="mfid_user[password]"]', state="visible", timeout=15_000)
    except PlaywrightTimeout:
        page.screenshot(path="/tmp/mf_debug_after_email.png")
        print("メール入力後にパスワードフィールドが表示されませんでした。", file=sys.stderr)
        sys.exit(1)

    page.fill('input[name="mfid_user[password]"]', password)
    page.keyboard.press("Enter")
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PlaywrightTimeout:
        pass
    print(f"  [URL after password] {page.url}")

    if "email_otp" in page.url or "two_factor" in page.url:
        if not sys.stdin.isatty():
            print(
                "エラー: 2段階認証が必要ですが非対話モード（cron 等）で実行されています。\n"
                "セッションを更新するには手動で ./update.sh --clear-session を実行してください。",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"2段階認証が必要です。{email} に届いた6桁のコードを入力してください。")
        code = input("認証コード: ").strip()
        page.fill('input[name="email_otp"]', code)
        page.keyboard.press("Enter")
        try:
            page.wait_for_url("https://moneyforward.com/**", timeout=30_000)
        except PlaywrightTimeout:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except PlaywrightTimeout:
            pass
        print(f"  [URL after OTP] {page.url}")

    if "id.moneyforward.com" in page.url:
        page.screenshot(path="/tmp/mf_debug_login_fail.png")
        print(f"  [URL] {page.url}")
        print("ログインに失敗しました。/tmp/mf_debug_login_fail.png を確認してください。", file=sys.stderr)
        sys.exit(1)

    print("ログイン完了。")


def _download_group_csv(page, group_label: str, group_value: str, output_dir: Path) -> Path:
    """指定グループを選択してCSVをダウンロードする。"""
    print(f"\n[{group_label}] グループを選択しています...")

    # グループを選択（onchange で自動フォーム送信）
    try:
        with page.expect_navigation(wait_until="commit", timeout=15_000):
            page.select_option('#group_id_hash', value=group_value)
    except PlaywrightTimeout:
        pass

    # JS レンダリング待ち
    page.wait_for_timeout(5000)

    # 「ダウンロード」ドロップダウンを開く
    try:
        page.locator('a:has-text("ダウンロード")').first.click(timeout=10_000)
        page.wait_for_timeout(500)
    except PlaywrightTimeout:
        pass

    # CSVリンクをクリック
    csv_link = page.locator('a[href*="/csv"]').first
    try:
        csv_link.wait_for(state="visible", timeout=10_000)
    except PlaywrightTimeout:
        screenshot = output_dir / f"debug_{group_label}.png"
        page.screenshot(path=str(screenshot), full_page=True)
        print(f"  CSV リンクが見つかりませんでした。{screenshot} を確認してください。", file=sys.stderr)
        return None

    print(f"  CSV をダウンロードしています...")
    with page.expect_download(timeout=60_000) as dl_info:
        csv_link.click()

    download = dl_info.value
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = output_dir / f"moneyforward_bs_history_{group_label}_{timestamp}.csv"
    download.save_as(str(dest))
    print(f"  保存完了: {dest}")

    _rotate_files(output_dir, group_label)
    return dest


def _rotate_files(output_dir: Path, group_label: str, keep: int = KEEP_GENERATIONS) -> None:
    """同グループの古いファイルを削除して最新 keep 世代だけ残す。"""
    files = sorted(output_dir.glob(f"moneyforward_bs_history_{group_label}_*.csv"))
    for old in files[:-keep]:
        old.unlink()
        print(f"  削除（世代管理）: {old.name}")


def download_csv(output_dir: Path, clear_session: bool = False) -> list[Path]:
    if clear_session and SESSION_FILE.exists():
        SESSION_FILE.unlink()
        print("セッションをクリアしました。")

    output_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        if SESSION_FILE.exists():
            print("保存済みセッションを読み込んでいます...")
            browser, context = _make_context(pw, SESSION_FILE)
            page = context.new_page()

            if not _is_logged_in(page):
                print("セッションが期限切れです。再ログインします...")
                browser.close()
                browser, context = _make_context(pw)
                page = context.new_page()
                email = os.environ.get("MF_EMAIL") or input("MoneyForward メールアドレス: ").strip()
                password = os.environ.get("MF_PASSWORD") or __import__("getpass").getpass("パスワード: ")
                _login(page, email, password)
                context.storage_state(path=str(SESSION_FILE))
                print(f"新しいセッションを保存しました: {SESSION_FILE}")
            else:
                print("セッション有効。")
        else:
            browser, context = _make_context(pw)
            page = context.new_page()
            email = os.environ.get("MF_EMAIL") or input("MoneyForward メールアドレス: ").strip()
            password = os.environ.get("MF_PASSWORD") or __import__("getpass").getpass("パスワード: ")
            _login(page, email, password)
            context.storage_state(path=str(SESSION_FILE))
            print(f"セッションを保存しました: {SESSION_FILE}")

        # 資産推移ページへ移動
        print("\n資産推移ページへ移動しています...")
        try:
            page.goto(HISTORY_URL, wait_until="commit", timeout=30_000)
        except Exception:
            pass
        page.wait_for_timeout(5000)

        # グループごとにCSVをダウンロード
        results = []
        for label, value, drive_title in DOWNLOAD_GROUPS:
            dest = _download_group_csv(page, label, value, output_dir)
            if dest:
                results.append((dest, drive_title))

        # グループ選択をデフォルト（グループ選択なし）に戻す
        try:
            with page.expect_navigation(wait_until="commit", timeout=15_000):
                page.select_option('#group_id_hash', value='0')
            print("\nグループ選択をリセットしました。")
        except Exception:
            pass

        browser.close()

    print(f"\n完了: {len(results)} ファイルをダウンロードしました。")
    return results


# ---------------------------------------------------------------------------
# Google Drive アップロード
# ---------------------------------------------------------------------------

def _gdrive_service():
    """OAuth2 認証済みの Drive サービスを返す。初回はブラウザ認証が開く。"""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if GDRIVE_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(GDRIVE_TOKEN), GDRIVE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not GDRIVE_CREDENTIALS.exists():
                print(f"credentials.json が見つかりません: {GDRIVE_CREDENTIALS}", file=sys.stderr)
                print("Google Cloud Console で OAuth2 認証情報を作成し、credentials.json として保存してください。", file=sys.stderr)
                sys.exit(1)
            if not sys.stdin.isatty():
                print(
                    "エラー: Google Drive の認証が必要ですが非対話モード（cron 等）で実行されています。\n"
                    ".gdrive_token.json を削除して手動で ./update.sh を実行し、再認証してください。",
                    file=sys.stderr,
                )
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(GDRIVE_CREDENTIALS), GDRIVE_SCOPES
            )
            # ブラウザを自動で開かず URL を表示して待つ（WSL・ラズパイ対応）
            creds = flow.run_local_server(
                port=0,
                open_browser=False,
                timeout_seconds=300,
            )
        GDRIVE_TOKEN.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


def upload_to_gdrive(csv_paths: list[tuple[Path, str]]) -> None:
    """CSV ファイルを Google Drive の指定フォルダへアップロード（上書き）する。

    csv_paths: [(ローカルパス, Drive上の固定ファイル名), ...]
    """
    from googleapiclient.http import MediaFileUpload

    if not GDRIVE_FOLDER_ID:
        print("GDRIVE_FOLDER_ID が設定されていません。.env を確認してください。", file=sys.stderr)
        sys.exit(1)

    print("\nGoogle Drive へアップロードしています...")
    service = _gdrive_service()

    for path, title in csv_paths:
        if not path or not path.exists():
            continue

        # Shift-JIS → UTF-8 変換して一時ファイルを作成
        import tempfile, subprocess
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="wb") as tmp:
            result = subprocess.run(
                ["iconv", "-f", "sjis", "-t", "utf8", str(path)],
                capture_output=True
            )
            tmp.write(result.stdout)
            tmp_path = tmp.name

        # 同名ファイルが既に存在するか確認
        existing = service.files().list(
            q=f"name='{title}' and '{GDRIVE_FOLDER_ID}' in parents and trashed=false",
            fields="files(id,name)"
        ).execute().get("files", [])

        media = MediaFileUpload(tmp_path, mimetype="text/csv", resumable=False)

        if existing:
            # 上書き更新
            service.files().update(
                fileId=existing[0]["id"],
                media_body=media
            ).execute()
            print(f"  更新: {title}")
        else:
            # 新規作成
            service.files().create(
                body={"name": title, "parents": [GDRIVE_FOLDER_ID]},
                media_body=media,
                fields="id"
            ).execute()
            print(f"  作成: {title}")

        Path(tmp_path).unlink(missing_ok=True)

    print("アップロード完了。")


def list_groups() -> None:
    """ログイン後、利用可能なグループ一覧と group_id を表示する。"""
    email = os.environ.get("MF_EMAIL") or input("MoneyForward メールアドレス: ").strip()
    password = os.environ.get("MF_PASSWORD") or __import__("getpass").getpass("パスワード: ")

    with sync_playwright() as pw:
        browser, context = _make_context(pw, SESSION_FILE if SESSION_FILE.exists() else None)
        page = context.new_page()

        if not _is_logged_in(page):
            _login(page, email, password)
            context.storage_state(path=str(SESSION_FILE))

        try:
            page.goto(HISTORY_URL, wait_until="commit", timeout=30_000)
        except Exception:
            pass
        page.wait_for_timeout(5000)

        print("\n利用可能なグループ一覧（config.json の group_id に貼り付けてください）:\n")
        options = page.locator('#group_id_hash option').all()
        for opt in options:
            value = opt.get_attribute("value")
            text  = opt.inner_text().strip()
            if value not in ("", "create_group"):
                print(f"  group_id: {value!r:45s} 名前: {text}")

        browser.close()


def main():
    parser = argparse.ArgumentParser(description="MoneyForward 資産推移 CSV ダウンローダー")
    parser.add_argument("--output", "-o", default="./downloads", help="保存先ディレクトリ (デフォルト: ./downloads)")
    parser.add_argument("--clear-session", action="store_true", help="保存済みセッションを削除して再ログインする")
    parser.add_argument("--no-upload", action="store_true", help="Google Drive へのアップロードをスキップする")
    parser.add_argument("--list-groups", action="store_true", help="利用可能なグループ一覧と group_id を表示して終了する")
    args = parser.parse_args()

    if args.list_groups:
        list_groups()
        return

    paths = download_csv(Path(args.output), clear_session=args.clear_session)

    if not args.no_upload:
        upload_to_gdrive(paths)


if __name__ == "__main__":
    main()

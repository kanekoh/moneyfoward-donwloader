"""
MoneyForward の手入力口座を Google Spreadsheet の保有量と
実行時の市場価格をもとに時価更新する。

Usage:
    python3 update_portfolio.py [--dry-run] [--clear-session]

config.json の "spreadsheet_id", "sheet_gid", "manual_accounts" を参照。
Google 認証: credentials.json が必要（初回のみブラウザ認証 → .sheets_token.json に保存）。
"""

import argparse
import datetime
import json
import os
import platform
import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

_print = print
def print(*args, **kwargs):
    _print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}]", *args, **kwargs)


PORTFOLIO_URL       = "https://moneyforward.com/bs/portfolio"
LOGIN_URL           = "https://moneyforward.com/sign_in"
SESSION_FILE        = Path(__file__).parent / ".mf_session.json"
CONFIG_FILE         = Path(__file__).parent / "config.json"
SHEETS_TOKEN        = Path(__file__).parent / ".sheets_token.json"
GDRIVE_CREDENTIALS  = Path(__file__).parent / "credentials.json"
SHEETS_SCOPES       = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


# ---------------------------------------------------------------------------
# 設定読み込み
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    if not CONFIG_FILE.exists():
        sys.exit(f"config.json が見つかりません: {CONFIG_FILE}")
    return json.loads(CONFIG_FILE.read_text())


# ---------------------------------------------------------------------------
# Google Sheets 読み込み
# ---------------------------------------------------------------------------

def _sheets_creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if SHEETS_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(SHEETS_TOKEN), SHEETS_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not GDRIVE_CREDENTIALS.exists():
                sys.exit("credentials.json が見つかりません。Google Cloud Console からダウンロードしてください。")
            if not sys.stdin.isatty():
                print(
                    "エラー: Google Sheets の認証が必要ですが非対話モード（cron 等）で実行されています。\n"
                    ".sheets_token.json を削除して手動で python3 update_portfolio.py --dry-run を実行し、再認証してください。",
                    file=sys.stderr,
                )
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(GDRIVE_CREDENTIALS), SHEETS_SCOPES)
            creds = flow.run_local_server(port=0, open_browser=False, timeout_seconds=300)
        SHEETS_TOKEN.write_text(creds.to_json())

    return creds


def _read_cells(spreadsheet_id: str, sheet_gid: int, cells: list[str]) -> dict[str, float]:
    """指定セルの値を一括取得する。戻り値: {cell_address: float}"""
    from googleapiclient.discovery import build

    creds = _sheets_creds()
    svc = build("sheets", "v4", credentials=creds)

    # GID → シート名
    meta = svc.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_name = next(
        s["properties"]["title"]
        for s in meta["sheets"]
        if s["properties"]["sheetId"] == sheet_gid
    )

    ranges = [f"'{sheet_name}'!{cell}" for cell in cells]
    result = svc.spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id,
        ranges=ranges,
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute()

    values: dict[str, float] = {}
    for i, cell in enumerate(cells):
        try:
            raw = result["valueRanges"][i]["values"][0][0]
            values[cell] = float(str(raw).replace(",", ""))
        except (KeyError, IndexError, TypeError, ValueError):
            print(f"  警告: セル {cell} の値を読み取れませんでした", file=sys.stderr)
            values[cell] = 0.0
    return values


# ---------------------------------------------------------------------------
# 市場価格取得（yfinance）
# ---------------------------------------------------------------------------

def _get_yahoo_price(symbol: str) -> float | None:
    """Yahoo Finance REST API から現在値を取得する。yfinance 非依存。"""
    import requests

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
    headers = {"User-Agent": UA}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        result = r.json().get("chart", {}).get("result")
        if result:
            return float(result[0]["meta"]["regularMarketPrice"])
    except Exception:
        pass
    return None


def _fetch_prices(accounts: list[dict]) -> dict[str, float]:
    symbols: set[str] = set()
    for acc in accounts:
        if acc["type"] == "fx":
            symbols.add(f"{acc['currency']}JPY=X")
        elif acc["type"] == "stock_us":
            symbols.add(acc["ticker"])
            symbols.add("USDJPY=X")

    prices: dict[str, float] = {}
    for sym in symbols:
        price = _get_yahoo_price(sym)
        if price is not None:
            prices[sym] = price
            print(f"  {sym}: {price:.4f}")
        else:
            print(f"  警告: {sym} の価格を取得できませんでした", file=sys.stderr)
    return prices


def _calc_jpy(acc: dict, quantity: float, prices: dict[str, float]) -> int:
    t = acc["type"]
    if t == "value_jpy":
        return round(quantity)
    if t == "fx":
        rate = prices.get(f"{acc['currency']}JPY=X", 0.0)
        return round(quantity * rate)
    if t == "stock_us":
        price_usd = prices.get(acc["ticker"], 0.0)
        usdjpy    = prices.get("USDJPY=X", 0.0)
        return round(quantity * price_usd * usdjpy)
    return 0


# ---------------------------------------------------------------------------
# MoneyForward ログイン
# ---------------------------------------------------------------------------

def _find_chromium() -> str | None:
    for c in ["chromium-browser", "chromium", "google-chrome", "/usr/bin/chromium"]:
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    return None


def _make_browser(pw):
    is_arm = platform.machine().startswith(("aarch64", "armv"))
    kw: dict = {"headless": True}
    if is_arm:
        exe = _find_chromium()
        if not exe:
            sys.exit("ARM 環境: システム Chromium が見つかりません。sudo apt install chromium を実行してください。")
        kw["executable_path"] = exe
    return pw.chromium.launch(**kw)


def _login(page, email: str, password: str) -> None:
    print("ログインしています...")
    page.goto(LOGIN_URL, wait_until="networkidle")
    page.fill('input[name="mfid_user[email]"]', email)
    page.keyboard.press("Enter")
    try:
        page.wait_for_selector('input[name="mfid_user[password]"]', state="visible", timeout=15_000)
    except PlaywrightTimeout:
        sys.exit("パスワードフィールドが見つかりません。ログインページの構造が変わった可能性があります。")
    page.fill('input[name="mfid_user[password]"]', password)
    page.keyboard.press("Enter")
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except PlaywrightTimeout:
        pass
    if "email_otp" in page.url or "two_factor" in page.url:
        if not sys.stdin.isatty():
            print(
                "エラー: 2段階認証が必要ですが非対話モード（cron 等）で実行されています。\n"
                "セッションを更新するには手動で ./update.sh --clear-session を実行してください。",
                file=sys.stderr,
            )
            sys.exit(1)
        email_addr = os.environ.get("MF_EMAIL", "")
        print(f"2段階認証コードを {email_addr} に送信しました。")
        code = input("認証コード: ").strip()
        page.fill('input[name="email_otp"]', code)
        page.keyboard.press("Enter")
        try:
            page.wait_for_url("https://moneyforward.com/**", timeout=30_000)
        except PlaywrightTimeout:
            pass
    if "id.moneyforward.com" in page.url:
        sys.exit("ログインに失敗しました。メールアドレス・パスワードを確認してください。")
    print("ログイン完了。")


def _ensure_logged_in(page, context, email: str, password: str) -> None:
    page.goto("https://moneyforward.com/", wait_until="commit", timeout=15_000)
    page.wait_for_timeout(1000)
    if "id.moneyforward.com" in page.url:
        _login(page, email, password)
        context.storage_state(path=str(SESSION_FILE))


# ---------------------------------------------------------------------------
# MoneyForward 口座更新
# ---------------------------------------------------------------------------

def _update_account(page, mf_name: str, jpy_value: int) -> bool:
    """Bootstrap モーダルを使わず JS でフォームを直接 submit して値を更新する。"""
    # ページ内のモーダルを name フィールドで探し、値をセットして submit
    result = page.evaluate(f"""
        (() => {{
            const target = {json.dumps(mf_name)};
            for (const modal of document.querySelectorAll('div.modal[id^="modal_asset"]')) {{
                const nameInput = modal.querySelector('input[name="user_asset_det[name]"]');
                if (!nameInput || nameInput.value.trim() !== target) continue;
                const form    = modal.querySelector('form');
                const valInput = modal.querySelector('input[name="user_asset_det[value]"]');
                if (!form || !valInput) return 'no_form';
                valInput.value = String({jpy_value});
                form.submit();
                return 'ok';
            }}
            return 'not_found';
        }})()
    """)

    if result != 'ok':
        print(f"  ✗ {mf_name}: モーダルが見つかりません（{result}）", file=sys.stderr)
        return False

    # form.submit() によるページ遷移を待つ
    try:
        page.wait_for_url("**/bs/portfolio**", wait_until="commit", timeout=15_000)
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(3_000)

    print(f"  ✓ {mf_name}: {jpy_value:,} 円")
    return True


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def update_portfolio(dry_run: bool = False, clear_session: bool = False) -> None:
    config   = _load_config()
    accounts = config.get("manual_accounts", [])
    if not accounts:
        sys.exit("config.json に manual_accounts が設定されていません。")

    spreadsheet_id = config.get("spreadsheet_id", "")
    sheet_gid      = int(config.get("sheet_gid", 0))
    if not spreadsheet_id:
        sys.exit("config.json に spreadsheet_id が設定されていません。")

    # ① Spreadsheet から保有量を読み込む
    print("Google Spreadsheet から保有データを読み込んでいます...")
    cells = [acc["cell"] for acc in accounts]
    cell_values = _read_cells(spreadsheet_id, sheet_gid, cells)

    # ② 市場価格を取得
    print("\n市場価格・為替レートを取得しています（Yahoo Finance）...")
    prices = _fetch_prices(accounts)

    # ③ 円換算額を計算して表示
    print("\n計算結果:")
    updates: list[tuple[str, int]] = []
    for acc in accounts:
        qty = cell_values.get(acc["cell"], 0.0)
        jpy = _calc_jpy(acc, qty, prices)
        t   = acc["type"]
        if t == "fx":
            rate   = prices.get(f"{acc['currency']}JPY=X", 0.0)
            detail = f"{qty:,.2f} {acc['currency']} × {rate:.4f} = "
        elif t == "stock_us":
            price  = prices.get(acc["ticker"], 0.0)
            usdjpy = prices.get("USDJPY=X", 0.0)
            detail = f"{qty:.2f} 株 × ${price:.2f} × ¥{usdjpy:.4f} = "
        else:
            detail = ""
        print(f"  {acc['mf_name']}: {detail}{jpy:,} 円")
        updates.append((acc["mf_name"], jpy))

    if dry_run:
        print("\n--dry-run: MoneyForward の更新はスキップします。")
        return

    # ④ MoneyForward を更新
    print("\nMoneyForward を更新しています...")
    if clear_session and SESSION_FILE.exists():
        SESSION_FILE.unlink()
        print("セッションをクリアしました。")

    email    = os.environ.get("MF_EMAIL")    or input("MF_EMAIL: ")
    password = os.environ.get("MF_PASSWORD") or __import__("getpass").getpass("MF_PASSWORD: ")

    with sync_playwright() as pw:
        browser = _make_browser(pw)
        ctx_kw  = dict(
            accept_downloads=True,
            user_agent=UA,
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
        )
        if SESSION_FILE.exists():
            ctx_kw["storage_state"] = str(SESSION_FILE)

        context = browser.new_context(**ctx_kw)
        page    = context.new_page()

        _ensure_logged_in(page, context, email, password)

        # ポートフォリオページへ
        try:
            page.goto(PORTFOLIO_URL, wait_until="commit", timeout=30_000)
        except Exception:
            pass
        page.wait_for_timeout(5_000)

        # 各口座を更新（送信後のページリロードに任せて次の口座へ）
        for mf_name, jpy in updates:
            # 更新後にポートフォリオページに戻っているか確認
            if PORTFOLIO_URL not in page.url:
                try:
                    page.goto(PORTFOLIO_URL, wait_until="commit", timeout=30_000)
                except Exception:
                    pass
                page.wait_for_timeout(5_000)

            _update_account(page, mf_name, jpy)

        browser.close()

    print("\n更新完了。")


def main():
    parser = argparse.ArgumentParser(description="MoneyForward 手入力口座を Spreadsheet 値で時価更新")
    parser.add_argument("--dry-run", action="store_true", help="計算結果の表示のみ（MoneyForward は更新しない）")
    parser.add_argument("--clear-session", action="store_true", help="保存済みセッションを削除して再ログインする")
    args = parser.parse_args()
    update_portfolio(dry_run=args.dry_run, clear_session=args.clear_session)


if __name__ == "__main__":
    main()

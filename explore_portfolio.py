"""
MoneyForward ポートフォリオページの構造を調査するスクリプト。
実行すると:
  - スクリーンショット: /tmp/mf_portfolio.png
  - 「変更」ボタン周辺の HTML ダンプ: /tmp/mf_portfolio_dump.txt
"""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# update.sh 経由 or `set -a && source .env && set +a` で実行すること

SESSION_FILE = Path(__file__).parent / ".mf_session.json"
LOGIN_URL    = "https://moneyforward.com/sign_in"
PORTFOLIO_URL = "https://moneyforward.com/bs/portfolio"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def main():
    email    = os.environ.get("MF_EMAIL")    or input("MF_EMAIL: ")
    password = os.environ.get("MF_PASSWORD") or __import__("getpass").getpass("MF_PASSWORD: ")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx_kwargs = dict(accept_downloads=True, user_agent=UA,
                          viewport={"width": 1280, "height": 900}, locale="ja-JP")
        if SESSION_FILE.exists():
            ctx_kwargs["storage_state"] = str(SESSION_FILE)

        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        # ログイン確認
        page.goto("https://moneyforward.com/", wait_until="commit", timeout=15_000)
        page.wait_for_timeout(1000)
        if "id.moneyforward.com" in page.url:
            print("セッションなし or 期限切れ → ログインします")
            page.goto(LOGIN_URL, wait_until="networkidle")
            page.fill('input[name="mfid_user[email]"]', email)
            page.keyboard.press("Enter")
            page.wait_for_selector('input[name="mfid_user[password]"]', timeout=15_000)
            page.fill('input[name="mfid_user[password]"]', password)
            page.keyboard.press("Enter")
            page.wait_for_load_state("networkidle", timeout=30_000)
            if "email_otp" in page.url or "two_factor" in page.url:
                code = input("OTPコード: ").strip()
                page.fill('input[name="email_otp"]', code)
                page.keyboard.press("Enter")
                page.wait_for_url("https://moneyforward.com/**", timeout=30_000)
            context.storage_state(path=str(SESSION_FILE))
            print("ログイン完了")

        # ポートフォリオページへ
        print("ポートフォリオページを開いています...")
        try:
            page.goto(PORTFOLIO_URL, wait_until="commit", timeout=30_000)
        except Exception:
            pass
        page.wait_for_timeout(5000)

        # スクリーンショット
        page.screenshot(path="/tmp/mf_portfolio.png", full_page=True)
        print("スクリーンショット: /tmp/mf_portfolio.png")

        dump_lines = []

        # ページ内の全ボタン・リンクのテキストとhrefを列挙
        dump_lines.append("=== ページ内のボタン・リンク一覧 ===")
        all_btns = page.evaluate("""
            () => {
                const els = document.querySelectorAll('a, button, input[type=submit], input[type=button]');
                return Array.from(els).map(el => ({
                    tag:  el.tagName,
                    text: el.innerText?.trim().slice(0, 80) || el.value?.trim() || '',
                    href: el.href || '',
                    cls:  el.className?.slice(0, 60) || ''
                })).filter(e => e.text || e.href);
            }
        """)
        for b in all_btns:
            dump_lines.append(f"  [{b['tag']}] text={b['text']!r:30s} href={b['href'][:60]}  cls={b['cls']}")

        # 全フォームの構造
        dump_lines.append("\n\n=== ページ内のフォーム構造 ===")
        forms_html = page.evaluate("""
            () => Array.from(document.querySelectorAll('form')).map(f => f.outerHTML).join('\\n---\\n')
        """)
        dump_lines.append(forms_html[:8000])

        # テーブルの先頭行 HTML（口座リスト構造を把握）
        dump_lines.append("\n\n=== テーブル先頭セクション ===")
        tables_html = page.evaluate("""
            () => Array.from(document.querySelectorAll('table, .assets-list'))
                    .slice(0, 5)
                    .map(t => t.outerHTML.slice(0, 2000))
                    .join('\\n---\\n')
        """)
        dump_lines.append(tables_html)

        # ダンプ保存
        dump_text = "\n".join(dump_lines)
        Path("/tmp/mf_portfolio_dump.txt").write_text(dump_text, encoding="utf-8")
        print("HTML ダンプ: /tmp/mf_portfolio_dump.txt")

        browser.close()


if __name__ == "__main__":
    main()

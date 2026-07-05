#!/bin/bash
# デフォルト: ポートフォリオ更新 → CSV ダウンロードの順に両方実行
#
# オプション:
#   --portfolio-only  ポートフォリオ更新のみ
#   --csv-only        CSV ダウンロードのみ
#   --dry-run         ポートフォリオの計算結果を表示するだけ（MF 更新なし）、CSV は通常実行
#   --no-upload       CSV ダウンロードを Drive へアップロードしない
#   --clear-session   MoneyForward のセッションをリセット（両方に適用）
#   --list-groups     利用可能なグループ一覧を表示して終了

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

set -a
source "$SCRIPT_DIR/.env"
set +a

cd "$SCRIPT_DIR"

# ---------------------------------------------------------------------------
# Slack 通知
# ---------------------------------------------------------------------------
slack_notify() {
    local msg="$1"
    if [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
        curl -s -X POST -H 'Content-type: application/json' \
            --data "{\"text\": \"$msg\"}" \
            "$SLACK_WEBHOOK_URL" > /dev/null || true
    fi
}

# エラー時に Slack 通知を送って終了
on_error() {
    local exit_code=$?
    local line=$1
    slack_notify ":x: *MoneyForward 更新失敗*
ホスト: $(hostname)
日時: $(date '+%Y-%m-%d %H:%M:%S')
終了コード: ${exit_code}（スクリプト行 ${line}）
対処: \`./update.sh --clear-session\` で再認証、またはログを確認してください。"
}
trap 'on_error $LINENO' ERR

# ---------------------------------------------------------------------------
# 引数解析
# ---------------------------------------------------------------------------
RUN_PORTFOLIO=true
RUN_CSV=true
PORTFOLIO_ARGS=()
CSV_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --portfolio-only)
            RUN_CSV=false
            ;;
        --csv-only)
            RUN_PORTFOLIO=false
            ;;
        --dry-run)
            PORTFOLIO_ARGS+=(--dry-run)
            ;;
        --no-upload)
            CSV_ARGS+=(--no-upload)
            ;;
        --clear-session)
            PORTFOLIO_ARGS+=(--clear-session)
            CSV_ARGS+=(--clear-session)
            ;;
        --list-groups)
            exec python3 "$SCRIPT_DIR/download_csv.py" --list-groups
            ;;
        *)
            echo "不明なオプション: $arg" >&2
            echo "使い方: $0 [--portfolio-only|--csv-only] [--dry-run] [--no-upload] [--clear-session] [--list-groups]" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------
ts() { date '+%Y-%m-%d %H:%M:%S'; }

echo "[$(ts)] === update.sh 実行開始 ==="

if $RUN_PORTFOLIO; then
    echo "[$(ts)] >>> ポートフォリオ更新"
    python3 "$SCRIPT_DIR/update_portfolio.py" "${PORTFOLIO_ARGS[@]+"${PORTFOLIO_ARGS[@]}"}"
fi

if $RUN_CSV; then
    echo "[$(ts)] >>> CSV ダウンロード"
    python3 "$SCRIPT_DIR/download_csv.py" "${CSV_ARGS[@]+"${CSV_ARGS[@]}"}"
fi

echo "[$(ts)] === update.sh 実行終了 ==="

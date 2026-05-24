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

if $RUN_PORTFOLIO; then
    echo ">>> ポートフォリオ更新"
    python3 "$SCRIPT_DIR/update_portfolio.py" "${PORTFOLIO_ARGS[@]+"${PORTFOLIO_ARGS[@]}"}"
fi

if $RUN_CSV; then
    echo ">>> CSV ダウンロード"
    python3 "$SCRIPT_DIR/download_csv.py" "${CSV_ARGS[@]+"${CSV_ARGS[@]}"}"
fi

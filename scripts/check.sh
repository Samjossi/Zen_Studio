#!/usr/bin/env bash
# scripts/check.sh
# 提交前门禁唯一入口（work plans/2026-0909-2244_测试门禁落地计划.md T4）：
# 隐私扫描（最便宜、最先失败）+ ruff（E/F/W，vendored 已排除）+ pytest tests/（offscreen）。
# 退出码即门禁结果。
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 隐私扫描 =="
bash scripts/privacy_scan.sh

uv run ruff check
QT_QPA_PLATFORM=offscreen uv run pytest tests/ -q

#!/usr/bin/env bash
# scripts/check.sh
# 提交前门禁唯一入口：隐私扫描（最便宜、最先失败）+ 协议回归 + 补充验证。
# smoke_gguf 需真实模型与 llama-server，不入门禁，按需手动跑。
# 退出码即门禁结果。
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 隐私扫描 =="
bash scripts/privacy_scan.sh

echo "== 协议回归 =="
.venv/bin/python tools/spike_handshake.py

echo "== 补充验证 =="
.venv/bin/python tools/verify_extras.py

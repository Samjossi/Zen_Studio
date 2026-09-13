#!/usr/bin/env bash
# 隐私扫描唯一实现：提交门禁与 pre-commit 钩子共用，规则只维护这一份，
# 否则两处规则漂移后总有一处先失效。规则只增不减；误伤用豁免解决，豁免必须写明 why。
# 依据：协议和指南/隐私脱敏与扫描阻断通用协议_v2.0.md §五-3（该目录整体不入库，协议文本仅存本机）
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { echo "🔴 $1" >&2; exit 1; }

# 身份防线：真实邮箱会随每条提交永久写入历史且事后只能重写历史清除，
# 所以仓库级身份必须存在且为匿名邮箱
email=$(git config --local user.email || true)
[ -n "$email" ] || fail "未配置仓库级 git 身份，先执行 git config user.name/user.email（匿名）"
if echo "$email" | grep -qEi '@(outlook|qq|163|gmail|hotmail|126|139|sina|foxmail|aliyun)\.(com|net|cn)$'; then
  fail "仓库级 git 邮箱疑似真实个人邮箱（$email），改为匿名邮箱后再提交"
fi

# 内容防线：扫描 tracked 文件中的内网地址/本机绝对路径/真实邮箱域名。
# 只扫 tracked 文件的原因：本机层（*.local.md/.env）本就不入库，扫工作区会误伤。
# 协议文档本身携带真实形态示例才有指导/验证价值，按文件名豁免，禁止扩大豁免面；
# （本仓库 协议和指南/ 整体不入库，该豁免为协议原样保留，规则只增不减）
# /home/xxx、/home/user 是占位符而非真实值。
if git ls-files -z | xargs -0 grep -InE '(192\.168\.[0-9]+\.[0-9]+|10\.[0-9]+\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+|/home/[a-z0-9_-]+|C:\\Users\\|@(outlook|qq|163|gmail|hotmail|126|139|sina|foxmail|aliyun)\.com)' 2>/dev/null \
  | grep -vE '隐私脱敏与扫描阻断通用协议' \
  | grep -vE '/home/(xxx|user)([^a-z0-9_-]|$)'; then
  fail "命中疑似隐私内容（见上），按协议 §二 脱敏约定脱敏后再提交"
fi

echo "隐私扫描通过 ✔"

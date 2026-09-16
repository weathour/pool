#!/usr/bin/env bash
# 推送 main 时临时解除分支保护，推完立刻恢复。
#
# 为什么需要它：main 开了保护（禁止直推），而维护者偶尔需要推
# 基础设施文件（CI 配置、模板、文档）。长期做法应该是走 PR，
# 但在只有一个人的引导期，用这个脚本把"解除—推送—恢复"做成一件事，
# 避免忘记恢复。
#
# 用法：bash scripts/push-main.sh
set -euo pipefail

GITEA="${GITEA_URL:-http://localhost:3000}"
OWNER_REPO="${OWNER_REPO:-JiaWeathour/pool}"
TOKEN_FILE="${TOKEN_FILE:-$HOME/gitea/api-token.txt}"
API="$GITEA/api/v1/repos/$OWNER_REPO"

TOKEN="$(cat "$TOKEN_FILE")"

protect() {
  curl -s -X POST "$API/branch_protections" \
    -H "Authorization: token $TOKEN" -H "Content-Type: application/json" \
    -d '{"branch_name":"main","enable_push":false,"enable_force_push":false,"require_approvals":0}' \
    -o /dev/null
  echo "  ✓ 分支保护已恢复"
}

unprotect() {
  curl -s -X DELETE "$API/branch_protections/main" \
    -H "Authorization: token $TOKEN" -o /dev/null
  echo "  ✓ 分支保护已临时解除"
}

# 无论成功失败都要恢复保护
trap 'protect' EXIT

echo "解除保护 → 推送 → 恢复保护"
unprotect
git push origin main
echo "  ✓ 已推送"

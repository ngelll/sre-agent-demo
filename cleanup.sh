#!/usr/bin/env bash
# ===== SRE Agent Demo cleanup script =====
# 用法: bash /root/.openclaw/workspace/tmp/sre-demo/cleanup.sh
set -e

echo "============================================="
echo " SRE Agent Demo - Cleanup"
echo "============================================="
echo ""
read -p "确认要清理所有 demo 资源吗？[y/N] " CONFIRM
if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
  echo "取消。"; exit 0
fi

source /root/.openclaw/credentials/sre-demo-sp.env

echo ""
echo "[1/4] 删除 Azure 资源组 (含 AKS + ACR + LAW + AppInsights + SRE Agent)..."
az group delete -n rg-sre-agent-demo --yes --no-wait
echo "  → 已发起（后台 3-10 分钟完成）。用下面命令查进度："
echo "  az group show -n rg-sre-agent-demo --query properties.provisioningState -o tsv 2>&1 || echo GONE"

echo ""
echo "[2/4] 清理本地临时文件..."
rm -rf /root/.openclaw/workspace/tmp/sre-demo/
echo "  → 已删 /root/.openclaw/workspace/tmp/sre-demo/"

echo ""
echo "[3/4] 清理本地凭据文件..."
rm -f /root/.openclaw/credentials/sre-demo-sp.env
rm -f /root/.openclaw/credentials/sre-demo-github.env
echo "  → 已删 sre-demo-sp.env / sre-demo-github.env"

echo ""
echo "============================================="
echo " ✅ 自动清理完成。以下 3 项请你手动收尾："
echo "============================================="
echo ""
echo " 1. GitHub PAT 撤销:"
echo "    → 打开 https://github.com/settings/tokens"
echo "    → 找 sre-agent-demo-jijimao → Delete"
echo ""
echo " 2. Service Principal 删除 (可选):"
echo "    → Portal → Entra ID → App registrations"
echo "    → 找 sp-sre-agent-demo-jijimao → 删除"
echo ""
echo " 3. GitHub repo 删除 (可选，public repo 留着也 OK):"
echo "    → 打开 https://github.com/ngelll/sre-agent-demo/settings"
echo "    → 拉到最底 → Delete this repository"
echo ""
echo "全部搞完就等于「demo 从没发生过」。"

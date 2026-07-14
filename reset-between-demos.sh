#!/usr/bin/env bash
# ===== SRE Agent Demo - Reset between客户场次 =====
# 用途：一场 demo 演完后，把环境完全还原到"未演过"的样子。
# 用法: bash /root/.openclaw/workspace/tmp/sre-demo/reset-between-demos.sh

set -e

echo "============================================="
echo " SRE Agent Demo - Reset Between Sessions"
echo "============================================="
echo ""

echo "[1/4] 把 web-app-a 强制重启（清 gunicorn worker 状态）..."
kubectl -n sre-demo rollout restart deployment/web-app-a
echo "  → 触发 rolling restart"

echo ""
echo "[2/4] 把 web-app-b 强制重启（释放 memhog 吃住的内存 + 停掉可能还在跑的 CPU 线程）..."
kubectl -n sre-demo rollout restart deployment/web-app-b
echo "  → 触发 rolling restart"

echo ""
echo "[3/4] 把 web-app-b 副本数强制回到 1（如果 HPA 或 agent 之前 scale 上去了）..."
kubectl -n sre-demo scale deployment/web-app-b --replicas=1
echo "  → replicas=1"

echo ""
echo "[4/4] 等待 pods Ready..."
kubectl -n sre-demo wait --for=condition=Ready pod -l 'app in (web-app-a,web-app-b)' --timeout=90s | tail -5

echo ""
echo "===== 当前状态 ====="
kubectl -n sre-demo get pods,hpa

echo ""
echo "===== 冒烟测试 ====="
IP=$(cat /root/.openclaw/workspace/tmp/sre-demo/external_ip)
echo "external IP: $IP"
echo "--- /a/health ---"
curl -sS http://$IP/a/health && echo ""
echo "--- /b/health ---"
curl -sS http://$IP/b/health && echo ""

echo ""
echo "✅ 环境已重置。可以开始下一场 demo。"
echo ""
echo "💡 提示：Application Insights / SRE Agent 里的"
echo "   历史事件不需要清（也清不了）——那些是过往记录，"
echo "   新一场的 5xx 会作为新事件独立触发，不受影响。"

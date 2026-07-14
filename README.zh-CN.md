# SRE Agent Demo（中文）

> **English**: [README.md](./README.md) · **中文**: 本文档
>
> 一套跑在 AKS 上的双 App 演示环境，用来展示 **Azure SRE Agent**（2025 年 5 月 Preview，7 月 GA）如何自动分诊真实故障。
> 一条 `bash deploy.sh` 从零到 demo 可跑，约 10 分钟，成本约 $2 USD/天。

---

## 这个 Demo 演什么

两个 Flask 应用跑在 AKS 上，前面挂 ingress-nginx，接 Application Insights。每个应用都有几个"故意坏"的接口。你调它们，Azure SRE Agent 会自动接收告警、关联日志，并且（GA 版本可以）在 GitHub 上开一个带根因分析的 Issue。

| 应用 | 角色 | 故障触发接口 |
|---|---|---|
| **web-app-a** | 演示电商 / 下单 | `/break`（500）、`/crash`（未捕获异常）、`/slow`（3 秒延迟）、`/api/checkout`（可通过 `/api/admin/payment` 切换健康状态） |
| **web-app-b** | 报表生成器 | `/leak`（CPU 打满，触发 HPA 扩容）、`/memhog`（每次 +200MB 内存）、`/api/report/monthly`（重负载报表） |

**场景 A —— 5xx 洪水**
```bash
for i in {1..50}; do curl -s http://$INGRESS_IP/a/break; done
```
→ Application Insights 告警在 3-5 分钟内触发。SRE Agent 关联到 `ORDER_PROCESSING_FAILED` 日志模式，写一个事件报告。

**场景 B —— CPU 尖刺 + 自动扩容**
```bash
for i in {1..10}; do curl -s http://$INGRESS_IP/b/leak; done
```
→ Pod CPU 打到接近 100%。HPA 触发，把 `web-app-b` 从 1 个扩到最多 4 个 Pod。SRE Agent 标记扩容事件并指出循环卡死的代码位置。

---

## 前置依赖

| 项 | 用途 | 怎么装 |
|---|---|---|
| Azure 订阅 | 所有资源都建在里面 | 你已经有 |
| **Owner** 或 **Contributor + User Access Admin** 权限 | AKS `--attach-acr` 需要建角色分配 | 用 `az role assignment list --assignee $(az ad signed-in-user show --query id -o tsv) --scope /subscriptions/$SUB` 检查 |
| `az` CLI ≥ 2.60 | 建所有 Azure 资源 | https://learn.microsoft.com/cli/azure/install-azure-cli |
| `kubectl` ≥ 1.28 | 部署 K8s 清单 | `az aks install-cli` |
| `helm` ≥ 3.14（可选） | 更干净地装 ingress-nginx；没装脚本会自动 fallback 到 raw manifest | https://helm.sh/docs/intro/install/ |
| Bash 4+ | 跑脚本 | 任何 Linux / macOS / WSL |

**不需要**本地装 Docker —— 镜像构建走 `az acr build` 在 ACR 云端跑。

---

## 快速部署（10 分钟一键版）

```bash
git clone https://github.com/ngelll/sre-agent-demo.git
cd sre-agent-demo

# 1. 登录 Azure
az login
az account set --subscription "<你的订阅ID>"

# 2. 建 .env 文件
cp .env.example .env
#    编辑 .env：
#      - AZURE_SUBSCRIPTION_ID   ← 必填
#      - AZURE_TENANT_ID         ← 必填
#      - 其他可以先用默认值，也可以自己改名

source .env

# 3. 一键部署
bash deploy.sh
```

跑完脚本会打印一个 ingress IP。浏览器打开 `http://<IP>/a/` 和 `http://<IP>/b/`，应该能看到两个 demo UI。

脚本是**幂等**的：重复跑会自动跳过已存在的资源，只重新构建/部署应用。

---

## deploy.sh 会创建什么

```
资源组：rg-sre-agent-demo (eastus2)
├── Azure Container Registry (Basic)
├── Log Analytics Workspace
├── Application Insights（关联到 LAW）
└── AKS (1 × Standard_B2s，Managed Identity，开启 monitoring 插件)
    └── ingress-nginx（helm 或 raw manifest 兜底）
    └── namespace/sre-demo
        ├── secret/appinsights          （AppInsights 连接字符串）
        ├── deploy/web-app-a + svc      （电商下单）
        ├── deploy/web-app-b + svc      （报表）  ← 用 web-app-b-sa 服务账号
        ├── hpa/web-app-b-hpa           （CPU 60% 触发，1→4 副本）
        ├── role + rolebinding          （pod-reader，给 web-app-b 用）
        └── ingress/sre-demo-ingress    （路径 /a 和 /b）
```

---

## 第 4 步：挂上 Azure SRE Agent（手工，约 2 分钟）

SRE Agent 目前只能在 Portal 里配置。`deploy.sh` 跑完后：

1. Portal → 搜 **"Azure SRE Agent"** → **Create**
2. Scope 选：资源组 `rg-sre-agent-demo`
3. 把 Agent 的 Managed Identity 授权：
   - `Reader` 角色 on RG
   - `Log Analytics Reader` on LAW
4. （可选）配 GitHub App 集成 → 指到你希望它开 Issue 的 repo
5. 在 App Insights → **Alerts** → 建一条规则：`requests/failed > 20 in 5 min` → 通知目标选 SRE Agent

配好之后触发场景 A，就能看到它自动干活了。

---

## 手工部署（如果你想每条命令自己敲一遍）

一键脚本本质就是把下面这些命令串起来。每一步都幂等，可以放心重复跑。

<details>
<summary>点开看完整手工步骤</summary>

```bash
source .env

# --- 基础资源 ---
az group create -n $AZURE_RG -l $AZURE_LOCATION
az acr create -n $ACR_NAME -g $AZURE_RG --sku Basic
az monitor log-analytics workspace create -g $AZURE_RG -n $LAW_NAME -l $AZURE_LOCATION
LAW_ID=$(az monitor log-analytics workspace show -g $AZURE_RG -n $LAW_NAME --query id -o tsv)
az monitor app-insights component create -g $AZURE_RG --app $APPI_NAME -l $AZURE_LOCATION --workspace $LAW_ID --kind web
APPI_CS=$(az monitor app-insights component show -g $AZURE_RG --app $APPI_NAME --query connectionString -o tsv)

# --- AKS ---
az aks create -g $AZURE_RG -n $AKS_NAME -l $AZURE_LOCATION \
  --node-count 1 --node-vm-size Standard_B2s \
  --enable-managed-identity --attach-acr $ACR_NAME \
  --enable-addons monitoring --workspace-resource-id $LAW_ID \
  --generate-ssh-keys
az aks get-credentials -g $AZURE_RG -n $AKS_NAME --overwrite-existing

# --- ingress-nginx (helm) ---
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update
helm install ingress-nginx ingress-nginx/ingress-nginx \
  -n ingress-nginx --create-namespace \
  --set controller.service.type=LoadBalancer

# --- 在 ACR 构建镜像 ---
az acr build --registry $ACR_NAME --image web-app-a:v1 apps/web-app-a
az acr build --registry $ACR_NAME --image web-app-b:v1 apps/web-app-b

# --- 部署到 K8s ---
ACR_LOGIN_SERVER=$(az acr show -n $ACR_NAME -g $AZURE_RG --query loginServer -o tsv)
kubectl create ns sre-demo
kubectl -n sre-demo create secret generic appinsights \
  --from-literal=APPLICATIONINSIGHTS_CONNECTION_STRING="$APPI_CS"
sed "s|ACR_LOGIN_SERVER|$ACR_LOGIN_SERVER|g" k8s/deploy.yaml | kubectl apply -f -
kubectl apply -f k8s/web-app-b-rbac.yaml
kubectl -n sre-demo set serviceaccount deployment/web-app-b web-app-b-sa

# --- 拿到公网 IP ---
kubectl -n ingress-nginx get svc ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
```

</details>

---

## 验证部署成功

```bash
INGRESS_IP=$(kubectl -n ingress-nginx get svc ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# 健康检查
curl http://$INGRESS_IP/a/health   # -> {"status":"healthy","app":"web-app-a"}
curl http://$INGRESS_IP/b/health   # -> {"status":"healthy","app":"web-app-b","pod":"..."}

# 触发场景 A
for i in {1..50}; do curl -s -o /dev/null -w "%{http_code}\n" http://$INGRESS_IP/a/break; done

# 触发场景 B（一边看 pod 扩容）
for i in {1..10}; do curl -s http://$INGRESS_IP/b/leak; done &
kubectl -n sre-demo get hpa -w
```

---

## 两场 Demo 之间重置

Pod 会保留状态（memhog 吃住的内存 / CPU 线程会一直烧到 pod 重启）。
`reset-between-demos.sh` 会重启两个 Deployment，把 `web-app-b` 副本数强制拉回 1。

```bash
bash reset-between-demos.sh
```

---

## 清理

所有东西都在一个资源组里，一条命令清完。`cleanup.sh` 会加一个确认提示。

```bash
bash cleanup.sh
# 或者直接：
az group delete -n rg-sre-agent-demo --yes --no-wait
```

另外需要手工处理的：
- **GitHub PAT**（如果你为 CI 建过）：https://github.com/settings/tokens
- **Service Principal**（如果你为自动化建过）：Portal → Entra ID → App registrations

---

## 仓库结构

```
.
├── README.md              ← English
├── README.zh-CN.md        ← 中文（本文档）
├── .env.example           ← 复制成 .env 填变量
├── .gitignore
├── deploy.sh              ← 一键部署（幂等）
├── cleanup.sh             ← 确认后销毁整个 RG
├── reset-between-demos.sh ← 重启 pod、副本回 1
├── apps/
│   ├── web-app-a/         ← Flask + gunicorn + AppInsights OpenTelemetry
│   │   ├── app.py
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   └── static/index.html
│   └── web-app-b/         ← 同上，多了 kubernetes 客户端（给 /api/cluster 用）
└── k8s/
    ├── deploy.yaml           ← 命名空间、Deployment、Service、HPA、Ingress
    └── web-app-b-rbac.yaml   ← ServiceAccount + Role + RoleBinding（列 pod）
```

---

## 踩过的坑（先看这里少走弯路）

1. **`az aks create --attach-acr` 需要订阅的 User Access Admin 权限**。你账号如果没有，要么让 owner 单独跑这一句，要么建完 AKS 后手工 `az role assignment create --role AcrPull --assignee <aks-kubelet-identity> --scope <acr-id>` 补上。
2. **Ingress 公网 IP 分配要 2-5 分钟**。`deploy.sh` 会轮询最多 5 分钟。超时不代表失败，用 `kubectl -n ingress-nginx get svc` 再看一眼就有。
3. **web-app-b 必须挂 `serviceaccount: web-app-b-sa`**，否则 `/api/cluster` 会因为没有 pod list 权限直接失败。`deploy.sh` 在 apply 之后会自动 set；手工部署时记得补 `kubectl -n sre-demo set serviceaccount deployment/web-app-b web-app-b-sa`。
4. **App Insights 数据入库有约 3 分钟延迟**。场景 A 如果看起来没触发，稍等一下 —— 告警规则要有足够的数据点。
5. **SRE Agent 目前是 Preview**（截至 2025-07）。GA 定价模型已公开（按 token 计费，费率对齐 GPT-4 Turbo），但 Preview 期间免费。给客户报价前先查一下最新状态。

---

## 成本

跑一整天：**约 $2 USD/day**（大头是 AKS 工作节点 + ACR + LAW/AppInsights 数据入库费）。
挂满 30 天：**约 $63 USD/月**。
跑完 `cleanup.sh` 后归零。

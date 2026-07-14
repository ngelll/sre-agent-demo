# SRE Agent Demo

> Two-app AKS environment that demonstrates **Azure SRE Agent** (Preview, GA July 2025) auto-triaging real incidents.
> One `bash deploy.sh` from zero to a running demo, ~10 minutes, ~$2/day.

---

## What this demo shows

Two Flask apps run on AKS behind ingress-nginx, wired to Application Insights.
Each app has "break-me" endpoints. When you hit them, Azure SRE Agent picks up the alerts, correlates logs, and (in GA) can open a GitHub issue with a root-cause analysis.

| App | Role | Break endpoints |
|---|---|---|
| **web-app-a** | Demo shop / checkout | `/break` (500), `/crash` (unhandled), `/slow` (3s), `/api/checkout` (toggleable via `/api/admin/payment`) |
| **web-app-b** | Report generator | `/leak` (CPU spike, triggers HPA), `/memhog` (200MB/hit), `/api/report/monthly` (heavy) |

**Scenario A — 5xx surge**
```bash
for i in {1..50}; do curl -s http://$INGRESS_IP/a/break; done
```
→ App Insights alert fires (3-5 min). SRE Agent correlates the `ORDER_PROCESSING_FAILED` log pattern and files an incident.

**Scenario B — CPU spike + auto-scale**
```bash
for i in {1..10}; do curl -s http://$INGRESS_IP/b/leak; done
```
→ Pod CPU pins ~100%. HPA scales `web-app-b` from 1 → up to 4 pods. SRE Agent flags the scale event and points at the loop.
\n---

## Prerequisites

| What | Why | How to get it |
|---|---|---|
| Azure subscription | Everything runs there | You already have one |
| **Owner** or **Contributor + User Access Admin** on the sub | AKS `--attach-acr` needs role assignment | `az role assignment list --assignee $(az ad signed-in-user show --query id -o tsv) --scope /subscriptions/$SUB` |
| `az` CLI ≥ 2.60 | All infra ops | https://learn.microsoft.com/cli/azure/install-azure-cli |
| `kubectl` ≥ 1.28 | Deploy manifests | `az aks install-cli` |
| `helm` ≥ 3.14 (optional) | Cleaner ingress-nginx install | https://helm.sh/docs/intro/install/ |
| Bash 4+ | Run the scripts | Any Linux / macOS / WSL |

You do **not** need Docker locally — image builds run in ACR via `az acr build`.

---

## Quick start (10 minutes, one-shot)

```bash
git clone https://github.com/ngelll/sre-agent-demo.git
cd sre-agent-demo

# 1. Log into Azure
az login
az account set --subscription "<your-sub-id>"

# 2. Create your env file
cp .env.example .env
#    Edit .env:
#      - AZURE_SUBSCRIPTION_ID   ← required
#      - AZURE_TENANT_ID         ← required
#      - (leave the rest as defaults, or rename freely)

source .env

# 3. Deploy everything
bash deploy.sh
```

When it finishes, the script prints the ingress IP. Open `http://<IP>/a/` and `http://<IP>/b/` — you should see the two demo UIs.

Deploy is **idempotent**: re-running skips resources that already exist and only re-builds/redeploys the apps.

---

## What deploy.sh creates

```
Resource Group: rg-sre-agent-demo (eastus2)
├── Azure Container Registry (Basic)
├── Log Analytics Workspace
├── Application Insights (workspace-based)
└── AKS (1 × Standard_B2s, managed identity, monitoring add-on)
    └── ingress-nginx (helm or raw manifest fallback)
    └── ns/sre-demo
        ├── secret/appinsights          (connection string)
        ├── deploy/web-app-a + svc      (checkout)
        ├── deploy/web-app-b + svc      (reports)  ← runs as web-app-b-sa
        ├── hpa/web-app-b-hpa           (CPU 60%, 1→4 replicas)
        ├── role + rolebinding          (pod-reader for web-app-b)
        └── ingress/sre-demo-ingress    (paths /a and /b)
```

---

## Step 4: Attach Azure SRE Agent (manual, ~2 min)

SRE Agent is Portal-only for now. After `deploy.sh` finishes:

1. Portal → search **"Azure SRE Agent"** → **Create**
2. Scope: resource group `rg-sre-agent-demo`
3. Grant the Agent's managed identity `Reader` on the RG + `Log Analytics Reader` on the LAW
4. (Optional) GitHub App integration → point at a repo where you want issues filed
5. In App Insights → **Alerts** → create a rule on `requests/failed` > 20 in 5 min → route to SRE Agent

Then trigger Scenario A above and watch it work.

---

## Manual deploy (if you'd rather run each command yourself)

The one-shot script wraps roughly these commands. Every step is idempotent, so you can freely re-run.

<details>
<summary>Click to expand — full manual sequence</summary>

```bash
source .env

# --- Foundation ---
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

# --- Build images in ACR ---
az acr build --registry $ACR_NAME --image web-app-a:v1 apps/web-app-a
az acr build --registry $ACR_NAME --image web-app-b:v1 apps/web-app-b

# --- Deploy ---
ACR_LOGIN_SERVER=$(az acr show -n $ACR_NAME -g $AZURE_RG --query loginServer -o tsv)
kubectl create ns sre-demo
kubectl -n sre-demo create secret generic appinsights \
  --from-literal=APPLICATIONINSIGHTS_CONNECTION_STRING="$APPI_CS"
sed "s|ACR_LOGIN_SERVER|$ACR_LOGIN_SERVER|g" k8s/deploy.yaml | kubectl apply -f -
kubectl apply -f k8s/web-app-b-rbac.yaml
kubectl -n sre-demo set serviceaccount deployment/web-app-b web-app-b-sa

# --- Get IP ---
kubectl -n ingress-nginx get svc ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
```

</details>

---

## Verify it works

```bash
INGRESS_IP=$(kubectl -n ingress-nginx get svc ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Health checks
curl http://$INGRESS_IP/a/health   # -> {"status":"healthy","app":"web-app-a"}
curl http://$INGRESS_IP/b/health   # -> {"status":"healthy","app":"web-app-b","pod":"..."}

# Trigger Scenario A
for i in {1..50}; do curl -s -o /dev/null -w "%{http_code}\n" http://$INGRESS_IP/a/break; done

# Trigger Scenario B (watch pods scale)
for i in {1..10}; do curl -s http://$INGRESS_IP/b/leak; done &
kubectl -n sre-demo get hpa -w
```

---

## Between demo sessions

Pods keep state (memhog holds memory, CPU threads keep burning until pod restart).
`reset-between-demos.sh` restarts both deployments and pins `web-app-b` back to `replicas=1`.

```bash
bash reset-between-demos.sh
```

---

## Cleanup

Everything lives in one RG, so cleanup is one command. `cleanup.sh` wraps it with a confirm prompt.

```bash
bash cleanup.sh
# or equivalently:
az group delete -n rg-sre-agent-demo --yes --no-wait
```

Also manually:
- **GitHub PAT** (if you created one for CI): https://github.com/settings/tokens
- **Service Principal** (if you created one for automation): Portal → Entra ID → App registrations

---

## Repo layout

```
.
├── README.md              ← you're reading it
├── .env.example           ← copy to .env, fill in
├── .gitignore
├── deploy.sh              ← one-shot deploy (idempotent)
├── cleanup.sh             ← confirm + tear down RG
├── reset-between-demos.sh ← restart pods, reset replicas
├── apps/
│   ├── web-app-a/         ← Flask + gunicorn + App Insights OpenTelemetry
│   │   ├── app.py
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   └── static/index.html
│   └── web-app-b/         ← same, plus kubernetes client for /api/cluster
└── k8s/
    ├── deploy.yaml           ← namespace, deployments, services, HPA, ingress
    └── web-app-b-rbac.yaml   ← ServiceAccount + Role + RoleBinding (pod list)
```

---

## Known gotchas

1. **`az aks create --attach-acr` needs User Access Admin** on the sub. If your account doesn't have it, either ask an owner to run just that line, or `az role assignment create --role AcrPull --assignee <aks-kubelet-identity> --scope <acr-id>` after the fact.
2. **Ingress IP can take 2-5 minutes** to be assigned. `deploy.sh` polls for up to 5 min. If it times out, the deploy still succeeded — just re-check with `kubectl -n ingress-nginx get svc`.
3. **web-app-b needs `serviceaccount: web-app-b-sa`** to list pods for the `/api/cluster` endpoint. `deploy.sh` sets this after apply; if you apply manually, remember `kubectl -n sre-demo set serviceaccount deployment/web-app-b web-app-b-sa`.
4. **App Insights ingestion has ~3 min lag.** If Scenario A doesn't seem to trigger, wait a bit — the alert rule needs enough data points.
5. **SRE Agent is Preview** as of Jul 2025. GA pricing model is announced (token-based, ~GPT-4 Turbo rates) but Preview is free. Check the current status before quoting numbers to customers.

---

## Cost

Running 24h: **~$2 USD/day** (mostly AKS worker node + ACR + LAW/AppInsights ingestion).
Full month: **~$63 USD** if you leave it up.
Zero cost once you run `cleanup.sh`.

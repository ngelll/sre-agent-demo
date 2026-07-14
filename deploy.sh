#!/usr/bin/env bash
# ==============================================================
# SRE Agent Demo — one-shot deploy script (idempotent)
# ==============================================================
# Prereqs (see README.md for details):
#   - az CLI logged in (`az login`) with Owner/Contributor on subscription
#   - kubectl installed
#   - docker (or podman) running locally  ← used only if you want local build.
#     Default path uses `az acr build` which builds in ACR (no local docker needed).
#   - .env copied from .env.example and filled in, then `source .env`
#
# Usage:
#   cp .env.example .env
#   # edit .env: fill AZURE_SUBSCRIPTION_ID + AZURE_TENANT_ID (rest have defaults)
#   source .env
#   bash deploy.sh
#
# Cost: ~$2/day while running. Run cleanup.sh when done.
# ==============================================================
set -euo pipefail

# ---------- guard: env loaded? ----------
: "${AZURE_SUBSCRIPTION_ID:?run: cp .env.example .env; edit; source .env}"
: "${AZURE_TENANT_ID:?missing AZURE_TENANT_ID in .env}"
: "${AZURE_RG:?missing AZURE_RG}"
: "${AZURE_LOCATION:?missing AZURE_LOCATION}"
: "${ACR_NAME:?missing ACR_NAME}"
: "${AKS_NAME:?missing AKS_NAME}"
: "${LAW_NAME:?missing LAW_NAME}"
: "${APPI_NAME:?missing APPI_NAME}"
: "${K8S_NAMESPACE:=sre-demo}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "======================================================"
echo " SRE Agent Demo — deploy"
echo "  Sub:      $AZURE_SUBSCRIPTION_ID"
echo "  RG:       $AZURE_RG ($AZURE_LOCATION)"
echo "  ACR:      $ACR_NAME"
echo "  AKS:      $AKS_NAME"
echo "  LAW:      $LAW_NAME"
echo "  AppInsi:  $APPI_NAME"
echo "======================================================"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"

# ---------- 1. Resource Group ----------
echo ""
echo "[1/8] Resource Group ..."
az group create -n "$AZURE_RG" -l "$AZURE_LOCATION" -o none
echo "  OK"

# ---------- 2. Azure Container Registry ----------
echo ""
echo "[2/8] Azure Container Registry ..."
if ! az acr show -n "$ACR_NAME" -g "$AZURE_RG" -o none 2>/dev/null; then
  az acr create -n "$ACR_NAME" -g "$AZURE_RG" --sku Basic --admin-enabled false -o none
fi
ACR_LOGIN_SERVER="$(az acr show -n "$ACR_NAME" -g "$AZURE_RG" --query loginServer -o tsv)"
echo "  login server: $ACR_LOGIN_SERVER"

# ---------- 3. Log Analytics Workspace ----------
echo ""
echo "[3/8] Log Analytics Workspace ..."
if ! az monitor log-analytics workspace show -g "$AZURE_RG" -n "$LAW_NAME" -o none 2>/dev/null; then
  az monitor log-analytics workspace create -g "$AZURE_RG" -n "$LAW_NAME" -l "$AZURE_LOCATION" -o none
fi
LAW_ID="$(az monitor log-analytics workspace show -g "$AZURE_RG" -n "$LAW_NAME" --query id -o tsv)"

# ---------- 4. Application Insights (workspace-based) ----------
echo ""
echo "[4/8] Application Insights ..."
if ! az monitor app-insights component show -g "$AZURE_RG" --app "$APPI_NAME" -o none 2>/dev/null; then
  az monitor app-insights component create \
    -g "$AZURE_RG" --app "$APPI_NAME" -l "$AZURE_LOCATION" \
    --workspace "$LAW_ID" --kind web -o none
fi
APPI_CS="$(az monitor app-insights component show \
  -g "$AZURE_RG" --app "$APPI_NAME" --query connectionString -o tsv)"

# ---------- 5. AKS ----------
echo ""
echo "[5/8] AKS (this can take ~5 min on first run) ..."
if ! az aks show -g "$AZURE_RG" -n "$AKS_NAME" -o none 2>/dev/null; then
  az aks create \
    -g "$AZURE_RG" -n "$AKS_NAME" -l "$AZURE_LOCATION" \
    --node-count 1 --node-vm-size Standard_B2s \
    --enable-managed-identity \
    --attach-acr "$ACR_NAME" \
    --generate-ssh-keys \
    --enable-addons monitoring --workspace-resource-id "$LAW_ID" \
    -o none
fi
az aks get-credentials -g "$AZURE_RG" -n "$AKS_NAME" --overwrite-existing -o none
kubectl cluster-info | head -1

# ---------- 6. ingress-nginx ----------
echo ""
echo "[6/8] ingress-nginx (via helm if available, else raw manifest) ..."
if command -v helm >/dev/null 2>&1; then
  helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >/dev/null 2>&1 || true
  helm repo update >/dev/null
  helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
    --namespace ingress-nginx --create-namespace \
    --set controller.service.type=LoadBalancer \
    --wait --timeout 5m >/dev/null
else
  kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.10.1/deploy/static/provider/cloud/deploy.yaml
  kubectl -n ingress-nginx wait --for=condition=Available deployment/ingress-nginx-controller --timeout=180s
fi

# ---------- 7. Build & push images ----------
echo ""
echo "[7/8] Build & push images to ACR ..."
az acr build --registry "$ACR_NAME" --image "web-app-a:v1" "$SCRIPT_DIR/apps/web-app-a"
az acr build --registry "$ACR_NAME" --image "web-app-b:v1" "$SCRIPT_DIR/apps/web-app-b"

# ---------- 8. K8s deploy ----------
echo ""
echo "[8/8] Deploy to AKS ..."
kubectl create namespace "$K8S_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl -n "$K8S_NAMESPACE" create secret generic appinsights \
  --from-literal=APPLICATIONINSIGHTS_CONNECTION_STRING="$APPI_CS" \
  --dry-run=client -o yaml | kubectl apply -f -

# Inject ACR login server into manifest
sed "s|ACR_LOGIN_SERVER|$ACR_LOGIN_SERVER|g" "$SCRIPT_DIR/k8s/deploy.yaml" \
  | kubectl apply -f -
kubectl apply -f "$SCRIPT_DIR/k8s/web-app-b-rbac.yaml"

# Patch web-app-b to use its SA (so it can list pods for /api/cluster)
kubectl -n "$K8S_NAMESPACE" set serviceaccount deployment/web-app-b web-app-b-sa
# Inject POD_NAME env so the app can report which pod served the request
kubectl -n "$K8S_NAMESPACE" set env deployment/web-app-b \
  POD_NAME='$(_POD_NAME_)' --overwrite >/dev/null || true
kubectl -n "$K8S_NAMESPACE" patch deployment web-app-b --type=json -p='[
  {"op":"add","path":"/spec/template/spec/containers/0/env/-",
   "value":{"name":"POD_NAME","valueFrom":{"fieldRef":{"fieldPath":"metadata.name"}}}}
]' 2>/dev/null || true

echo ""
echo "Waiting for pods to become Ready ..."
kubectl -n "$K8S_NAMESPACE" wait --for=condition=Available deployment --all --timeout=180s

# Wait for external IP
echo ""
echo "Waiting for ingress external IP ..."
for i in $(seq 1 60); do
  INGRESS_IP="$(kubectl -n ingress-nginx get svc ingress-nginx-controller \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)"
  [ -n "$INGRESS_IP" ] && break
  sleep 5
done

# Persist runtime values into .env (append if missing, replace if present)
update_env () {
  local k="$1" v="$2"
  if grep -q "^export ${k}=" .env 2>/dev/null; then
    sed -i "s|^export ${k}=.*|export ${k}=\"${v}\"|" .env
  else
    echo "export ${k}=\"${v}\"" >> .env
  fi
}
if [ -f "$SCRIPT_DIR/.env" ]; then
  cd "$SCRIPT_DIR"
  update_env ACR_LOGIN_SERVER "$ACR_LOGIN_SERVER"
  update_env APPI_CS         "$APPI_CS"
  update_env LAW_ID          "$LAW_ID"
  update_env INGRESS_IP      "$INGRESS_IP"
fi

echo ""
echo "======================================================"
echo " ✅ Deploy complete"
echo "======================================================"
echo "  External IP:  ${INGRESS_IP:-<pending — re-check in a minute>}"
echo "  Shop UI:      http://${INGRESS_IP:-<IP>}/a/"
echo "  Report UI:    http://${INGRESS_IP:-<IP>}/b/"
echo ""
echo "Smoke test:"
echo "  curl http://${INGRESS_IP:-<IP>}/a/health"
echo "  curl http://${INGRESS_IP:-<IP>}/b/health"
echo ""
echo "Next step: hook Azure SRE Agent onto RG '$AZURE_RG' via Portal."
echo "See README.md → 'Step 4: Attach Azure SRE Agent'."

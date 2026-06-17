#!/usr/bin/env bash
# Azure Container Apps deployment — Reconciliation Agent demo
#
# Prerequisites:
#   az login
#   az extension add --name containerapp --upgrade
#
# Usage:
#   export ANTHROPIC_API_KEY=sk-ant-...
#   chmod +x azure/deploy.sh
#   ./azure/deploy.sh
#
# After deployment the script prints the public URL.

set -euo pipefail

# ── Config (edit these) ──────────────────────────────────────────────────────
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-reconciliation-demo}"
LOCATION="${LOCATION:-eastus}"
ACR_NAME="${ACR_NAME:-reconciliationdemoacr}"   # must be globally unique, lowercase
APP_NAME="${APP_NAME:-reconciliation-agent}"
ENVIRONMENT_NAME="${ENVIRONMENT_NAME:-reconciliation-env}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

# ── Validate ─────────────────────────────────────────────────────────────────
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "ERROR: ANTHROPIC_API_KEY is not set."
    echo "  export ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
fi

echo ""
echo "=== Reconciliation Agent — Azure Container Apps Deploy ==="
echo "  Resource Group : $RESOURCE_GROUP"
echo "  Location       : $LOCATION"
echo "  Registry       : $ACR_NAME"
echo "  App name       : $APP_NAME"
echo ""

# ── 1. Resource group ────────────────────────────────────────────────────────
echo "▸ Creating resource group..."
az group create \
    --name "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none

# ── 2. Container registry ────────────────────────────────────────────────────
echo "▸ Creating container registry ($ACR_NAME)..."
az acr create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ACR_NAME" \
    --sku Basic \
    --admin-enabled true \
    --output none

# ── 3. Build and push image ──────────────────────────────────────────────────
echo "▸ Building and pushing Docker image..."
# Build from repo root (where Dockerfile lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

az acr build \
    --registry "$ACR_NAME" \
    --image "${APP_NAME}:${IMAGE_TAG}" \
    --file "$REPO_ROOT/Dockerfile" \
    "$REPO_ROOT"

# ── 4. Container Apps environment ────────────────────────────────────────────
echo "▸ Creating Container Apps environment..."
az containerapp env create \
    --name "$ENVIRONMENT_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none

# ── 5. Get ACR credentials ───────────────────────────────────────────────────
ACR_SERVER="${ACR_NAME}.azurecr.io"
ACR_USERNAME=$(az acr credential show --name "$ACR_NAME" --query username --output tsv)
ACR_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query "passwords[0].value" --output tsv)

# ── 6. Deploy container app ──────────────────────────────────────────────────
echo "▸ Deploying container app..."
az containerapp create \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --environment "$ENVIRONMENT_NAME" \
    --image "${ACR_SERVER}/${APP_NAME}:${IMAGE_TAG}" \
    --registry-server "$ACR_SERVER" \
    --registry-username "$ACR_USERNAME" \
    --registry-password "$ACR_PASSWORD" \
    --target-port 8501 \
    --ingress external \
    --min-replicas 1 \
    --max-replicas 1 \
    --cpu 1.0 \
    --memory 2.0Gi \
    --env-vars "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}" \
    --output none

# ── 7. Print URL ─────────────────────────────────────────────────────────────
APP_URL=$(az containerapp show \
    --name "$APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --query "properties.configuration.ingress.fqdn" \
    --output tsv)

echo ""
echo "=== Deployment complete ==="
echo ""
echo "  App URL: https://${APP_URL}"
echo ""
echo "  Demo steps:"
echo "    1. Open https://${APP_URL}"
echo "    2. Dashboard → click 'Run Reconciliation Pipeline' (~3 min first run)"
echo "    3. Exception Queue → review flagged items with agent suggestions"
echo "    4. Fill in Analyst Review form → approve / reject / override"
echo "    5. Audit Trail → verify every decision is logged"
echo ""
echo "  To tear down:"
echo "    az group delete --name $RESOURCE_GROUP --yes --no-wait"

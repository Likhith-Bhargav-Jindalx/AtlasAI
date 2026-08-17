#!/usr/bin/env bash
set -euo pipefail

# Provisions the Azure resources the deployed backend needs: a resource
# group, Container Registry, Container Apps environment, PostgreSQL
# Flexible Server, and a storage account. The Container Apps environment
# wires up its own Log Analytics workspace automatically — no separate
# step for that. Re-running this script is safe: each `az ... create`
# no-ops (or errors harmlessly) against a resource that already exists
# with the same name.
#
# Required env vars — no defaults, deliberately, so this never runs
# against a real subscription by accident:
#   AZURE_RESOURCE_GROUP, AZURE_LOCATION, AZURE_ACR_NAME,
#   AZURE_CONTAINERAPPS_ENV, AZURE_POSTGRES_SERVER_NAME,
#   AZURE_POSTGRES_ADMIN_PASSWORD, AZURE_STORAGE_ACCOUNT_NAME

: "${AZURE_RESOURCE_GROUP:?set AZURE_RESOURCE_GROUP}"
: "${AZURE_LOCATION:?set AZURE_LOCATION}"
: "${AZURE_ACR_NAME:?set AZURE_ACR_NAME}"
: "${AZURE_CONTAINERAPPS_ENV:?set AZURE_CONTAINERAPPS_ENV}"
: "${AZURE_POSTGRES_SERVER_NAME:?set AZURE_POSTGRES_SERVER_NAME}"
: "${AZURE_POSTGRES_ADMIN_PASSWORD:?set AZURE_POSTGRES_ADMIN_PASSWORD}"
: "${AZURE_STORAGE_ACCOUNT_NAME:?set AZURE_STORAGE_ACCOUNT_NAME}"

echo "Creating resource group $AZURE_RESOURCE_GROUP in $AZURE_LOCATION..."
az group create --name "$AZURE_RESOURCE_GROUP" --location "$AZURE_LOCATION"

echo "Creating Container Registry $AZURE_ACR_NAME..."
az acr create --resource-group "$AZURE_RESOURCE_GROUP" --name "$AZURE_ACR_NAME" \
  --sku Basic --admin-enabled true

echo "Creating PostgreSQL Flexible Server $AZURE_POSTGRES_SERVER_NAME..."
az postgres flexible-server create \
  --resource-group "$AZURE_RESOURCE_GROUP" --name "$AZURE_POSTGRES_SERVER_NAME" \
  --location "$AZURE_LOCATION" --admin-user atlas --admin-password "$AZURE_POSTGRES_ADMIN_PASSWORD" \
  --sku-name Standard_B1ms --tier Burstable --storage-size 32 --version 16 \
  --database-name atlas --public-access 0.0.0.0

echo "Creating storage account $AZURE_STORAGE_ACCOUNT_NAME..."
az storage account create \
  --resource-group "$AZURE_RESOURCE_GROUP" --name "$AZURE_STORAGE_ACCOUNT_NAME" \
  --location "$AZURE_LOCATION" --sku Standard_LRS

echo "Creating Container Apps environment $AZURE_CONTAINERAPPS_ENV..."
az containerapp env create \
  --resource-group "$AZURE_RESOURCE_GROUP" --name "$AZURE_CONTAINERAPPS_ENV" \
  --location "$AZURE_LOCATION"

echo "Done. Next: Task 8 creates the Container App itself on first deploy."

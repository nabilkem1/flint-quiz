// Key Vault in RBAC mode (NOT access-policy), soft-delete + purge protection ON.
// Purge protection cannot be undone — re-deploying with the same name within
// 90 days of a delete will fail. Accept this trade-off per SEC-013.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

@description('Tenant ID the vault is scoped to')
param tenantId string = subscription().tenantId

// Globally unique: hyphenated, max 24 chars
var suffix = uniqueString(resourceGroup().id, environmentName)
var kvName = take('${prefix}-${environmentName}-kv-${suffix}', 24)

resource kv 'Microsoft.KeyVault/vaults@2024-04-01-preview' = {
  name: kvName
  location: location
  tags: tags
  properties: {
    tenantId: tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

output keyVaultId string = kv.id
output keyVaultName string = kv.name
output keyVaultUri string = kv.properties.vaultUri

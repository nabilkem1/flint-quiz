// Cosmos DB account (SQL API). Container creation lives in 003-cosmos-db.
// Local auth disabled (SEC-004); single-region + autoscale for v1;
// public network allowed for v1 (VNET integration reserved for v2).

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

// Cosmos account names share a GLOBAL DNS namespace (`*.documents.azure.com`),
// so a bare `<prefix>-<env>-cosmos` will collide with someone else's account.
// Append a deterministic per-RG suffix (same scheme keyvault.bicep uses).
// Max length 44 chars; lowercase + digits + hyphen.
var suffix = uniqueString(resourceGroup().id, environmentName)
var cosmosName = take('${prefix}-${environmentName}-cosmos-${suffix}', 44)

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-08-15' = {
  name: cosmosName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  identity: {
    type: 'None'
  }
  properties: {
    databaseAccountOfferType: 'Standard'
    disableLocalAuth: true
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
    publicNetworkAccess: 'Enabled'
    networkAclBypass: 'AzureServices'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: []
    backupPolicy: {
      type: 'Continuous'
      continuousModeProperties: {
        tier: 'Continuous7Days'
      }
    }
    minimalTlsVersion: 'Tls12'
  }
}

output cosmosAccountId string = cosmos.id
output cosmosAccountName string = cosmos.name
output cosmosEndpoint string = cosmos.properties.documentEndpoint

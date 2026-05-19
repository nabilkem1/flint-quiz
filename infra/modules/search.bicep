// Azure AI Search S1 with semantic search enabled and local auth disabled.
// Index schema and seed content live in 002-ai-search; only the service is
// provisioned here. Index lifecycle is Bicep-owned via uami-deploy-* (002).

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

// Search service names: 2–60 chars, lowercase, hyphens allowed
var searchName = toLower('${prefix}-${environmentName}-srch-${uniqueString(resourceGroup().id, environmentName)}')

resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: searchName
  location: location
  tags: tags
  sku: {
    name: 'standard'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    disableLocalAuth: true
    semanticSearch: 'standard'
    publicNetworkAccess: 'enabled'
  }
}

output searchServiceId string = search.id
output searchServiceName string = search.name
output searchEndpoint string = 'https://${search.name}.search.windows.net'

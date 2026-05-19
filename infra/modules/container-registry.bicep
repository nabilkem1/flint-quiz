// Azure Container Registry (ACR) for the quiz-agent + seed-loader images.
//
// `azd deploy` uses ACR Tasks (`docker.remoteBuild: true` in `azure.yaml`)
// to build images in Azure rather than locally. Pull is via Managed
// Identity — `AcrPull` on the runtime UAMI (granted by rbac.bicep).

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name, e.g., dev')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

@description('ACR SKU. `Basic` is the cheapest with the throughput a small app needs.')
@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param skuName string = 'Basic'

// Globally unique. ACR names: lowercase alnum, 5-50 chars. The suffix
// mirrors the AppConfig store's pattern so resources sort together.
var suffix = uniqueString(resourceGroup().id, environmentName)
var registryName = take(toLower(replace('${prefix}${environmentName}acr${suffix}', '-', '')), 50)

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: registryName
  location: location
  tags: tags
  sku: {
    name: skuName
  }
  properties: {
    // Admin user OFF — auth is Entra (Managed Identity) only.
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
    anonymousPullEnabled: false
    dataEndpointEnabled: false
  }
}

output registryId string = registry.id
output registryName string = registryName
output loginServer string = registry.properties.loginServer

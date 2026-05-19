// Blob storage account for authored question source-of-truth.
// allowSharedKeyAccess MUST be false — all access is via Managed Identity.
// Creates the `questions` container and per-language append-blob folders by
// uploading a tiny placeholder so the `en/`, `fr/`, `es/` virtual prefixes
// exist immediately. Real seed content is loaded by 002 task pack.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

@description('Languages whose virtual folders should be created under questions/')
param supportedLanguages array

// Globally unique: lowercase, no hyphens, 3-24 chars
var suffix = uniqueString(resourceGroup().id, environmentName)
var storageName = take(toLower(replace('${prefix}${environmentName}st${suffix}', '-', '')), 24)

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowSharedKeyAccess: false
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    isVersioningEnabled: true
  }
}

resource questionsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'questions'
  properties: {
    publicAccess: 'None'
    metadata: {
      languages: join(supportedLanguages, ',')
    }
  }
}

output storageAccountId string = storage.id
output storageAccountName string = storage.name
output blobEndpoint string = storage.properties.primaryEndpoints.blob
output questionsContainerName string = questionsContainer.name

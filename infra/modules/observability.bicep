// Log Analytics workspace + workspace-based Application Insights.
// App Insights connection string is non-secret (per Microsoft guidance) and
// is exposed as an output so it can flow into the Hosted Agent + post-provision env.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name, e.g., dev')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

@description('Log retention days')
param retentionInDays int = 30

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-${environmentName}-law'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-${environmentName}-appi'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
    IngestionMode: 'LogAnalytics'
    DisableLocalAuth: true
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

output logAnalyticsId string = law.id
output logAnalyticsName string = law.name
output appInsightsId string = appInsights.id
output appInsightsName string = appInsights.name
// Connection string is non-secret per Microsoft Application Insights guidance.
output appInsightsConnectionString string = appInsights.properties.ConnectionString

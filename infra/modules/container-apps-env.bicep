// Azure Container Apps Managed Environment.
//
// Holds the `quiz-agent` Container App + the `seed-loader` Container
// App Job. Both share the same App Insights + Log Analytics workspace
// so traces land in one place.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

@description('Log Analytics workspace resource ID (for env-level log forwarding)')
param logAnalyticsWorkspaceId string

@description('App Insights connection string (for env-level Dapr / OTel forwarding)')
param appInsightsConnectionString string

var envName = '${prefix}-${environmentName}-cae'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: last(split(logAnalyticsWorkspaceId, '/'))
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
    daprAIConnectionString: appInsightsConnectionString
    zoneRedundant: false
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

output environmentId string = env.id
output environmentName string = envName
output environmentDefaultDomain string = env.properties.defaultDomain

// Microsoft Foundry resource (Bicep target: Microsoft.CognitiveServices/accounts
// kind=AIServices) and a Foundry project as a child sub-resource.
//
// Reference:
//   https://learn.microsoft.com/en-us/azure/foundry/how-to/create-resource-template
//   github.com/microsoft-foundry/foundry-samples (infrastructure-setup-bicep/00-basic)
//
// Differences from Microsoft's "00-basic" sample, applied for Flint Quiz
// governance posture:
//   - identity.type = UserAssigned (NOT SystemAssigned). SAMI is forbidden by
//     infra/README §3.1 — UAMI gives stable principal IDs across recreates so
//     RBAC survives a project rebuild.
//   - disableLocalAuth = true. Entra-only, no API keys (SEC-004 equivalent
//     applied to the Foundry inference plane).
//   - No storage/keyvault/appinsights wiring at the account level. The new
//     Foundry account does NOT have the old ML-Hub dependency chain; those
//     resources still exist in this env but are consumed independently
//     (Storage for authoring, KV for secrets, App Insights for tracing wired
//     via diagnostic settings on the project below).

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name')
param environmentName string

@description('Azure region (MUST support Foundry + the realtime model deployment)')
param location string

@description('Mandatory tags')
param tags object

@description('UAMI resource ID to attach to both the Foundry account and the project')
param uamiAgentResourceId string

@description('Log Analytics workspace resource ID — destination of project diagnostic settings (NFR-008)')
param logAnalyticsId string

var foundryName = '${prefix}-${environmentName}-foundry'
var projectName = '${prefix}-${environmentName}-proj'

// Foundry account ("AI Foundry resource" in portal language). The custom
// subdomain is what makes the inference + project APIs reachable on a stable
// hostname (used by Realtime + Hosted Agent endpoint computation downstream).
resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: foundryName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiAgentResourceId}': {}
    }
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: foundryName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

// Foundry project — child of the account. One project per environment for v1.
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiAgentResourceId}': {}
    }
  }
  properties: {}
}

// Project diagnostic settings → Log Analytics. Satisfies NFR-008 (Foundry
// tracing reaches the workspace-based App Insights / LAW for the observability
// surface 008 will build dashboards on top of).
resource projectDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: project
  name: 'foundry-project-to-law'
  properties: {
    workspaceId: logAnalyticsId
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

// Account-level diagnostic settings (model inference + control plane).
resource foundryDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: foundry
  name: 'foundry-account-to-law'
  properties: {
    workspaceId: logAnalyticsId
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
      }
    ]
  }
}

output foundryAccountId string = foundry.id
output foundryAccountName string = foundry.name
output foundryEndpoint string = foundry.properties.endpoint
output foundryCustomSubdomain string = foundry.properties.customSubDomainName
output projectId string = project.id
output projectName string = project.name

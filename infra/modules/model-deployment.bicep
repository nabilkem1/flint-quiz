// Foundry model deployment — Microsoft.CognitiveServices/accounts/deployments.
//
// Why this lives in Phase 1: the Realtime endpoint and the Hosted Agent are
// inert without a model deployment to call. The OLD Hub-based Foundry pattern
// hid this behind a deployment-script bootstrap; the NEW account-based
// Foundry exposes it as a first-class child ARM resource, so we declare it
// natively in Bicep. Application-layer model lifecycle (upgrade, A/B,
// fallback) still belongs to 004-agent-framework and the model-upgrade
// process in docs/ai-agent-development-guidelines.md.

@description('Foundry account name (parent)')
param foundryAccountName string

@description('Deployment name — this is what the SDK sees as the model alias (matches AppConfig key model:deploymentName)')
param deploymentName string

@description('Underlying model name (OpenAI catalog), e.g., gpt-4o-realtime-preview')
param modelName string

@description('Model version, e.g., 2024-10-01')
param modelVersion string

@description('Model publisher format, e.g., OpenAI')
param modelFormat string = 'OpenAI'

@description('SKU name (capacity tier)')
param skuName string = 'GlobalStandard'

@description('SKU capacity (TPM units)')
param skuCapacity int = 1

// Look up the parent account by name. The Foundry account itself is declared
// in foundry-project.bicep; this module deploys the model as a child without
// re-declaring the parent.
resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: foundry
  name: deploymentName
  sku: {
    name: skuName
    capacity: skuCapacity
  }
  properties: {
    model: {
      name: modelName
      format: modelFormat
      version: modelVersion
    }
  }
}

output deploymentId string = deployment.id
output deploymentName string = deployment.name
output modelName string = modelName
output modelVersion string = modelVersion

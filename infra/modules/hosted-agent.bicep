// "Hosted Agent" in the new Foundry model.
//
// In the OLD ML-workspace Foundry, the Hosted Agent was a discrete ARM
// resource declared inside the project. In the NEW account+project Foundry,
// agents are first-class **runtime** objects created via the Azure AI Projects
// SDK (or `az ml agent create` / portal) against an existing project — there
// is currently no `Microsoft.CognitiveServices/accounts/projects/agents` ARM
// type for agent registration. The "shell" therefore IS the project.
//
// This module's job in Phase 1 is purely declarative: emit the agent's
// canonical name, surface the runtime endpoint the SDK will use, and stamp
// the UAMI/AppConfig/AppInsights context the agent will need. The actual
// agent registration call lands in 004-agent-framework.
//
// If/when an ARM resource type for Foundry agents ships, replace these
// outputs with a native resource declaration.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name')
param environmentName string

@description('Foundry project resource ID')
param projectId string

@description('Foundry project name')
param projectName string

@description('Foundry custom subdomain (used to derive the runtime endpoint)')
param foundryCustomSubdomain string

@description('UAMI client ID the agent runtime will authenticate as')
param uamiAgentClientId string

@description('UAMI resource ID attached to the project (declared in foundry-project.bicep)')
param uamiAgentResourceId string

@description('App Insights connection string (non-secret; surfaced to agent runtime)')
param appInsightsConnectionString string

@description('App Configuration endpoint URL')
param appConfigEndpoint string

@description('Model deployment name (matches the deployment created by model-deployment.bicep)')
param modelDeploymentName string

@description('Model deployment resource ID — dependency anchor so this module sequences after the model is deployed')
param modelDeploymentId string

var agentName = '${prefix}-${environmentName}-agent'

// Sequence after the model deployment exists (declared via an output read,
// so Bicep wires the implicit dependency without a redundant dependsOn).
var dependencyAnchor = modelDeploymentId

output agentName string = agentName
output agentRuntimeEndpoint string = 'https://${foundryCustomSubdomain}.openai.azure.com'
output agentProjectId string = projectId
output agentProjectName string = projectName
output agentUamiClientId string = uamiAgentClientId
output agentUamiResourceId string = uamiAgentResourceId
output agentAppInsightsConnectionString string = appInsightsConnectionString
output agentAppConfigEndpoint string = appConfigEndpoint
output agentModelDeploymentName string = modelDeploymentName
output agentDependencyAnchor string = dependencyAnchor

// Per-resource RBAC for the three runtime identities.
//
// Scoping discipline (mirrors infra/README §4):
//   uami-agent-*    : Cosmos data-plane (custom; restricted to four containers
//                     — wired in 003), Search Index Data READER, Key Vault
//                     Secrets User, App Configuration Data Reader, Monitoring
//                     Metrics Publisher.
//   uami-indexer-*  : Search Index Data CONTRIBUTOR (writes documents; does
//                     NOT include Search Service Contributor), Storage Blob
//                     Data Reader on the storage account.
//   uami-deploy-*   : Contributor on the RG (env-scoped), Search Service
//                     Contributor on the search service (CI is the only
//                     principal that creates/deletes the index — index
//                     lifecycle is Bicep-owned, not runtime-owned).
//
// FORBIDDEN here (and enforced by the post-provision hook):
//   - No Owner / Contributor / User Access Administrator on uami-agent-* or
//     uami-indexer-*.
//   - No Search Service Contributor on uami-agent-* or uami-indexer-*.
//   - No Search Index Data Contributor on uami-agent-*.
//   - No RG- or subscription-scope assignments where a per-resource scope is
//     available.

@description('UAMI principal IDs')
param agentPrincipalId string
param indexerPrincipalId string
param deployPrincipalId string

@description('Human / CI deployer AAD principal ID (the entity running `azd up`). Needs Search Service Contributor so `post-provision.sh` can PUT the questions index via REST without manual portal action. Pass `""` to skip the assignment (CI envs that never run interactive hooks).')
param deployerHumanPrincipalId string = ''

@description('Type of `deployerHumanPrincipalId` — User for interactive azd, ServicePrincipal for CI.')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param deployerHumanPrincipalType string = 'User'

@description('Resource IDs to scope assignments to')
param keyVaultId string
param keyVaultName string
param appConfigId string
param appConfigName string
param appInsightsId string
param appInsightsName string
param storageAccountId string
param storageAccountName string
param searchServiceId string
param searchServiceName string
param foundryAccountId string
param foundryAccountName string

// ---- Built-in role definition IDs (do NOT hardcode the full path; build it
// with subscriptionResourceId so the assignment scope is unambiguous) ----
var roles = {
  searchIndexDataReader: '1407120a-92aa-4202-b7e9-c0e197c71c8f'
  searchIndexDataContributor: '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
  searchServiceContributor: '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
  appConfigDataReader: '516239f1-63e1-4d78-a4de-a74fb236a071'
  monitoringMetricsPublisher: '3913510d-42f4-4e42-8a64-420c390055eb'
  storageBlobDataReader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  contributor: 'b24988ac-6180-42a0-ab88-20f7382dd24c'
  // Foundry data-plane
  cognitiveServicesOpenAIUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  azureAIDeveloper: '64702f94-c441-49e6-a78b-ef80e0188fee'
  // ACR pull (needed by the agent + seed container apps so they can pull
  // their images from the registry; granted on the ACR resource scope —
  // never tenant-wide).
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
}

@description('Container Registry resource ID — the AcrPull assignments scope here. Empty string disables the assignments.')
param containerRegistryId string = ''

@description('Container Registry name (used to construct the `existing` resource reference).')
param containerRegistryName string = ''

// Look up the consuming resources via `existing` so role assignments scope at
// the correct child-resource depth (assignment scope = parent resource).
resource keyVault 'Microsoft.KeyVault/vaults@2024-04-01-preview' existing = {
  name: keyVaultName
}

resource appConfig 'Microsoft.AppConfiguration/configurationStores@2024-05-01' existing = {
  name: appConfigName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource searchService 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchServiceName
}

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

// ============================================================================
// uami-agent-*  (runtime, read-mostly)
// ============================================================================

resource agentKvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVaultId, agentPrincipalId, roles.keyVaultSecretsUser)
  properties: {
    principalId: agentPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.keyVaultSecretsUser)
  }
}

resource agentAppConfigReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: appConfig
  name: guid(appConfigId, agentPrincipalId, roles.appConfigDataReader)
  properties: {
    principalId: agentPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.appConfigDataReader)
  }
}

resource agentSearchIndexReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: searchService
  name: guid(searchServiceId, agentPrincipalId, roles.searchIndexDataReader)
  properties: {
    principalId: agentPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexDataReader)
  }
}

resource agentMonitoringPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: appInsights
  name: guid(appInsightsId, agentPrincipalId, roles.monitoringMetricsPublisher)
  properties: {
    principalId: agentPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.monitoringMetricsPublisher)
  }
}

// Foundry inference: agent calls deployed models via MI. OpenAI User is the
// least-privilege data-plane role for inference calls.
resource agentFoundryOpenAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundryAccount
  name: guid(foundryAccountId, agentPrincipalId, roles.cognitiveServicesOpenAIUser)
  properties: {
    principalId: agentPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesOpenAIUser)
  }
}

// Foundry project APIs: AI Developer is required for the agent to enumerate
// and call project-scoped APIs (threads, runs, connections) — scoped to the
// account, not subscription.
resource agentFoundryAIDeveloper 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundryAccount
  name: guid(foundryAccountId, agentPrincipalId, roles.azureAIDeveloper)
  properties: {
    principalId: agentPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.azureAIDeveloper)
  }
}

// Foundry agent register / version create — the built-in roles do NOT cover
// `Microsoft.CognitiveServices/accounts/AIServices/agents/*` data actions, so
// we define a tiny custom role here and assign it to the agent UAMI. Without
// this, `agents.create_version(...)` at container startup returns 403 and
// the agent's first tool-schema push silently fails.
//
// The role definition is account-scoped (subscription-level GUID would
// require subscription Owner to author, which the bicep deployer doesn't
// hold on personal subs). Scoping to `foundryAccount` means a re-deploy in
// a different account creates a sibling role; that's fine — there's only
// one Foundry account per env.
resource foundryAgentsWriterRole 'Microsoft.Authorization/roleDefinitions@2022-04-01' = {
  scope: foundryAccount
  name: guid(foundryAccountId, 'foundry-agents-writer')
  properties: {
    roleName: 'Foundry Agents Writer (${foundryAccountName})'
    description: 'Allows write to Foundry agent definitions + versions on this account. Required by uami-agent-*.'
    type: 'CustomRole'
    permissions: [
      {
        actions: []
        notActions: []
        dataActions: [
          'Microsoft.CognitiveServices/accounts/AIServices/agents/*'
        ]
        notDataActions: []
      }
    ]
    assignableScopes: [
      foundryAccountId
    ]
  }
}

resource agentFoundryAgentsWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundryAccount
  name: guid(foundryAccountId, agentPrincipalId, 'foundry-agents-writer')
  properties: {
    principalId: agentPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: foundryAgentsWriterRole.id
  }
}

// NOTE: Cosmos DB data-plane role assignment for uami-agent-* uses the
// SqlRoleAssignments resource type and is restricted to specific containers
// (sessions, users, audit r/w; topics r/o) — that binding lives in
// 003-cosmos-db along with the container declarations, since the role
// definition must reference the container DataActions which 003 owns.

// ============================================================================
// uami-indexer-*  (seed loader; writes index documents, reads blob source)
// ============================================================================

resource indexerSearchIndexContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: searchService
  name: guid(searchServiceId, indexerPrincipalId, roles.searchIndexDataContributor)
  properties: {
    principalId: indexerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexDataContributor)
  }
}

resource indexerStorageBlobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storageAccount
  name: guid(storageAccountId, indexerPrincipalId, roles.storageBlobDataReader)
  properties: {
    principalId: indexerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataReader)
  }
}

// ============================================================================
// uami-deploy-*  (CI; control plane only)
// ============================================================================

resource deployRgContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  // Resource-group scope is the smallest meaningful scope for "deploy
  // everything in this env"; subscription-level assignment is prohibited.
  scope: resourceGroup()
  name: guid(resourceGroup().id, deployPrincipalId, roles.contributor)
  properties: {
    principalId: deployPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.contributor)
  }
}

resource deploySearchServiceContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: searchService
  name: guid(searchServiceId, deployPrincipalId, roles.searchServiceContributor)
  properties: {
    principalId: deployPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchServiceContributor)
  }
}

// Human deployer (interactive azd) — same role, scoped to the search
// service, so `post-provision.sh` can PUT the `questions` index via the
// Search REST API. Skipped when `deployerHumanPrincipalId == ""` (CI envs
// that never run the interactive hook).
resource deployerHumanSearchContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerHumanPrincipalId)) {
  scope: searchService
  name: guid(searchServiceId, deployerHumanPrincipalId, roles.searchServiceContributor, 'human')
  properties: {
    principalId: deployerHumanPrincipalId
    principalType: deployerHumanPrincipalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchServiceContributor)
  }
}

// ---- Human deployer Foundry data-plane access ----------------------------
//
// Without these three, the operator's AAD user sees:
//   * "You don't have permission to build agents in this project" in the
//     Foundry portal (Agents tab — needs Azure AI Developer at minimum).
//   * `401 PermissionDenied` when running the chat CLI locally against
//     `/api/projects/<proj>/openai/v1/responses` (needs Cognitive
//     Services OpenAI User).
//   * Can't push agent versions out-of-band for testing (needs the
//     custom `foundryAgentsWriterRole` defined above for the UAMI).
//
// All three skip when `deployerHumanPrincipalId == ""` (CI envs that only
// need the runtime UAMI to have data-plane access).
resource deployerHumanFoundryAIDeveloper 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerHumanPrincipalId)) {
  scope: foundryAccount
  name: guid(foundryAccountId, deployerHumanPrincipalId, roles.azureAIDeveloper, 'human')
  properties: {
    principalId: deployerHumanPrincipalId
    principalType: deployerHumanPrincipalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.azureAIDeveloper)
  }
}

resource deployerHumanFoundryOpenAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerHumanPrincipalId)) {
  scope: foundryAccount
  name: guid(foundryAccountId, deployerHumanPrincipalId, roles.cognitiveServicesOpenAIUser, 'human')
  properties: {
    principalId: deployerHumanPrincipalId
    principalType: deployerHumanPrincipalType
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesOpenAIUser)
  }
}

resource deployerHumanFoundryAgentsWriter 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerHumanPrincipalId)) {
  scope: foundryAccount
  name: guid(foundryAccountId, deployerHumanPrincipalId, 'foundry-agents-writer', 'human')
  properties: {
    principalId: deployerHumanPrincipalId
    principalType: deployerHumanPrincipalType
    roleDefinitionId: foundryAgentsWriterRole.id
  }
}

// ---- ACR AcrPull on agent + indexer UAMIs --------------------------------
// Scoped to the ACR only — never the RG. The role assignments are
// gated on `containerRegistryId` so the module remains usable without
// container infra (Phase 1) — when empty, the `existing` reference is
// still materialised but no assignments are created.

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: !empty(containerRegistryName) ? containerRegistryName : 'placeholder'
}

resource agentAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(containerRegistryId)) {
  scope: acr
  name: guid(containerRegistryId, agentPrincipalId, roles.acrPull)
  properties: {
    principalId: agentPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
  }
}

resource indexerAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(containerRegistryId)) {
  scope: acr
  name: guid(containerRegistryId, indexerPrincipalId, roles.acrPull)
  properties: {
    principalId: indexerPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
  }
}

output assignedRoles object = {
  agent: [
    'KeyVaultSecretsUser'
    'AppConfigDataReader'
    'SearchIndexDataReader'
    'MonitoringMetricsPublisher'
    'CognitiveServicesOpenAIUser'
    'AzureAIDeveloper'
  ]
  indexer: [
    'SearchIndexDataContributor'
    'StorageBlobDataReader'
  ]
  deploy: [
    'Contributor (RG)'
    'SearchServiceContributor'
  ]
}

// Flint Quiz — Phase 1 Infrastructure Foundation (entry point).
//
// Subscription-scoped. `azd up` from a clean subscription runs this file
// end-to-end. It creates the env resource group and composes every Phase 1
// module in dependency order. Index/container schema and any seed content
// belong to 002 (AI Search) and 003 (Cosmos DB) respectively.
//
// Governance pointers:
//   - All `disableLocalAuth` / `allowSharedKeyAccess=false` posture enforced
//     inside each module — see SEC-004, SEC-005, SEC-013.
//   - UAMI only (SAMI is forbidden on Foundry + Hosted Agent).
//   - No connection strings, account keys, SAS tokens, or shared keys are
//     emitted as outputs. App Insights connection string is the documented
//     exception (non-secret per Microsoft guidance).
//   - Least-privilege RBAC scoped per-resource (never RG, never subscription
//     for runtime UAMIs). See infra/modules/rbac.bicep and infra/README §4.

targetScope = 'subscription'

// ---- Parameters ------------------------------------------------------------

@minLength(2)
@maxLength(12)
@description('Environment name (dev | qa | prod)')
param environmentName string

@description('Azure region. MUST support Foundry Realtime API.')
param location string

@description('Override region for AI Search only. Defaults to `location`. Use this when the primary region is out of Search capacity (InsufficientResourcesAvailable).')
param searchLocation string = location

@description('Short product prefix used in resource names (e.g., fq)')
param prefix string = 'fq'

@description('Languages supported by the v1 quiz system')
param supportedLanguages array = [
  'en'
  'fr'
  'es'
]

@description('Model deployment name resolved by the agent at runtime (matches AppConfig key model:deploymentName)')
param modelDeploymentName string

@description('Underlying model name (OpenAI catalog). Current GA realtime model is gpt-realtime.')
param modelName string = 'gpt-realtime'

@description('Model version. gpt-realtime GA is 2025-08-28.')
param modelVersion string = '2025-08-28'

@description('Model deployment SKU name')
param modelSkuName string = 'GlobalStandard'

@description('Model deployment capacity (TPM units)')
param modelSkuCapacity int = 1

// ---- Chat-capable model (text completions/responses) ----------------------
//
// `gpt-realtime` is voice-only — its endpoint rejects `/chat/completions`
// and `/responses`. The agent's PromptAgentDefinition needs a text-capable
// model so the Foundry Playground (and the chat client at
// `src/agent/chat.py`) can roundtrip. We deploy `gpt-4o-mini` alongside.

@description('Chat-capable model deployment name (used by the agent registration)')
param chatModelDeploymentName string = 'gpt-4o-mini'

@description('Chat model name (OpenAI catalog)')
param chatModelName string = 'gpt-4o-mini'

@description('Chat model version')
param chatModelVersion string = '2024-07-18'

@description('Chat model SKU name')
param chatModelSkuName string = 'GlobalStandard'

@description('Chat model SKU capacity (TPM units, kilo-tokens-per-minute on GlobalStandard). 10 is the bicep default; 10x that handles interactive quiz play comfortably (each multi-turn tool call ~1-3k tokens; 5+ questions × MCP roundtrips overflows the floor). Bump higher for production / load tests; the gpt-4o-mini PAYG ceiling is typically 1000+ on most subscriptions.')
param chatModelSkuCapacity int = 100

@description('TTL applied to completed/expired session documents in Cosmos (003)')
param cosmosSessionsTtlDays int = 30

@description('TTL applied to audit documents (long retention; SEC-014)')
param auditTtlDays int = 2555

@description('Max single voice session length in minutes (NFR-013)')
param voiceMaxSessionMinutes int = 20

@description('Idle seconds before realtime channel auto-disconnects')
param voiceIdleSeconds int = 30

@description('Whether APIM is enabled (seeded into AppConfig as features:apim)')
param featuresApim bool = false

@description('Tag value for owner contact')
param tagOwner string

@description('Tag value for finance cost center')
param tagCostCenter string

@description('Object ID of the principal running `azd provision` (auto-populated by azd as AZURE_PRINCIPAL_ID). Used to grant App Configuration Data Owner on the store ONLY for seed-time key-value writes; runtime reads are via uami-agent-* (least privilege).')
param deployerPrincipalId string

@description('AAD principal type of deployerPrincipalId. `User` for interactive azd; `ServicePrincipal` for CI.')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param deployerPrincipalType string = 'User'

@description('Whether to deploy the AI Search `questions` index control-plane via deploymentScripts. Set to `false` in subscriptions where the deploymentScripts identity surface is restricted; the index is then created by the seed loader (`src/seed/seed_index.py`) at first run. Phase-1 default is `false`.')
param deployAiSearchIndex bool = false

@description('Whether to deploy the background sweeper as a scheduled Container Apps Job. No `Microsoft.Web/serverFarms` quota required — the legacy Functions-on-VM design needed it; this one shares the existing Container Apps managed environment.')
param deploySweeper bool = true

@description('Image reference for the sweeper CAJ. On a fresh deploy this is the public hello-world bootstrap (the resource needs SOME image at create time, before `azd deploy sweeper` builds the real one). Subsequent provisions read `SERVICE_SWEEPER_IMAGE_NAME` from the azd env — which azd populates after each successful `azd deploy sweeper` — so the bicep replace step never reverts a real image back to the bootstrap. Accepts empty string so `azd down + azd up` (where the env var is stale-but-set) still falls back to bootstrap; see pre-deploy.sh stale-image guard. The bootstrap-image race on a FIRST provision is unchanged: the first 1-3 cron firings will Fail until the real image lands; subsequent provisions are clean.')
param sweeperImageRef string = ''

@description('Image reference for the quiz-agent Container App. Same pattern as `sweeperImageRef`: bootstrap when empty, real ACR tag otherwise. Read from `SERVICE_QUIZ_AGENT_IMAGE_NAME` via main.parameters.json on subsequent provisions.')
param quizAgentImageRef string = ''

@description('Image reference for the seed-loader CAJ. Same pattern as `sweeperImageRef`: bootstrap when empty, real ACR tag otherwise. Read from `SERVICE_SEED_LOADER_IMAGE_NAME` via main.parameters.json on subsequent provisions.')
param seedLoaderImageRef string = ''

@description('Image reference for the mcp-server Container App. Same pattern as `sweeperImageRef`: bootstrap when empty, real ACR tag otherwise. Read from `SERVICE_MCP_SERVER_IMAGE_NAME` via main.parameters.json on subsequent provisions.')
param mcpServerImageRef string = ''

@description('Whether to deploy the MCP server Container App. The 5 quiz tools are also registered as inline `type:function` tools on the agent for the chat CLI path; the MCP server is the bridge that lets the Foundry Playground execute them too.')
param deployMcpServer bool = true

@secure()
@description('Shared API key the Foundry MCP connection presents and the MCP server validates (X-API-Key header). Defaults to newGuid() which rotates every provision — pin to a stable value via `azd env set MCP_API_KEY <value>` if you need rotation control. Same value is wired into both the Foundry connection (CustomKeys auth) and the MCP server container (env-as-secret).')
param mcpApiKey string = newGuid()

// Centralized bootstrap image — keeps every consumer string in sync if we
// ever want to change the hello-world default (e.g., a tiny custom image
// that prints a clearer message about "real image not yet pushed").
var bootstrapImageRef = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

// Resolved image refs — empty (or stale-but-set-to-empty by pre-deploy.sh)
// becomes bootstrap. Bicep evaluates `empty()` to true for both `null` and `''`.
var resolvedSweeperImageRef = empty(sweeperImageRef) ? bootstrapImageRef : sweeperImageRef
var resolvedQuizAgentImageRef = empty(quizAgentImageRef) ? bootstrapImageRef : quizAgentImageRef
var resolvedSeedLoaderImageRef = empty(seedLoaderImageRef) ? bootstrapImageRef : seedLoaderImageRef
var resolvedMcpServerImageRef = empty(mcpServerImageRef) ? bootstrapImageRef : mcpServerImageRef

// ---- Derived values --------------------------------------------------------

var rgName = '${prefix}-${environmentName}-rg'

var commonTags = {
  app: 'flint-quiz'
  environment: environmentName
  owner: tagOwner
  costCenter: tagCostCenter
  managedBy: 'bicep'
}

// ---- Resource group --------------------------------------------------------

module rg 'modules/resource-group.bicep' = {
  name: 'rg-${environmentName}'
  params: {
    name: rgName
    location: location
    tags: commonTags
  }
}

// ---- UAMIs (created first so RBAC can reference principalIds) --------------

module uami 'modules/uami.bicep' = {
  name: 'uami-${environmentName}'
  scope: resourceGroup(rgName)
  dependsOn: [
    rg
  ]
  params: {
    prefix: prefix
    environmentName: environmentName
    location: location
    tags: commonTags
  }
}

// ---- Observability ---------------------------------------------------------

module observability 'modules/observability.bicep' = {
  name: 'observability-${environmentName}'
  scope: resourceGroup(rgName)
  dependsOn: [
    rg
  ]
  params: {
    prefix: prefix
    environmentName: environmentName
    location: location
    tags: commonTags
  }
}

// ---- Key Vault + AppConfig + Storage + Cosmos + Search ---------------------

module keyVault 'modules/keyvault.bicep' = {
  name: 'kv-${environmentName}'
  scope: resourceGroup(rgName)
  dependsOn: [
    rg
  ]
  params: {
    prefix: prefix
    environmentName: environmentName
    location: location
    tags: commonTags
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage-${environmentName}'
  scope: resourceGroup(rgName)
  dependsOn: [
    rg
  ]
  params: {
    prefix: prefix
    environmentName: environmentName
    location: location
    tags: commonTags
    supportedLanguages: supportedLanguages
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos-${environmentName}'
  scope: resourceGroup(rgName)
  dependsOn: [
    rg
  ]
  params: {
    prefix: prefix
    environmentName: environmentName
    location: location
    tags: commonTags
  }
}

// 003-cosmos-db: flint-quiz database + four containers + retention stances.
module cosmosDatabase 'modules/cosmos-database.bicep' = {
  name: 'cosmosdb-${environmentName}'
  scope: resourceGroup(rgName)
  params: {
    cosmosAccountName: cosmos.outputs.cosmosAccountName
    // Grant the agent UAMI account-scope Data Contributor at provision time
    // so the quiz-agent container + sweeper CAJ can read/write without a
    // manual `az cosmosdb sql role assignment create`. See module for the
    // tighten-to-custom-role follow-up note.
    uamiAgentPrincipalId: uami.outputs.agentPrincipalId
    // Grant the indexer UAMI the same — the seed-loader CAJ chains
    // `seed_index` (Search) + `seed_topics` (Cosmos) on a single firing.
    uamiIndexerPrincipalId: uami.outputs.indexerPrincipalId
  }
}

// 003-cosmos-db: immutable Blob container for the audit two-stage archive.
module auditArchive 'modules/audit-archive.bicep' = {
  name: 'audit-archive-${environmentName}'
  scope: resourceGroup(rgName)
  params: {
    storageAccountName: storage.outputs.storageAccountName
  }
}

module search 'modules/search.bicep' = {
  name: 'search-${environmentName}'
  scope: resourceGroup(rgName)
  dependsOn: [
    rg
  ]
  params: {
    prefix: prefix
    environmentName: environmentName
    location: searchLocation
    tags: commonTags
  }
}

// AI Search `questions` index + per-language synonym maps (002 control plane).
// Runs as uami-deploy-* (Search Service Contributor — see rbac.bicep). Must
// run after RBAC so the assignment has propagated; depends_on wired below
// once rbac module is declared (see end of this file).
var questionsIndexSchema = loadJsonContent('scripts/questions-index-schema.json')
var synonymsEn = loadJsonContent('scripts/synonyms-en.json').synonyms
var synonymsFr = loadJsonContent('scripts/synonyms-fr.json').synonyms
var synonymsEs = loadJsonContent('scripts/synonyms-es.json').synonyms

// AppConfig depends on the search endpoint URL so it can seed search:endpoint.
module appConfig 'modules/appconfig.bicep' = {
  name: 'appcs-${environmentName}'
  scope: resourceGroup(rgName)
  dependsOn: [
    rg
  ]
  params: {
    prefix: prefix
    environmentName: environmentName
    location: location
    tags: commonTags
    modelDeploymentName: modelDeploymentName
    supportedLanguages: supportedLanguages
    searchEndpoint: search.outputs.searchEndpoint
    featuresApim: featuresApim
    deployerPrincipalId: deployerPrincipalId
    deployerPrincipalType: deployerPrincipalType
    uamiDeployResourceId: uami.outputs.deployResourceId
  }
}

// ---- Foundry hub + project ------------------------------------------------

module foundry 'modules/foundry-project.bicep' = {
  name: 'foundry-${environmentName}'
  scope: resourceGroup(rgName)
  params: {
    prefix: prefix
    environmentName: environmentName
    location: location
    tags: commonTags
    logAnalyticsId: observability.outputs.logAnalyticsId
    uamiAgentResourceId: uami.outputs.agentResourceId
  }
}

// Model deployment lives on the Foundry account; Realtime + Hosted Agent
// downstream depend on it existing.
module modelDeployment 'modules/model-deployment.bicep' = {
  name: 'modeldeploy-${environmentName}'
  scope: resourceGroup(rgName)
  params: {
    foundryAccountName: foundry.outputs.foundryAccountName
    deploymentName: modelDeploymentName
    modelName: modelName
    modelVersion: modelVersion
    skuName: modelSkuName
    skuCapacity: modelSkuCapacity
  }
}

// Chat-capable model deployment — used by the agent's PromptAgentDefinition
// so the Playground + chat CLI can roundtrip text. `gpt-realtime` (above)
// is voice-only and rejects `/responses`/`/chat/completions` requests.
//
// Bicep dependsOn the realtime deployment to serialise the two
// `Microsoft.CognitiveServices/accounts/deployments` creates against the
// same Foundry account (parallel creates frequently 409).
module chatModelDeployment 'modules/model-deployment.bicep' = {
  name: 'chatmodel-${environmentName}'
  scope: resourceGroup(rgName)
  dependsOn: [
    modelDeployment
  ]
  params: {
    foundryAccountName: foundry.outputs.foundryAccountName
    deploymentName: chatModelDeploymentName
    modelName: chatModelName
    modelVersion: chatModelVersion
    skuName: chatModelSkuName
    skuCapacity: chatModelSkuCapacity
  }
}

// ---- Container Registry + Container Apps Environment ---------------------
//
// ACR holds the `quiz-agent` and `seed-loader` images that `azd deploy`
// builds (via ACR Tasks remote build). The Container Apps Environment is
// the shared host; both the long-running agent dispatcher and the
// one-shot seed job land in it.

module containerRegistry 'modules/container-registry.bicep' = {
  name: 'acr-${environmentName}'
  scope: resourceGroup(rgName)
  params: {
    prefix: prefix
    environmentName: environmentName
    location: location
    tags: commonTags
  }
}

module containerAppsEnv 'modules/container-apps-env.bicep' = {
  name: 'cae-${environmentName}'
  scope: resourceGroup(rgName)
  params: {
    prefix: prefix
    environmentName: environmentName
    location: location
    tags: commonTags
    logAnalyticsWorkspaceId: observability.outputs.logAnalyticsId
    appInsightsConnectionString: observability.outputs.appInsightsConnectionString
  }
}

// ---- RBAC (per-resource, least privilege) ---------------------------------

module rbac 'modules/rbac.bicep' = {
  name: 'rbac-${environmentName}'
  scope: resourceGroup(rgName)
  params: {
    agentPrincipalId: uami.outputs.agentPrincipalId
    indexerPrincipalId: uami.outputs.indexerPrincipalId
    deployPrincipalId: uami.outputs.deployPrincipalId
    keyVaultId: keyVault.outputs.keyVaultId
    keyVaultName: keyVault.outputs.keyVaultName
    appConfigId: appConfig.outputs.appConfigId
    appConfigName: appConfig.outputs.appConfigName
    appInsightsId: observability.outputs.appInsightsId
    appInsightsName: observability.outputs.appInsightsName
    storageAccountId: storage.outputs.storageAccountId
    storageAccountName: storage.outputs.storageAccountName
    searchServiceId: search.outputs.searchServiceId
    searchServiceName: search.outputs.searchServiceName
    foundryAccountId: foundry.outputs.foundryAccountId
    foundryAccountName: foundry.outputs.foundryAccountName
    containerRegistryId: containerRegistry.outputs.registryId
    containerRegistryName: containerRegistry.outputs.registryName
    deployerHumanPrincipalId: deployerPrincipalId
    deployerHumanPrincipalType: deployerPrincipalType
  }
}

// ---- AI Search index control plane (002 TASK-020..TASK-023) --------------
//
// Two paths exist:
//   - `questionsIndex` (deploymentScripts) — gated on `deployAiSearchIndex`,
//     fails on personal-MSA subscriptions (identity-endpoint URI parse error).
//   - Post-provision REST PUT via `infra/hooks/post-provision.sh` — works
//     on every subscription. The deployer's CLI principal needs
//     `Search Service Contributor` on the search service (granted in
//     `infra/modules/rbac.bicep`).
//
// A direct `Microsoft.Search/searchServices/indexes` ARM resource was tried
// and returns opaque `BadRequest` on this schema; deferred as follow-up.

module questionsIndex 'scripts/create-questions-index.bicep' = if (deployAiSearchIndex) {
  name: 'questions-index-${environmentName}'
  scope: resourceGroup(rgName)
  dependsOn: [
    rbac
  ]
  params: {
    searchServiceName: search.outputs.searchServiceName
    uamiDeployResourceId: uami.outputs.deployResourceId
    tags: commonTags
    location: searchLocation
    indexSchema: questionsIndexSchema
    synonymsEn: synonymsEn
    synonymsFr: synonymsFr
    synonymsEs: synonymsEs
  }
}

// ---- Hosted Agent (depends on Foundry project + RBAC + AppConfig + AI) ----

module hostedAgent 'modules/hosted-agent.bicep' = {
  name: 'agent-${environmentName}'
  scope: resourceGroup(rgName)
  dependsOn: [
    rbac
  ]
  params: {
    prefix: prefix
    environmentName: environmentName
    projectId: foundry.outputs.projectId
    projectName: foundry.outputs.projectName
    foundryCustomSubdomain: foundry.outputs.foundryCustomSubdomain
    uamiAgentResourceId: uami.outputs.agentResourceId
    uamiAgentClientId: uami.outputs.agentClientId
    appInsightsConnectionString: observability.outputs.appInsightsConnectionString
    appConfigEndpoint: appConfig.outputs.appConfigEndpoint
    modelDeploymentName: modelDeploymentName
    modelDeploymentId: modelDeployment.outputs.deploymentId
  }
}

// ---- Quiz Agent Container App + Seed Loader Job --------------------------
//
// `quiz-agent` runs the long-lived Foundry tool-dispatcher daemon.
// `seed-loader` is a manual Container Apps Job that runs `seed_index.py`.
// Both pull images from the ACR via UAMI (AcrPull granted by rbac.bicep).
//
// On the FIRST `azd deploy` for each service, ACR Tasks builds the image
// from the corresponding Dockerfile (the service's `docker.remoteBuild`
// flag in azure.yaml). Until that build lands, the Container App will
// fail to pull `:latest` and show as Degraded — that's expected; the
// first `azd deploy` clears it.

module quizAgentApp 'modules/quiz-agent-app.bicep' = {
  name: 'quiz-agent-${environmentName}'
  scope: resourceGroup(rgName)
  dependsOn: [
    rbac
    chatModelDeployment
  ]
  params: {
    prefix: prefix
    environmentName: environmentName
    location: location
    tags: commonTags
    environmentId: containerAppsEnv.outputs.environmentId
    registryLoginServer: containerRegistry.outputs.loginServer
    uamiAgentResourceId: uami.outputs.agentResourceId
    uamiAgentClientId: uami.outputs.agentClientId
    // The Azure AI Projects SDK v2 expects the **project** endpoint, not the
    // account endpoint. Shape: `https://<account>.services.ai.azure.com/api/projects/<project>`.
    // `foundry.outputs.foundryEndpoint` is the account endpoint
    // (`https://<account>.cognitiveservices.azure.com/`); we reshape it
    // here so the agent container's env var is the SDK-friendly form.
    foundryProjectEndpoint: 'https://${foundry.outputs.foundryAccountName}.services.ai.azure.com/api/projects/${foundry.outputs.projectName}'
    foundryProjectName: foundry.outputs.projectName
    modelDeploymentName: modelDeploymentName
    chatModelDeploymentName: chatModelDeploymentName
    // When the MCP server is deployed, surface its public URL so the
    // agent registration adds an MCPTool entry next to the function
    // tools (Playground reaches function-tools-via-MCP, chat CLI uses
    // the inline FunctionTool entries). Empty when the gate is off.
    mcpServerUrl: deployMcpServer ? mcpServerApp!.outputs.mcpUrl : ''
    mcpConnectionName: deployMcpServer ? mcpConnection!.outputs.connectionName : ''
    appInsightsConnectionString: observability.outputs.appInsightsConnectionString
    appConfigEndpoint: appConfig.outputs.appConfigEndpoint
    cosmosEndpoint: cosmos.outputs.cosmosEndpoint
    searchEndpoint: search.outputs.searchEndpoint
    imageRef: resolvedQuizAgentImageRef
  }
}

// ---- Foundry project connection for the MCP server ----------------------
//
// Without this, the agent's MCPTool entry has no way to authenticate to
// our /mcp endpoint and Foundry calls it anonymously → 401. The
// connection stores the URL + tells Foundry to use AAD (its project MI)
// when calling out.
module mcpConnection 'modules/foundry-mcp-connection.bicep' = if (deployMcpServer) {
  name: 'mcp-connection-${environmentName}'
  scope: resourceGroup(rgName)
  dependsOn: [
    mcpServerApp
  ]
  params: {
    foundryAccountName: foundry.outputs.foundryAccountName
    foundryProjectName: foundry.outputs.projectName
    mcpServerUrl: mcpServerApp!.outputs.mcpUrl
    connectionName: 'flint-quiz-mcp'
    apiKey: mcpApiKey
  }
}

// ---- MCP server Container App (Foundry Playground bridge) ---------------
//
// The 5 quiz tools are inline `type:function` tools on the agent (for the
// chat CLI). The MCP server is the parallel bridge that lets the Foundry
// Playground execute the same tools server-side. Both surfaces share
// `build_tools(deps)` so the tool bodies stay single-source-of-truth.
module mcpServerApp 'modules/mcp-server-app.bicep' = if (deployMcpServer) {
  name: 'mcp-server-${environmentName}'
  scope: resourceGroup(rgName)
  dependsOn: [
    rbac
    cosmosDatabase
  ]
  params: {
    prefix: prefix
    environmentName: environmentName
    location: location
    tags: commonTags
    environmentId: containerAppsEnv.outputs.environmentId
    registryLoginServer: containerRegistry.outputs.loginServer
    uamiAgentResourceId: uami.outputs.agentResourceId
    uamiAgentClientId: uami.outputs.agentClientId
    cosmosEndpoint: cosmos.outputs.cosmosEndpoint
    searchEndpoint: search.outputs.searchEndpoint
    apiKey: mcpApiKey
    appInsightsConnectionString: observability.outputs.appInsightsConnectionString
    imageRef: resolvedMcpServerImageRef
  }
}

module seedLoaderJob 'modules/seed-loader-job.bicep' = {
  name: 'seed-loader-${environmentName}'
  scope: resourceGroup(rgName)
  dependsOn: [
    rbac
  ]
  params: {
    prefix: prefix
    environmentName: environmentName
    location: location
    tags: commonTags
    environmentId: containerAppsEnv.outputs.environmentId
    registryLoginServer: containerRegistry.outputs.loginServer
    uamiIndexerResourceId: uami.outputs.indexerResourceId
    uamiIndexerClientId: uami.outputs.indexerClientId
    searchEndpoint: search.outputs.searchEndpoint
    blobEndpoint: storage.outputs.blobEndpoint
    cosmosEndpoint: cosmos.outputs.cosmosEndpoint
    appInsightsConnectionString: observability.outputs.appInsightsConnectionString
    imageRef: resolvedSeedLoaderImageRef
  }
}

// ---- Background sweeper (003 TASK-191) -----------------------------------
// Scheduled Container Apps Job — uses uami-agent-* with Cosmos Data
// Contributor scoped to the sessions container only. Sweep code lives in
// src/sweeper/ (entry point `python -m src.sweeper`) and is deployed via
// `azd deploy sweeper` after this module provisions the host.
//
// Gated on `deploySweeper`. The CAJ host needs no `Microsoft.Web/serverFarms`
// quota (the original blocker on Phase-1 / personal subscriptions); only
// Container Apps Consumption capacity, which is already in the env.
module sweeperJob 'modules/sweeper-job.bicep' = if (deploySweeper) {
  name: 'sweeper-job-${environmentName}'
  scope: resourceGroup(rgName)
  // `cosmosDatabase` is referenced via `outputs.databaseName` /
  // `sessionsContainerName` in the params block below — Bicep infers
  // the dependency from those references and doesn't need it spelled
  // out here.
  dependsOn: [
    rbac
  ]
  params: {
    prefix: prefix
    environmentName: environmentName
    location: location
    tags: commonTags
    environmentId: containerAppsEnv.outputs.environmentId
    registryLoginServer: containerRegistry.outputs.loginServer
    uamiAgentResourceId: uami.outputs.agentResourceId
    uamiAgentClientId: uami.outputs.agentClientId
    cosmosEndpoint: cosmos.outputs.cosmosEndpoint
    cosmosDatabaseName: cosmosDatabase.outputs.databaseName
    cosmosSessionsContainerName: cosmosDatabase.outputs.sessionsContainerName
    appInsightsConnectionString: observability.outputs.appInsightsConnectionString
    imageRef: resolvedSweeperImageRef
  }
}

// ---- Realtime endpoint (derived from the model deployment) ----------------

module realtime 'modules/realtime.bicep' = {
  name: 'realtime-${environmentName}'
  scope: resourceGroup(rgName)
  params: {
    foundryCustomSubdomain: foundry.outputs.foundryCustomSubdomain
    supportedLanguages: supportedLanguages
    voiceMaxSessionMinutes: voiceMaxSessionMinutes
    voiceIdleSeconds: voiceIdleSeconds
    modelDeploymentName: modelDeploymentName
    modelDeploymentId: modelDeployment.outputs.deploymentId
  }
}

// ---- Outputs (non-secret; consumed by azd env get-values + post-provision hook)

output AZURE_RESOURCE_GROUP string = rg.outputs.name
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = subscription().tenantId

output APP_INSIGHTS_CONNECTION_STRING string = observability.outputs.appInsightsConnectionString
output APP_CONFIG_ENDPOINT string = appConfig.outputs.appConfigEndpoint
output KEY_VAULT_URI string = keyVault.outputs.keyVaultUri
output COSMOS_ENDPOINT string = cosmos.outputs.cosmosEndpoint
output SEARCH_ENDPOINT string = search.outputs.searchEndpoint
// Both outputs flow through the conditional `questionsIndex` module
// (gated on `deployAiSearchIndex`). When the gate is off, the seed
// loader at `src/seed/seed_index.py` creates the index; fallback
// values are the static defaults the seed loader also uses.
output SEARCH_INDEX_NAME string = deployAiSearchIndex ? questionsIndex.outputs.indexName : 'questions'
output SEARCH_API_VERSION string = deployAiSearchIndex ? questionsIndex.outputs.searchApiVersion : '2024-07-01'
output BLOB_ENDPOINT string = storage.outputs.blobEndpoint
output FOUNDRY_ACCOUNT_NAME string = foundry.outputs.foundryAccountName
output FOUNDRY_ENDPOINT string = foundry.outputs.foundryEndpoint
output FOUNDRY_PROJECT_ID string = foundry.outputs.projectId
output FOUNDRY_PROJECT_NAME string = foundry.outputs.projectName
output MODEL_DEPLOYMENT_NAME string = modelDeployment.outputs.deploymentName
output MODEL_NAME string = modelDeployment.outputs.modelName
output MODEL_VERSION string = modelDeployment.outputs.modelVersion
output CHAT_MODEL_DEPLOYMENT_NAME string = chatModelDeployment.outputs.deploymentName
output CHAT_MODEL_NAME string = chatModelDeployment.outputs.modelName
output CHAT_MODEL_VERSION string = chatModelDeployment.outputs.modelVersion
output AGENT_NAME string = hostedAgent.outputs.agentName
output AGENT_RUNTIME_ENDPOINT string = hostedAgent.outputs.agentRuntimeEndpoint
output REALTIME_ENDPOINT string = realtime.outputs.realtimeEndpoint

// Container infra — surfaced so `azd deploy` knows the ACR target and
// `make smoke` can `az containerapp job start` the seed-loader.
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerRegistry.outputs.loginServer
output AZURE_CONTAINER_REGISTRY_NAME string = containerRegistry.outputs.registryName
output AZURE_CONTAINER_APPS_ENVIRONMENT_NAME string = containerAppsEnv.outputs.environmentName
output QUIZ_AGENT_CONTAINER_APP_NAME string = quizAgentApp.outputs.containerAppName
output SEED_LOADER_JOB_NAME string = seedLoaderJob.outputs.jobName
output SWEEPER_JOB_NAME string = deploySweeper ? sweeperJob!.outputs.jobName : ''

output UAMI_AGENT_CLIENT_ID string = uami.outputs.agentClientId
output UAMI_AGENT_RESOURCE_ID string = uami.outputs.agentResourceId
output UAMI_INDEXER_CLIENT_ID string = uami.outputs.indexerClientId
output UAMI_DEPLOY_CLIENT_ID string = uami.outputs.deployClientId

output KEY_VAULT_NAME string = keyVault.outputs.keyVaultName
output COSMOS_ACCOUNT_NAME string = cosmos.outputs.cosmosAccountName
output SEARCH_SERVICE_NAME string = search.outputs.searchServiceName
output STORAGE_ACCOUNT_NAME string = storage.outputs.storageAccountName
output APP_CONFIG_NAME string = appConfig.outputs.appConfigName
output APP_INSIGHTS_NAME string = observability.outputs.appInsightsName
output LOG_ANALYTICS_NAME string = observability.outputs.logAnalyticsName

output COSMOS_SESSIONS_TTL_DAYS int = cosmosSessionsTtlDays
output AUDIT_TTL_DAYS int = auditTtlDays
output VOICE_MAX_SESSION_MINUTES int = voiceMaxSessionMinutes
output VOICE_IDLE_SECONDS int = voiceIdleSeconds
output FEATURES_APIM bool = featuresApim

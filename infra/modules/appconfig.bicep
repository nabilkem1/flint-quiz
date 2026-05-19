// App Configuration store with local auth disabled.
//
// The four seed keys (model:deploymentName, search:endpoint,
// languages:supported, features:apim) are seeded out-of-band via
// `az appconfig kv set --auth-mode login` for v1 — see the NOTE block below
// for rationale. Index/container schema does NOT seed here — that lives in
// 002 and 003.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

@description('Model deployment name to seed at model:deploymentName')
param modelDeploymentName string

@description('Supported language codes (used to seed languages:supported)')
param supportedLanguages array

@description('Search service endpoint URL to seed at search:endpoint')
param searchEndpoint string

@description('Whether APIM is enabled (seeded at features:apim)')
param featuresApim bool

@description('Object ID of the principal running `azd provision`. Receives App Configuration Data Owner ONLY so that ARM can seed the four key-values below via pass-through. Runtime reads are via uami-agent-* with App Configuration Data Reader (see rbac.bicep) — strictly less privilege.')
param deployerPrincipalId string

@description('AAD principal type of deployerPrincipalId. Use `User` for an interactive azd run, `ServicePrincipal` for CI (uami-deploy-*).')
@allowed([
  'User'
  'ServicePrincipal'
  'Group'
])
param deployerPrincipalType string = 'User'

@description('UAMI resource ID used to run the RBAC propagation wait deployment script (uami-deploy-*). Required only because Microsoft.Resources/deploymentScripts cannot run as a user principal.')
param uamiDeployResourceId string

// Globally unique: hyphen-friendly, lower bound is 5 chars
var suffix = uniqueString(resourceGroup().id, environmentName)
var appcsName = take('${prefix}-${environmentName}-appcs-${suffix}', 50)

resource appcs 'Microsoft.AppConfiguration/configurationStores@2024-05-01' = {
  name: appcsName
  location: location
  tags: tags
  sku: {
    name: 'standard'
  }
  properties: {
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    // With disableLocalAuth=true, ARM cannot write child key-value resources
    // via key-based data-plane access. Pass-through tells ARM to use the
    // *deployer's* AAD identity for data-plane writes — which then needs the
    // role assignment below. Runtime stays MI-only.
    dataPlaneProxy: {
      authenticationMode: 'Pass-through'
      privateLinkDelegation: 'Disabled'
    }
  }
}

// NOTE: The four required key-values (model:deploymentName, search:endpoint,
// languages:supported, features:apim) are NOT declared as Bicep resources
// because Microsoft.AppConfiguration/configurationStores/keyValues writes go
// through the AppConfig data plane, which races RBAC propagation when the
// store has disableLocalAuth=true + dataPlaneProxy.authenticationMode=Pass-through.
// Even with a 90s sleep deployment script, interactive azd deploys
// (especially with personal MSA accounts) consistently 403.
//
// For v1 the keys are seeded out-of-band via `az appconfig kv set --auth-mode login`
// against the existing Data Owner role assignment created above. The keys are
// listed in infra/README.md §14 and the post-provision hook verifies them.
//
// FUTURE (cleanup task — not blocking Phase 1):
//   Replace this out-of-band seed with a Microsoft.Resources/deploymentScripts
//   resource running as uami-deploy-* (granted App Configuration Data Owner
//   permanently as a CI principal). That eliminates the human-deployer
//   identity dependency and re-enables fully declarative seeding.
//
// The unused parameters below (deployerPrincipalId, deployerPrincipalType,
// uamiDeployResourceId, modelDeploymentName, supportedLanguages,
// searchEndpoint, featuresApim) are retained on the module signature so the
// future cleanup needs zero wiring changes in main.bicep — just toggle the
// block back on.

output appConfigId string = appcs.id
output appConfigName string = appcs.name
output appConfigEndpoint string = appcs.properties.endpoint

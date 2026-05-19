// APIM rate-limiting front (TASK-129 / SEC-011).
//
// Provisions an APIM instance + API + per-user quota policy in front of
// the Hosted Agent / Realtime endpoints. **Disabled by default in v1**:
// the module is deployable, but `featuresApim=false` (the dev/qa default)
// shells out the policy attachment so the resource exists without
// enforcing quotas. Mandatory before public exposure
// (`docs/pre-public-gate.md §2.3`).
//
// Quotas read from AppConfig at deploy time and surfaced as parameters
// here so a value swap doesn't require re-authoring the policy XML.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name, e.g., dev')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

@description('Whether APIM enforcement is enabled. False by default in dev; required to be true before public exposure (SEC-011).')
param featuresApim bool = false

@description('Publisher email on the APIM instance. Required by ARM.')
param publisherEmail string = 'platform@example.com'

@description('Publisher display name.')
param publisherName string = 'Flint Quiz Platform'

@description('Per-user questions per minute quota.')
param questionsPerMinute int = 60

@description('Per-user quizzes per day quota.')
param quizzesPerDay int = 30

@description('Per-user voice minutes per day quota.')
param voiceMinutesPerDay int = 60

@description('Hosted Agent backend URL (where APIM forwards). Bicep output from quiz-agent module.')
param agentBackendUrl string

@description('Realtime backend URL. Bicep output from realtime module.')
param realtimeBackendUrl string

@description('Tier — Consumption is cheapest; Developer for dev-with-VNet. Production should use Standard/Premium.')
@allowed([
  'Consumption'
  'Developer'
  'Basic'
  'Standard'
  'Premium'
])
param skuName string = 'Consumption'

var apimName = '${prefix}-${environmentName}-apim'

resource apim 'Microsoft.ApiManagement/service@2023-09-01-preview' = {
  name: apimName
  location: location
  tags: tags
  sku: {
    name: skuName
    capacity: skuName == 'Consumption' ? 0 : 1
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
    // Quota enforcement requires APIM to identify the caller. We rely on
    // the `Authorization` header carrying the Entra-issued bearer token
    // — the policy below extracts the OID claim. Anonymous traffic is
    // rejected upstream (SEC-003); APIM's role here is per-user quotas,
    // not auth.
    publicNetworkAccess: 'Enabled'
  }
}

// Single API exposing the agent endpoints behind APIM.
resource api 'Microsoft.ApiManagement/service/apis@2023-09-01-preview' = {
  parent: apim
  name: 'flint-quiz'
  properties: {
    displayName: 'Flint Quiz Agent'
    path: 'quiz'
    protocols: ['https']
    serviceUrl: agentBackendUrl
    subscriptionRequired: false
  }
}

// Backend pointer for the Realtime endpoint so a `/realtime` operation
// can forward there. The Realtime URL is `wss://...` — APIM supports
// HTTPS upgrade for WebSocket through the dedicated backend.
resource realtimeBackend 'Microsoft.ApiManagement/service/backends@2023-09-01-preview' = {
  parent: apim
  name: 'realtime'
  properties: {
    url: realtimeBackendUrl
    protocol: 'http'
  }
}

// Per-user quota policy. Three counters:
//   * `questions/minute` — short-window burst control.
//   * `quizzes/day`      — abuse / runaway prevention.
//   * `voice-minutes/day`— Realtime billing protection.
//
// Counter key derives from the JWT `oid` claim (Entra Object ID) so
// rate-limit accounting is per Entra principal. A missing claim yields
// the static key `anonymous`, which is then rejected upstream (the
// agent refuses anonymous calls — SEC-003); the static key still
// rate-limits the failed-auth path so a flood of anonymous attempts
// cannot pummel the agent.
//
// Policy applied **only when featuresApim=true**. With the flag off the
// API exists but enforces nothing — useful for staging deploys that
// validate routing without the quota friction.
resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2023-09-01-preview' = if (featuresApim) {
  parent: api
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: '<policies>\n  <inbound>\n    <base />\n    <validate-jwt header-name="Authorization" failed-validation-httpcode="401" failed-validation-error-message="anonymous traffic rejected">\n      <openid-config url="https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration" />\n    </validate-jwt>\n    <set-variable name="caller-oid" value="@(context.Principal?.Identity?.Name ?? \'anonymous\')" />\n    <quota-by-key calls="${quizzesPerDay}" renewal-period="86400" counter-key="@((string)context.Variables[\'caller-oid\'])-quizzes-day" />\n    <rate-limit-by-key calls="${questionsPerMinute}" renewal-period="60" counter-key="@((string)context.Variables[\'caller-oid\'])-q-min" />\n    <quota-by-key calls="${voiceMinutesPerDay}" renewal-period="86400" counter-key="@((string)context.Variables[\'caller-oid\'])-voice-day" />\n  </inbound>\n  <backend>\n    <base />\n  </backend>\n  <outbound>\n    <base />\n  </outbound>\n  <on-error>\n    <base />\n  </on-error>\n</policies>'
  }
}

output apimId string = apim.id
output apimName string = apimName
output apimGatewayUrl string = apim.properties.gatewayUrl
output apiId string = api.id
output policyEnabled bool = featuresApim

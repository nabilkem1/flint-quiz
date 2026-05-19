// Foundry Realtime endpoint configuration surface.
//
// In the new Foundry, the Realtime endpoint is NOT a separate ARM resource —
// it's the protocol surface exposed by the Foundry account whenever a
// realtime-capable model (e.g., gpt-4o-realtime-preview) has been deployed
// via model-deployment.bicep. This module therefore does not provision
// anything new; it computes the realtime URL, captures the per-language voice
// allowlist, and codifies the session-length cap (NFR-013) + idle timeout so
// that 006 (voice integration) reads them from a single source.
//
// Voice allowlist and caps live in AppConfig at runtime (006); this module
// emits them as outputs so main.bicep can flow them into the env file
// `azd env get-values` writes for the agent runtime.

@description('Foundry custom subdomain (used to derive the realtime endpoint)')
param foundryCustomSubdomain string

@description('Supported languages whose voice profiles must be configured')
param supportedLanguages array

@description('Max length of a single voice session in minutes (NFR-013)')
param voiceMaxSessionMinutes int

@description('Idle seconds before the realtime channel auto-disconnects')
param voiceIdleSeconds int

@description('Model deployment name the realtime endpoint uses')
param modelDeploymentName string

@description('Model deployment resource ID — dependency anchor so this module sequences after the model is deployed')
param modelDeploymentId string

// Default per-language voice picks (Foundry/OpenAI Realtime catalog).
// Centralised here so 006 reads the same map.
var voiceByLanguage = {
  en: 'alloy'
  fr: 'shimmer'
  es: 'verse'
}

// Validate supportedLanguages match the voice map keys we actually deploy.
// Bicep doesn't have set assertions, but emitting both lets the post-provision
// hook compare them and fail if 006 ever passes an unmapped language.
var configuredLanguages = supportedLanguages

// Touch the model deployment ID so this module sequences after the model is
// available — without an explicit dependsOn that would clutter the call site.
var dependencyAnchor = modelDeploymentId

output realtimeEndpoint string = 'wss://${foundryCustomSubdomain}.openai.azure.com/openai/realtime?deployment=${modelDeploymentName}'
output voicesByLanguage object = voiceByLanguage
output configuredLanguages array = configuredLanguages
output voiceMaxSessionMinutes int = voiceMaxSessionMinutes
output voiceIdleSeconds int = voiceIdleSeconds
output realtimeDependencyAnchor string = dependencyAnchor

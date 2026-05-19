// Quiz Agent Container App — the long-running Foundry tool-dispatcher daemon.
//
// Runs as `uami-agent-*` (granted by rbac.bicep). Pulls its image from the
// ACR via the same UAMI (AcrPull). Container Apps' default probe is TCP
// against the exposed port; the agent opens an `accept-and-close` socket
// on $PORT for liveness.
//
// `imageTag` defaults to `latest` so the first `azd deploy` works before
// any tagged build exists; production deploys should pass an immutable
// tag (`v1.2.3`) to keep revisions deterministic.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

@description('Managed Environment resource ID (from container-apps-env.bicep)')
param environmentId string

@description('ACR login server (e.g., fqdevacrxxx.azurecr.io)')
param registryLoginServer string

@description('Full image reference. Default is the public Microsoft hello-world bootstrap image so the FIRST `azd provision` succeeds before any image is pushed to ACR. After `azd deploy` runs once, the Container App carries a real `<acr>/quiz-agent:<sha>` image — to prevent the next `azd provision` from reverting it back to the bootstrap, the consumer reads the currently-deployed image via an `existing` resource (in main.bicep) and passes it here.')
param imageRef string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('UAMI resource ID the agent authenticates as')
param uamiAgentResourceId string

@description('UAMI client ID surfaced to the container env (AZURE_CLIENT_ID)')
param uamiAgentClientId string

@description('Foundry project endpoint the SDK calls')
param foundryProjectEndpoint string

@description('Foundry project name (informational)')
param foundryProjectName string

@description('Model deployment name (Realtime voice — gpt-realtime).')
param modelDeploymentName string

@description('Chat-capable model deployment name (text — gpt-4o-mini). Used by the agent\'s PromptAgentDefinition so the Playground + chat CLI can roundtrip text. Realtime models reject /responses and /chat/completions.')
param chatModelDeploymentName string

@description('Public MCP server URL (https://<fqdn>/mcp). When non-empty, the agent registration adds an MCPTool entry so the Foundry Playground can reach the same 5 tools. Empty disables — chat CLI / function-tool path stays the only way to invoke tools.')
param mcpServerUrl string = ''

@description('Foundry project connection name for the MCP server (created by modules/foundry-mcp-connection.bicep). Agent registration uses this to authenticate via AAD against /mcp.')
param mcpConnectionName string = ''

@description('App Insights connection string (non-secret per Microsoft guidance)')
param appInsightsConnectionString string

@description('App Configuration endpoint URL')
param appConfigEndpoint string

@description('Cosmos endpoint')
param cosmosEndpoint string

@description('AI Search endpoint')
param searchEndpoint string

@description('Subscription ID surfaced to the container env')
param subscriptionId string = subscription().subscriptionId

var appName = '${prefix}-${environmentName}-quiz-agent'

// The `azd-service-name` tag tells `azd deploy` which ARM resource backs
// the `quiz-agent` service entry in `azure.yaml`. Without it, `azd deploy`
// fails with "unable to find a resource tagged with azd-service-name".
resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: union(tags, {
    'azd-service-name': 'quiz-agent'
  })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiAgentResourceId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      // No public ingress — the agent does not expose an HTTP API; it
      // communicates outbound to Foundry via the SDK. Health probes are
      // TCP-only against `$PORT` for Container Apps' liveness check.
      activeRevisionsMode: 'Single'
      registries: [
        {
          server: registryLoginServer
          identity: uamiAgentResourceId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'quiz-agent'
          image: imageRef
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Liveness'
              tcpSocket: {
                port: 8080
              }
              initialDelaySeconds: 20
              periodSeconds: 30
              failureThreshold: 3
            }
          ]
          env: [
            // Identity — the agent uses DefaultAzureCredential with this
            // client ID to resolve the UAMI.
            { name: 'AZURE_CLIENT_ID', value: uamiAgentClientId }
            { name: 'AZURE_SUBSCRIPTION_ID', value: subscriptionId }
            { name: 'AZURE_ENV_NAME', value: environmentName }
            // Foundry surface
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
            { name: 'FOUNDRY_PROJECT_NAME', value: foundryProjectName }
            { name: 'AGENT_NAME', value: '${prefix}-${environmentName}-agent' }
            { name: 'MODEL_DEPLOYMENT_NAME', value: modelDeploymentName }
            { name: 'CHAT_MODEL_DEPLOYMENT_NAME', value: chatModelDeploymentName }
            { name: 'MCP_SERVER_URL', value: mcpServerUrl }
            { name: 'MCP_CONNECTION_NAME', value: mcpConnectionName }
            // Data plane
            { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'SEARCH_ENDPOINT', value: searchEndpoint }
            { name: 'APP_CONFIG_ENDPOINT', value: appConfigEndpoint }
            // Telemetry — connection string is non-secret per Microsoft.
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'AZURE_AI_TRACING_ENABLED', value: 'true' }
            { name: 'PORT', value: '8080' }
            { name: 'LOG_LEVEL', value: 'INFO' }
          ]
        }
      ]
      scale: {
        // One replica is enough for v1 — the agent runtime is single-
        // process by design (state lives in Cosmos). Scaling beyond 1
        // adds dispatcher contention without buying throughput.
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

output containerAppId string = app.id
output containerAppName string = appName

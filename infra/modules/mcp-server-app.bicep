// MCP Server Container App — exposes the 5 quiz tools to the Foundry
// Playground via HTTP + JSON-RPC. Unlike the quiz-agent, this one has
// **public ingress** because Foundry's runtime calls it from outside
// the Container Apps managed environment.
//
// Security model: ingress is public but every request must carry an
// Entra Bearer token; `src/mcp/auth.py` validates the signature, issuer,
// and `oid` against the allowlist threaded in via
// `mcpTrustedPrincipalOids`. The agent UAMI is in that list so future
// CI / chat-CLI scenarios can also reach the endpoint.
//
// Identity: runs as `uami-agent-*` — Cosmos Data Contributor + AI Search
// reader, exactly the same data-plane RBAC the quiz-agent container has.

@description('Naming prefix')
param prefix string

@description('Environment name')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

@description('Container Apps managed environment resource ID')
param environmentId string

@description('ACR login server')
param registryLoginServer string

@description('Full image reference. Bootstrap default; replaced by `azd deploy mcp-server`.')
param imageRef string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Agent UAMI resource ID (Cosmos + Search data plane)')
param uamiAgentResourceId string

@description('Agent UAMI client ID — surfaced as AZURE_CLIENT_ID')
param uamiAgentClientId string

@description('Cosmos account endpoint')
param cosmosEndpoint string

@description('AI Search endpoint')
param searchEndpoint string

@description('Entra tenant ID — needed for JWT validation in src/mcp/auth.py.')
param tenantId string

@description('Comma-separated allowlist of Entra principal OIDs the MCP server accepts. Wired at deploy time to the Foundry account SAMI + the agent UAMI principal IDs.')
param mcpTrustedPrincipalOids string

@description('App Insights connection string (non-secret)')
param appInsightsConnectionString string

var appName = '${prefix}-${environmentName}-mcp-server'

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: union(tags, {
    'azd-service-name': 'mcp-server'
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
      activeRevisionsMode: 'Single'
      // PUBLIC ingress — Foundry's runtime needs to reach /mcp from outside
      // the env. Authentication is enforced by the application layer
      // (`src/mcp/auth.py`), not by network boundary.
      ingress: {
        external: true
        targetPort: 8080
        transport: 'http'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
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
          name: 'mcp-server'
          image: imageRef
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8080
              }
              initialDelaySeconds: 15
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/healthz'
                port: 8080
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
          env: [
            { name: 'AZURE_CLIENT_ID', value: uamiAgentClientId }
            { name: 'AZURE_TENANT_ID', value: tenantId }
            { name: 'AZURE_ENV_NAME', value: environmentName }
            { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'SEARCH_ENDPOINT', value: searchEndpoint }
            { name: 'SEARCH_INDEX_NAME', value: 'questions' }
            { name: 'MCP_TRUSTED_PRINCIPAL_OIDS', value: mcpTrustedPrincipalOids }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'LOG_LEVEL', value: 'INFO' }
            { name: 'PORT', value: '8080' }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output containerAppId string = app.id
output containerAppName string = appName
output mcpFqdn string = app.properties.configuration.ingress.fqdn
output mcpUrl string = 'https://${app.properties.configuration.ingress.fqdn}/mcp'

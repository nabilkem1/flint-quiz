// Foundry project connection that registers our MCP server with
// **API-key (CustomKeys)** authentication.
//
// Why CustomKeys instead of ProjectManagedIdentity / AAD:
//   The Foundry Playground enforces a guardrail that rejects forwarding
//   any Entra-issued token (including the project MI's own token) to an
//   MCP endpoint not on its trusted endpoints list. Our custom Container
//   App `/mcp` is not on that list, so AAD-based auth surfaces as
//   `tool_user_error: Cannot pass Microsoft token to untrusted MCP
//   endpoint or connector`. Switching to a static API key sidesteps the
//   guardrail entirely — Foundry stores the key in the connection record
//   and presents it on the wire; no Entra token is involved.
//
// Schema reference:
//   https://learn.microsoft.com/azure/templates/microsoft.cognitiveservices/2026-03-01/accounts/projects/connections
//
//   - category   = "RemoteTool"
//   - authType   = "CustomKeys"
//   - target     = the MCP /mcp endpoint URL on our Container App.
//   - credentials.keys = the header set Foundry attaches to outgoing
//     requests. `X-API-Key` matches what `src/mcp/auth.py` reads.
//
// Key rotation: change the value parameter on next `azd provision`. The
// MCP server container picks up the new value from its env (which the
// `mcp-server-app.bicep` module reads from the same param), and Foundry
// stores the new value in the connection record in the same deploy.

@description('Foundry account name (output of foundry.bicep)')
param foundryAccountName string

@description('Foundry project name (output of foundry.bicep)')
param foundryProjectName string

@description('Connection name. Constraint: ^[a-zA-Z0-9][a-zA-Z0-9_-]{2,32}$ — must start with alphanumeric, 3-33 chars total.')
param connectionName string = 'flint-quiz-mcp'

@description('Full MCP /mcp endpoint URL (e.g. https://<fqdn>/mcp)')
param mcpServerUrl string

@secure()
@description('Shared API key Foundry presents on the `X-API-Key` header when calling /mcp. Same value the MCP server validates server-side. Wired in main.bicep so both modules see the same string in a single deploy.')
param apiKey string

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2026-03-01' existing = {
  name: '${foundryAccountName}/${foundryProjectName}'
}

resource mcpConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2026-03-01' = {
  parent: foundryProject
  name: connectionName
  properties: {
    category: 'RemoteTool'
    target: mcpServerUrl
    authType: 'CustomKeys'
    credentials: {
      keys: {
        'X-API-Key': apiKey
      }
    }
    isSharedToAll: true
    metadata: {
      ApiType: 'MCP'
    }
  }
}

output connectionId string = mcpConnection.id
output connectionName string = mcpConnection.name

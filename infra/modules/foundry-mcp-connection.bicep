// Foundry project connection that registers our MCP server with
// Microsoft Entra (project-managed-identity) authentication.
//
// Schema reference:
//   https://learn.microsoft.com/azure/templates/microsoft.cognitiveservices/2026-03-01/accounts/projects/connections
//
//   - category = "RemoteTool" — the MCP server category. (The Python SDK
//     enum surfaces this as `RemoteTool_Preview` for backwards compat;
//     the underlying ARM API expects bare `RemoteTool`.)
//   - authType = "ProjectManagedIdentity" — Foundry's project-managed
//     identity acquires an Entra token and presents it to the MCP
//     server. Our server-side `src/mcp/auth.py` validates signature +
//     `oid` against the allowlist threaded into the mcp-server
//     Container App. Note: the generic ARM `Microsoft.CognitiveServices
//     /accounts/projects/connections` schema lists "AAD" as a valid
//     authType, but the `RemoteTool` category specifically rejects
//     "AAD" with a ValidationError — the accepted subset is None /
//     CustomKeys / ProjectManagedIdentity / OAuth2 / DeveloperConnection
//     / UserEntraToken / AgentUserImpersonation / AgenticIdentityToken
//     / AgenticUser / UserTokenAndProjectManagedIdentity.
//   - target = the MCP /mcp endpoint URL on our Container App.
//
// When this connection exists, the agent registration code at
// `src/agent/__main__.py::register_foundry_agent` (the MCPTool entry)
// should reference it via `project_connection_id = <this resource name>`
// so Foundry knows to authenticate before calling our MCP server.

@description('Foundry account name (output of foundry.bicep)')
param foundryAccountName string

@description('Foundry project name (output of foundry.bicep)')
param foundryProjectName string

@description('Connection name. Constraint: ^[a-zA-Z0-9][a-zA-Z0-9_-]{2,32}$ — must start with alphanumeric, 3-33 chars total.')
param connectionName string = 'flint-quiz-mcp'

@description('Full MCP /mcp endpoint URL (e.g. https://<fqdn>/mcp)')
param mcpServerUrl string

@description('Entra audience the project MI requests a token for when calling the MCP server. Our server-side `src/mcp/auth.py` validates signature + `oid` against an allowlist but does NOT verify audience, so any well-known Azure audience works. `https://cognitiveservices.azure.com` is documented as the canonical choice for Foundry-backend connections.')
param audience string = 'https://cognitiveservices.azure.com'

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2026-03-01' existing = {
  name: '${foundryAccountName}/${foundryProjectName}'
}

resource mcpConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2026-03-01' = {
  parent: foundryProject
  name: connectionName
  properties: {
    category: 'RemoteTool'
    target: mcpServerUrl
    authType: 'ProjectManagedIdentity'
    // `audience` lives at the properties level (not inside metadata).
    // The ARM schema for connections doesn't formally type it, but the
    // RemoteTool / ProjectManagedIdentity path requires it — without
    // it Foundry's MI token-acquisition fails with
    // `BadRequest: Missing required query parameter 'audience'`.
    audience: audience
    isSharedToAll: true
    metadata: {
      ApiType: 'MCP'
    }
  }
}

output connectionId string = mcpConnection.id
output connectionName string = mcpConnection.name

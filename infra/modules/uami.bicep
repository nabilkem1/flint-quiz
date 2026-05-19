// Three User-Assigned Managed Identities, one per workload role.
// UAMI (not SAMI) so principal IDs survive Hosted Agent re-creation
// and RBAC can be wired before the consuming resource exists.
// See infra/README.md §3.1.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name, e.g., dev')
param environmentName string

@description('Azure region for the identities')
param location string

@description('Mandatory tags')
param tags object

resource uamiAgent 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-${environmentName}-uami-agent'
  location: location
  tags: tags
}

resource uamiIndexer 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-${environmentName}-uami-indexer'
  location: location
  tags: tags
}

resource uamiDeploy 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-${environmentName}-uami-deploy'
  location: location
  tags: tags
}

output agentPrincipalId string = uamiAgent.properties.principalId
output agentClientId string = uamiAgent.properties.clientId
output agentResourceId string = uamiAgent.id
output agentName string = uamiAgent.name

output indexerPrincipalId string = uamiIndexer.properties.principalId
output indexerClientId string = uamiIndexer.properties.clientId
output indexerResourceId string = uamiIndexer.id
output indexerName string = uamiIndexer.name

output deployPrincipalId string = uamiDeploy.properties.principalId
output deployClientId string = uamiDeploy.properties.clientId
output deployResourceId string = uamiDeploy.id
output deployName string = uamiDeploy.name

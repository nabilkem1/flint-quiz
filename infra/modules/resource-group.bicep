// Creates the env-scoped resource group with mandatory tags.
// Called from main.bicep at subscription scope.
targetScope = 'subscription'

@description('Resource group name (e.g., fq-dev-rg)')
param name string

@description('Azure region')
param location string

@description('Mandatory tags applied to the RG itself; child resources inherit via param')
param tags object

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: name
  location: location
  tags: tags
}

output id string = rg.id
output name string = rg.name
output location string = rg.location

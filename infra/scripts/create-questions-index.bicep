// Bicep deployment script that creates / updates the AI Search `questions`
// index and three per-language synonym maps (en, fr, es).
//
// Control plane only. Runs under uami-deploy-* (has Search Service Contributor
// on the search service — see infra/modules/rbac.bicep). The seed loader in
// src/seed/seed_index.py runs under uami-indexer-* (data plane only, has
// Search Index Data Contributor) and would abort at startup if granted control
// plane rights — see TASK-026.
//
// This is idempotent: a re-run upserts the synonym maps via PUT, then upserts
// the index. Schema changes that AI Search cannot apply in place (e.g. analyzer
// changes on existing fields) require a versioned re-build — the script logs
// the error and exits non-zero so CI surfaces the migration need.

@description('Search service name (output from search.bicep).')
param searchServiceName string

@description('Resource ID of uami-deploy-* (Search Service Contributor).')
param uamiDeployResourceId string

@description('Mandatory tags.')
param tags object

@description('Azure region for the deployment script container.')
param location string

@description('Index schema body. Loaded as JSON from questions-index-schema.json by the caller.')
param indexSchema object

@description('Synonyms map for English (per-language namespacing — TASK-022).')
param synonymsEn array

@description('Synonyms map for French.')
param synonymsFr array

@description('Synonyms map for Spanish.')
param synonymsEs array

@description('Force-update token. Set to a unique value (e.g. utcNow) to force the deployment script to re-run on every deployment.')
param forceUpdateTag string = utcNow()

// AI Search REST API version. Index schema features (semantic config v2,
// vectorSearch, complex types) tie to this. Bump deliberately.
var searchApiVersion = '2024-07-01'

var searchEndpoint = 'https://${searchServiceName}.search.windows.net'

// Synonym maps are uploaded as `\n`-separated mapping lines per the REST
// API contract. We pre-join here so the deployment script body stays small.
var synonymsEnBody = join(synonymsEn, '\n')
var synonymsFrBody = join(synonymsFr, '\n')
var synonymsEsBody = join(synonymsEs, '\n')

resource createQuestionsIndex 'Microsoft.Resources/deploymentScripts@2023-08-01' = {
  name: 'create-questions-index-${uniqueString(searchServiceName)}'
  location: location
  tags: tags
  kind: 'AzurePowerShell'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiDeployResourceId}': {}
    }
  }
  properties: {
    azPowerShellVersion: '11.5'
    forceUpdateTag: forceUpdateTag
    retentionInterval: 'PT1H'
    cleanupPreference: 'OnSuccess'
    timeout: 'PT15M'
    environmentVariables: [
      {
        name: 'SEARCH_ENDPOINT'
        value: searchEndpoint
      }
      {
        name: 'SEARCH_API_VERSION'
        value: searchApiVersion
      }
      {
        name: 'INDEX_SCHEMA_JSON'
        value: string(indexSchema)
      }
      {
        name: 'SYNONYMS_EN'
        value: synonymsEnBody
      }
      {
        name: 'SYNONYMS_FR'
        value: synonymsFrBody
      }
      {
        name: 'SYNONYMS_ES'
        value: synonymsEsBody
      }
    ]
    scriptContent: '''
      $ErrorActionPreference = 'Stop'

      # Token: deployment script identity is uami-deploy-*; AAD token for
      # https://search.azure.com is the AI Search data-plane audience.
      $tokenResponse = Invoke-RestMethod `
        -Uri "$env:IDENTITY_ENDPOINT?resource=https://search.azure.com/&api-version=2019-08-01" `
        -Headers @{ 'X-IDENTITY-HEADER' = $env:IDENTITY_HEADER }
      $token = $tokenResponse.access_token
      $headers = @{
        Authorization  = "Bearer $token"
        'Content-Type' = 'application/json'
      }

      function Put-Synonyms($name, $body) {
        if ([string]::IsNullOrWhiteSpace($body)) {
          Write-Host "skipping empty synonym map $name"
          return
        }
        $uri = "$env:SEARCH_ENDPOINT/synonymmaps/$($name)?api-version=$env:SEARCH_API_VERSION"
        $payload = @{
          name     = $name
          format   = 'solr'
          synonyms = $body
        } | ConvertTo-Json -Depth 4
        Write-Host "PUT synonym map $name"
        Invoke-RestMethod -Method Put -Uri $uri -Headers $headers -Body $payload | Out-Null
      }

      Put-Synonyms 'topic-synonyms-en' $env:SYNONYMS_EN
      Put-Synonyms 'topic-synonyms-fr' $env:SYNONYMS_FR
      Put-Synonyms 'topic-synonyms-es' $env:SYNONYMS_ES

      # Index PUT must follow synonym map PUTs (the index schema references
      # the synonym maps by name).
      $indexBody = $env:INDEX_SCHEMA_JSON
      $indexName = (ConvertFrom-Json -InputObject $indexBody).name
      $indexUri  = "$env:SEARCH_ENDPOINT/indexes/$($indexName)?api-version=$env:SEARCH_API_VERSION&allowIndexDowntime=true"
      Write-Host "PUT index $indexName"
      Invoke-RestMethod -Method Put -Uri $indexUri -Headers $headers -Body $indexBody | Out-Null

      $DeploymentScriptOutputs = @{
        indexName         = $indexName
        synonymMaps       = @('topic-synonyms-en', 'topic-synonyms-fr', 'topic-synonyms-es')
        searchApiVersion  = $env:SEARCH_API_VERSION
      }
    '''
  }
}

output indexName string = createQuestionsIndex.properties.outputs.indexName
output searchApiVersion string = searchApiVersion

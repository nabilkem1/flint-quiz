// Seed-Loader Container Apps Job — one-shot reindex of the question bank.
//
// Runs as `uami-indexer-*` (Search Index Data Contributor + Storage
// Blob Data Reader). The loader script ABORTS if it detects
// `Search Service Contributor` on its identity (defence in depth).
//
// Trigger: manual via `az containerapp job start -n <jobName> -g <rg>`.
// Wired into `make smoke` and the post-deploy hook.

@description('Naming prefix')
param prefix string

@description('Environment name')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

@description('Managed Environment resource ID')
param environmentId string

@description('ACR login server')
param registryLoginServer string

@description('Full image reference. Default is the public hello-world bootstrap image so Bicep can create the Job before any seed image is pushed. `azd deploy seed-loader` replaces it with `<acr>/seed-loader:<sha>`.')
param imageRef string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('UAMI resource ID — uami-indexer-*')
param uamiIndexerResourceId string

@description('UAMI client ID — surfaced as AZURE_CLIENT_ID')
param uamiIndexerClientId string

@description('Search endpoint URL')
param searchEndpoint string

@description('Storage account Blob endpoint (authoring source-of-truth)')
param blobEndpoint string

@description('App Insights connection string (non-secret)')
param appInsightsConnectionString string

@description('Cosmos account endpoint. The job chains `seed_topics` after `seed_index` on a single firing so the post-provision flow stays one CAJ trigger; that step needs Cosmos write to the `topics` container (granted to the indexer UAMI in cosmos-database.bicep).')
param cosmosEndpoint string

var jobName = '${prefix}-${environmentName}-seed-loader'

// `azd-service-name: seed-loader` lets `azd deploy seed-loader` find this
// Container Apps Job as the deploy target.
resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: union(tags, {
    'azd-service-name': 'seed-loader'
  })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiIndexerResourceId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      // Manual trigger — invoked by operator / post-deploy hook.
      triggerType: 'Manual'
      replicaTimeout: 600
      replicaRetryLimit: 1
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: [
        {
          server: registryLoginServer
          identity: uamiIndexerResourceId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'seed-loader'
          image: imageRef
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'AZURE_CLIENT_ID', value: uamiIndexerClientId }
            { name: 'AZURE_ENV_NAME', value: environmentName }
            { name: 'SEARCH_ENDPOINT', value: searchEndpoint }
            { name: 'BLOB_ENDPOINT', value: blobEndpoint }
            { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'LOG_LEVEL', value: 'INFO' }
          ]
          // Chain `seed_index` (AI Search) then `seed_topics` (Cosmos) on
          // one firing so the post-provision hook only has to trigger one
          // CAJ. `&&` (vs `;`) means a seed_index failure short-circuits
          // and the job exits non-zero — the operator sees the incident.
          // `--acknowledge-identity-check-skipped` on seed_index is because
          // the in-script role probe needs the AI Search resource ID we
          // don't thread through here; identity is already enforced by the
          // UAMI binding above (uami-indexer-*).
          command: ['/bin/sh', '-c']
          args: [
            'python -m src.seed.seed_index --source local --search-endpoint "${searchEndpoint}" --acknowledge-identity-check-skipped && python -m src.seed.seed_topics --cosmos-endpoint "${cosmosEndpoint}" --search-endpoint "${searchEndpoint}"'
          ]
        }
      ]
    }
  }
}

output jobId string = job.id
output jobName string = jobName

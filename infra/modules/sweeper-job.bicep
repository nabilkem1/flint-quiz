// Background sweeper Container Apps Job (TASK-191 — CAJ variant).
//
// Replaces the Functions-on-VM design in `modules/foundry/sweeper.bicep`:
// same UAMI (`uami-agent-*`), same scope guard, same App Insights metrics,
// same sweep logic (one source of truth in `src/sweeper/_core.py`). The
// only thing that changes is the host — no `Microsoft.Web/serverFarms`
// quota required, billing reduces to the per-vCPU-second consumption rate
// for the sub-second tick.
//
// Trigger: 5-field cron (CAJ format). Minimum cadence is 1 minute, which
// matches the Functions 6-field ncron `0 */1 * * * *` the legacy host used.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

@description('Container Apps managed environment resource ID')
param environmentId string

@description('ACR login server (e.g., fqdevacr3mlbwqqlmxoji.azurecr.io)')
param registryLoginServer string

@description('Full image reference. Default is the public Microsoft hello-world bootstrap image so the FIRST `azd provision` succeeds before any sweeper image is pushed to ACR. After `azd deploy sweeper` runs once, consumers should pass the currently-deployed image via an `existing` lookup (same pattern as quiz-agent-app.bicep).')
param imageRef string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Agent UAMI resource ID (Cosmos Data Contributor scoped to the `sessions` container — see modules/rbac.bicep)')
param uamiAgentResourceId string

@description('Agent UAMI client ID — surfaced as AZURE_CLIENT_ID')
param uamiAgentClientId string

@description('Cosmos account endpoint (https://<account>.documents.azure.com:443/)')
param cosmosEndpoint string

@description('Cosmos database name')
param cosmosDatabaseName string

@description('Cosmos sessions container name (also used as SWEEPER_ALLOWED_CONTAINER scope guard)')
param cosmosSessionsContainerName string

@description('App Insights connection string (non-secret per Microsoft guidance)')
param appInsightsConnectionString string

@description('Sweeper cron expression — 5-field standard cron (CAJ format). Default fires every minute, matching the Functions-host cadence.')
param cronExpression string = '*/1 * * * *'

var jobName = '${prefix}-${environmentName}-sweeper'

// `azd-service-name: sweeper` lets `azd deploy sweeper` find this Container
// Apps Job as the deploy target.
resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: jobName
  location: location
  tags: union(tags, {
    'azd-service-name': 'sweeper'
  })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uamiAgentResourceId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Schedule'
      // 120s replica timeout is generous for a sub-second tick + cold start.
      // No retries on the cron path — if a tick fails, the next firing
      // re-evaluates fresh; retrying a stale `_ts` cutoff buys nothing.
      replicaTimeout: 120
      replicaRetryLimit: 0
      scheduleTriggerConfig: {
        cronExpression: cronExpression
        parallelism: 1
        replicaCompletionCount: 1
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
          name: 'sweeper'
          image: imageRef
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'AZURE_CLIENT_ID', value: uamiAgentClientId }
            { name: 'AZURE_ENV_NAME', value: environmentName }
            { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'COSMOS_DATABASE', value: cosmosDatabaseName }
            { name: 'COSMOS_SESSIONS_CONTAINER', value: cosmosSessionsContainerName }
            // Scope guard: `_core.SweeperConfig` raises at boot if this is
            // anything other than `sessions` (SEC-004 / TASK-191).
            { name: 'SWEEPER_ALLOWED_CONTAINER', value: cosmosSessionsContainerName }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
            { name: 'LOG_LEVEL', value: 'INFO' }
          ]
        }
      ]
    }
  }
}

output jobId string = job.id
output jobName string = jobName

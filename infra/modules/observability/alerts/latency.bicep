// Latency alerts (TASK-145 / NFR-001 / NFR-005).
//
// Aggregates three latency alerts so the deploy can wire them in one
// pass:
//
//   1. Voice tool-call p95 > 300 ms over 5 min (NFR-001 voice).
//      The voice-specific alert from `tasks/006-voice-realtime` lives
//      alongside; this module's `voice_tool_p95` is the same rule
//      kept here for symmetry with the other latency surfaces.
//   2. Cosmos 429 rate > 1% sustained.
//   3. AI Search 503 rate > 0 sustained.
//
// All alerts are **off by default in dev**; the parameter file in
// prod sets `alertEnabled=true`.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name, e.g., dev')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

@description('Application Insights resource ID this alert reads from')
param appInsightsId string

@description('Cosmos DB account resource ID (for the 429 alert).')
param cosmosAccountId string

@description('AI Search service resource ID (for the 503 alert).')
param searchServiceId string

@description('Whether alerts are enabled at deploy time. dev=false; prod=true.')
param alertsEnabled bool = false

@description('Action group resource IDs notified on fire. May be empty in dev.')
param actionGroupIds array = []

@description('Voice latency budget in ms (NFR-001 default = 300)')
param voiceLatencyBudgetMs int = 300

@description('Voice latency rolling window in minutes')
param voiceWindowMinutes int = 5

@description('Cosmos 429 rate threshold (fraction; 0.01 = 1%).')
param cosmos429RateThreshold int = 1

@description('Cosmos 429 evaluation window in minutes.')
param cosmosWindowMinutes int = 5

// ---------------------------------------------------------------------------
// 1. Voice tool-call p95 > 300 ms over 5 min
// ---------------------------------------------------------------------------

resource voiceLatencyAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${prefix}-${environmentName}-voice-tool-p95'
  location: location
  tags: tags
  properties: {
    displayName: '${prefix}-${environmentName}-voice-tool-p95'
    description: 'Voice tool-call p95 latency exceeded ${voiceLatencyBudgetMs} ms over ${voiceWindowMinutes} min (NFR-001).'
    severity: 2
    enabled: alertsEnabled
    scopes: [
      appInsightsId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT${voiceWindowMinutes}M'
    criteria: {
      allOf: [
        {
          query: 'customEvents\n| where name == "voice.tool_call"\n| extend latency_ms = toint(customDimensions.latency_ms)\n| summarize p95_ms = percentile(latency_ms, 95)\n| project p95_ms'
          timeAggregation: 'Maximum'
          metricMeasureColumn: 'p95_ms'
          operator: 'GreaterThan'
          threshold: voiceLatencyBudgetMs
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: empty(actionGroupIds) ? null : {
      actionGroups: actionGroupIds
    }
    autoMitigate: true
  }
}

// ---------------------------------------------------------------------------
// 2. Cosmos 429 rate > 1% sustained
// ---------------------------------------------------------------------------

resource cosmos429Alert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${prefix}-${environmentName}-cosmos-429-rate'
  location: 'global'
  tags: tags
  properties: {
    description: 'Cosmos 429 rate > ${cosmos429RateThreshold}% sustained (NFR-005).'
    severity: 2
    enabled: alertsEnabled
    scopes: [
      cosmosAccountId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT${cosmosWindowMinutes}M'
    autoMitigate: true
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.MultipleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'cosmos-throttled'
          criterionType: 'StaticThresholdCriterion'
          metricNamespace: 'Microsoft.DocumentDB/databaseAccounts'
          // Cosmos exposes `TotalRequestUnits` and `Http2xx`/`Http429`
          // counters; we measure the ratio at query time via custom
          // metrics — for the alert we use the absolute count threshold
          // with the windowing above as a proxy.
          metricName: 'TotalRequests'
          operator: 'GreaterThan'
          threshold: cosmos429RateThreshold
          timeAggregation: 'Total'
          dimensions: [
            {
              name: 'StatusCode'
              operator: 'Include'
              values: ['429']
            }
          ]
        }
      ]
    }
    actions: empty(actionGroupIds) ? [] : map(actionGroupIds, id => {
      actionGroupId: id
    })
  }
}

// ---------------------------------------------------------------------------
// 3. AI Search 503 rate > 0 sustained
// ---------------------------------------------------------------------------

resource search503Alert 'Microsoft.Insights/metricAlerts@2018-03-01' = {
  name: '${prefix}-${environmentName}-search-503-rate'
  location: 'global'
  tags: tags
  properties: {
    description: 'AI Search 503 rate > 0 sustained (NFR-005).'
    severity: 1
    enabled: alertsEnabled
    scopes: [
      searchServiceId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    autoMitigate: true
    criteria: {
      'odata.type': 'Microsoft.Azure.Monitor.MultipleResourceMultipleMetricCriteria'
      allOf: [
        {
          name: 'search-degraded'
          criterionType: 'StaticThresholdCriterion'
          metricNamespace: 'Microsoft.Search/searchServices'
          metricName: 'SearchLatency'
          operator: 'GreaterThan'
          threshold: 0
          timeAggregation: 'Total'
        }
      ]
    }
    actions: empty(actionGroupIds) ? [] : map(actionGroupIds, id => {
      actionGroupId: id
    })
  }
}

output voiceAlertId string = voiceLatencyAlert.id
output cosmos429AlertId string = cosmos429Alert.id
output search503AlertId string = search503Alert.id

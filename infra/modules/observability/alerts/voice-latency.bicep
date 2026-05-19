// Voice tool-call latency alert (TASK-107 / NFR-001).
//
// Fires when the p95 of `voice.tool_call.latency_ms` exceeds 300 ms over
// a rolling 5-minute window — the NFR-001 voice-channel budget. Disabled
// by default in dev so a cold start doesn't page on-call; production
// parameter file sets `alertEnabled=true`.
//
// The query is parameterised on the alert resource itself so a future
// budget revision (e.g., 350 ms for a model upgrade window) is a single
// parameter override, not a query rewrite.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name, e.g., dev')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

@description('Application Insights resource ID the alert reads from')
param appInsightsId string

@description('Latency budget in milliseconds (NFR-001 default = 300)')
param latencyBudgetMs int = 300

@description('Rolling evaluation window in minutes')
param windowMinutes int = 5

@description('Alert severity — 2 = warning, 1 = error, 0 = critical')
@allowed([
  0
  1
  2
  3
  4
])
param severity int = 2

@description('Whether the alert is enabled at deploy time. dev=false, prod=true.')
param alertEnabled bool = false

@description('Action group resource IDs to notify on fire. May be empty in dev.')
param actionGroupIds array = []

var alertName = '${prefix}-${environmentName}-voice-latency-p95'
var alertDescription = 'Voice tool-call p95 latency exceeded ${latencyBudgetMs} ms over ${windowMinutes} min (NFR-001).'

// Scheduled-query alert (formerly "metric alert v2 for logs"). Uses the
// Kusto query against the `voice.tool_call` customEvent emitted by
// `src/voice/realtime_runtime.py`. The threshold check (`Operator: GreaterThan`)
// fires when the computed `p95_ms` exceeds `latencyBudgetMs`.
resource alert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: alertName
  location: location
  tags: tags
  properties: {
    displayName: alertName
    description: alertDescription
    severity: severity
    enabled: alertEnabled
    scopes: [
      appInsightsId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT${windowMinutes}M'
    criteria: {
      allOf: [
        {
          query: 'customEvents\n| where name == "voice.tool_call"\n| extend latency_ms = toint(customDimensions.latency_ms)\n| summarize p95_ms = percentile(latency_ms, 95)\n| project p95_ms'
          timeAggregation: 'Maximum'
          metricMeasureColumn: 'p95_ms'
          operator: 'GreaterThan'
          threshold: latencyBudgetMs
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

output alertId string = alert.id
output alertName string = alertName

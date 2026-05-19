// Per-language correctness rate alert (TASK-146 / NFR-010).
//
// Tracks each language's correctness rate against a 7-day rolling
// baseline. Fires when any language's rate deviates more than
// `deviationPercentPoints` from its baseline AND the sample size in
// the window exceeds `minSampleSize` (so a noisy 5-grader trough
// doesn't page on-call).
//
// On fire, the action group routes to:
//
//   * Content team — a per-language Foundry Evaluation
//     (009-testing TASK-167) is triggered for the affected language.
//   * App Insights query link in the alert description so the analyst
//     can jump straight to the offending question distribution.

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

@description('Deviation threshold in percentage points (e.g., 5 → fire on 5% below baseline)')
param deviationPercentPoints int = 5

@description('Minimum sample size before the alert fires.')
param minSampleSize int = 100

@description('Whether the alert is enabled at deploy time. dev=false; prod=true.')
param alertEnabled bool = false

@description('Action group resource IDs to notify on fire.')
param actionGroupIds array = []

var alertName = '${prefix}-${environmentName}-per-language-correctness-drift'

resource alert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: alertName
  location: location
  tags: tags
  properties: {
    displayName: alertName
    description: 'Per-language correctness rate deviated more than ${deviationPercentPoints} percentage points from the 7-day rolling baseline (NFR-010). Triggers per-language Foundry Evaluation (TASK-167).'
    severity: 1
    enabled: alertEnabled
    scopes: [
      appInsightsId
    ]
    evaluationFrequency: 'PT1H'
    windowSize: 'PT1H'
    criteria: {
      allOf: [
        {
          // Compute the current-hour correctness per language and the
          // 7-day rolling baseline; emit the largest deviation as a
          // single numeric value the threshold compares against.
          //
          // The query returns the **absolute deviation in percentage
          // points** so a 95% baseline → 88% current produces `7`, and
          // the threshold (`deviationPercentPoints=5`) fires.
          query: 'let baseline_window = 7d;\nlet current_window = 1h;\nlet baseline = customEvents\n  | where name == "grading_event" and timestamp >= ago(baseline_window) and timestamp < ago(current_window)\n  | extend language = tostring(customDimensions.language), verdict = tostring(customDimensions.verdict)\n  | summarize correct = countif(verdict == "correct"), total = count() by language\n  | project language, baseline_pct = todouble(correct) / total * 100.0;\nlet current = customEvents\n  | where name == "grading_event" and timestamp >= ago(current_window)\n  | extend language = tostring(customDimensions.language), verdict = tostring(customDimensions.verdict)\n  | summarize correct = countif(verdict == "correct"), total = count() by language\n  | where total >= ${minSampleSize}\n  | project language, current_pct = todouble(correct) / total * 100.0, sample = total;\nbaseline\n| join kind=inner current on language\n| project language, baseline_pct, current_pct, sample, deviation = abs(baseline_pct - current_pct)\n| summarize max_deviation = max(deviation)'
          timeAggregation: 'Maximum'
          metricMeasureColumn: 'max_deviation'
          operator: 'GreaterThan'
          threshold: deviationPercentPoints
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

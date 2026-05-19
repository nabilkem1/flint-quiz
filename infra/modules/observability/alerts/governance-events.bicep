// Governance-event alerts (TASK-149 / `infra/README §10.2`).
//
// One bicep module ⇒ four alerts that wrap the GOV-* contracts:
//
//   * `agent.prompt_hash_mismatch` ≥ 1 → **P0** page (GOV-003).
//   * `agent.injection_detected` rate > 10× rolling baseline → P2 page
//     (GOV-061 — DoS-shaped attempt).
//   * `agent.coverage_gap` rate > 1% of `start_quiz` over 24 h → P1
//     ticket (GOV-025 — content-team triage).
//   * `agent.unknown_tool` ≥ 1 → P1 ticket (GOV-010 — model drift).
//
// All alerts are off by default in dev; the parameter file in prod
// sets `alertsEnabled=true`.

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

@description('Whether alerts are enabled at deploy time. dev=false; prod=true.')
param alertsEnabled bool = false

@description('Action group routed for P0 events (prompt-hash mismatch).')
param p0ActionGroupIds array = []

@description('Action group routed for P1/P2 events.')
param standardActionGroupIds array = []

@description('Coverage-gap rate threshold over a 24h window, as fraction of start_quiz events (default 0.01 = 1%).')
param coverageGapRateFraction string = '0.01'

@description('Injection-detected multiplier vs 7-day rolling baseline before alerting.')
param injectionRateMultiplier int = 10

// ---------------------------------------------------------------------------
// P0 — agent.prompt_hash_mismatch ≥ 1
// ---------------------------------------------------------------------------

resource promptHashAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${prefix}-${environmentName}-agent-prompt-hash-mismatch'
  location: location
  tags: tags
  properties: {
    displayName: '${prefix}-${environmentName}-agent-prompt-hash-mismatch'
    description: '**P0** — `agent.prompt_hash_mismatch` fired (GOV-003). The session\'s composed prompt drifted from `session.prompt_hash`. Halt + page.'
    severity: 0
    enabled: alertsEnabled
    scopes: [
      appInsightsId
    ]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    criteria: {
      allOf: [
        {
          query: 'customEvents\n| where name == "agent.prompt_hash_mismatch"\n| summarize cnt = count()'
          timeAggregation: 'Total'
          metricMeasureColumn: 'cnt'
          operator: 'GreaterThanOrEqual'
          threshold: 1
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: empty(p0ActionGroupIds) ? null : {
      actionGroups: p0ActionGroupIds
    }
    autoMitigate: false
  }
}

// ---------------------------------------------------------------------------
// P2 — agent.injection_detected rate > N× rolling baseline
// ---------------------------------------------------------------------------

resource injectionRateAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${prefix}-${environmentName}-agent-injection-rate'
  location: location
  tags: tags
  properties: {
    displayName: '${prefix}-${environmentName}-agent-injection-rate'
    description: '`agent.injection_detected` rate exceeded ${injectionRateMultiplier}× the 7-day rolling baseline (GOV-061). DoS-shaped attempt likely.'
    severity: 2
    enabled: alertsEnabled
    scopes: [
      appInsightsId
    ]
    evaluationFrequency: 'PT10M'
    windowSize: 'PT1H'
    criteria: {
      allOf: [
        {
          query: 'let baseline = customEvents\n  | where name == "agent.injection_detected" and timestamp between (ago(8d) .. ago(1d))\n  | summarize total = count()\n  | extend per_hour_baseline = total / 168.0\n  | project per_hour_baseline;\nlet current = customEvents\n  | where name == "agent.injection_detected" and timestamp >= ago(1h)\n  | summarize cnt = count();\ncurrent\n| extend baseline_per_hour = toscalar(baseline)\n| extend multiplier = iff(baseline_per_hour > 0, cnt / baseline_per_hour, cnt * 1.0)\n| project multiplier'
          timeAggregation: 'Maximum'
          metricMeasureColumn: 'multiplier'
          operator: 'GreaterThan'
          threshold: injectionRateMultiplier
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: empty(standardActionGroupIds) ? null : {
      actionGroups: standardActionGroupIds
    }
    autoMitigate: true
  }
}

// ---------------------------------------------------------------------------
// P1 — agent.coverage_gap rate > 1% of start_quiz / 24h
// ---------------------------------------------------------------------------

resource coverageGapAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${prefix}-${environmentName}-agent-coverage-gap-rate'
  location: location
  tags: tags
  properties: {
    displayName: '${prefix}-${environmentName}-agent-coverage-gap-rate'
    description: '`agent.coverage_gap` rate exceeded ${coverageGapRateFraction} of `start_quiz` events over 24 h (GOV-025). Content-team triage.'
    severity: 1
    enabled: alertsEnabled
    scopes: [
      appInsightsId
    ]
    evaluationFrequency: 'PT1H'
    windowSize: 'PT24H'
    criteria: {
      allOf: [
        {
          query: 'let starts = toscalar(\n  customEvents\n  | where name == "agent.dispatch.start_quiz" and timestamp >= ago(24h)\n  | summarize cnt = count()\n);\nlet gaps = toscalar(\n  customEvents\n  | where name == "agent.coverage_gap" and timestamp >= ago(24h)\n  | summarize cnt = count()\n);\nprint rate = iff(starts > 0, todouble(gaps) / todouble(starts), 0.0)'
          timeAggregation: 'Maximum'
          metricMeasureColumn: 'rate'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: empty(standardActionGroupIds) ? null : {
      actionGroups: standardActionGroupIds
    }
    autoMitigate: true
  }
}

// ---------------------------------------------------------------------------
// P1 — agent.unknown_tool ≥ 1
// ---------------------------------------------------------------------------

resource unknownToolAlert 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: '${prefix}-${environmentName}-agent-unknown-tool'
  location: location
  tags: tags
  properties: {
    displayName: '${prefix}-${environmentName}-agent-unknown-tool'
    description: '`agent.unknown_tool` fired (GOV-010). Model attempted to call an unregistered tool — investigate for model drift / injection.'
    severity: 1
    enabled: alertsEnabled
    scopes: [
      appInsightsId
    ]
    evaluationFrequency: 'PT15M'
    windowSize: 'PT15M'
    criteria: {
      allOf: [
        {
          query: 'customEvents\n| where name == "agent.unknown_tool"\n| summarize cnt = count()'
          timeAggregation: 'Total'
          metricMeasureColumn: 'cnt'
          operator: 'GreaterThanOrEqual'
          threshold: 1
          failingPeriods: {
            numberOfEvaluationPeriods: 1
            minFailingPeriodsToAlert: 1
          }
        }
      ]
    }
    actions: empty(standardActionGroupIds) ? null : {
      actionGroups: standardActionGroupIds
    }
    autoMitigate: true
  }
}

output promptHashAlertId string = promptHashAlert.id
output injectionRateAlertId string = injectionRateAlert.id
output coverageGapAlertId string = coverageGapAlert.id
output unknownToolAlertId string = unknownToolAlert.id

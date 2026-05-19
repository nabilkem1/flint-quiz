// Per-language correctness saved query (TASK-146 / NFR-010).
//
// Persisted Kusto query the per-language correctness rate monitor
// (`infra/modules/observability/alerts/per-language-correctness.bicep`)
// references. Saved queries live in the LAW workspace; the alert
// independently embeds its own KQL, but the saved query is what an
// operator runs interactively from the Quiz Correctness workbook.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name, e.g., dev')
param environmentName string

@description('Mandatory tags')
param tags object

@description('Log Analytics workspace resource ID (saved queries live here).')
param logAnalyticsWorkspaceId string

// Extract the workspace name from the resource ID — saved queries are
// parented by the workspace name, not its ID.
var logAnalyticsName = last(split(logAnalyticsWorkspaceId, '/'))

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsName
}

// 7-day rolling correctness rate per language. Plumbed to the
// "Quiz Correctness" workbook and to the per-language drift alert.
resource correctnessSaved 'Microsoft.OperationalInsights/workspaces/savedSearches@2020-08-01' = {
  parent: workspace
  name: '${prefix}-${environmentName}-correctness-by-language-7d'
  properties: {
    category: 'Flint Quiz'
    displayName: 'Correctness rate per language (rolling 7d)'
    query: 'customEvents\n| where name == "grading_event" and timestamp >= ago(7d)\n| extend language = tostring(customDimensions.language), verdict = tostring(customDimensions.verdict)\n| summarize correct = countif(verdict == "correct"), total = count() by language\n| project language, correctness_pct = todouble(correct) / total * 100.0, sample = total\n| order by correctness_pct asc'
    version: 2
    tags: [
      {
        name: 'workload'
        value: 'flint'
      }
      {
        name: 'category'
        value: 'correctness'
      }
    ]
  }
}

output savedQueryId string = correctnessSaved.id
output savedQueryName string = correctnessSaved.name

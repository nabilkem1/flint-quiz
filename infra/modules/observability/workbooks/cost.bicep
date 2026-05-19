// Quiz Cost workbook (TASK-147 / §007-operational-runbook §5).
//
// Per-resource cost surfaces + the "Realtime audio minutes per session"
// KPI that anchors NFR-013. The cap from `tasks/006 TASK-105`
// (`voice:maxSessionMinutes`) is visible alongside so a regression on
// the cap is observable here, not only on the voice workbook.
//
// All queries are constants from `src/observability/cost.py` — keeping
// the Kusto strings in code lets tests grep them for stable shapes.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name, e.g., dev')
param environmentName string

@description('Azure region')
param location string

@description('Mandatory tags')
param tags object

@description('Application Insights resource ID this workbook reads from')
param appInsightsId string

@description('Voice session length cap in minutes (NFR-013). Surfaces as a reference line.')
param voiceMaxSessionMinutes int = 30

var workbookName = '${prefix}-${environmentName}-cost'
var workbookDisplayName = 'Quiz Cost'

var workbookContent = {
  version: 'Notebook/1.0'
  items: [
    {
      type: 1
      content: {
        json: '## Quiz Cost\n\nPer-resource cost dimensions that move: Realtime audio minutes, Foundry model tokens, Cosmos RU, AI Search SU. The NFR-013 anchor is **Realtime audio minutes per session** — the cap is **${voiceMaxSessionMinutes}** minutes (`voice:maxSessionMinutes`).'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customEvents\n| where name == "voice.session_closed"\n| extend session_id = tostring(customDimensions.session_id), elapsed_seconds = toint(customDimensions.elapsed_seconds)\n| summarize minutes_per_session = avg(elapsed_seconds) / 60.0 by bin(timestamp, 1h)\n| order by timestamp desc'
        size: 0
        title: 'Realtime audio minutes per session (1h bins)'
        timeContext: { durationMs: 86400000 }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'linechart'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customMetrics\n| where name == "model.tokens"\n| extend model = tostring(customDimensions.model), direction = tostring(customDimensions.direction)\n| summarize tokens = sum(value) by bin(timestamp, 1h), model, direction\n| order by timestamp desc'
        size: 0
        title: 'Foundry model tokens by direction (input vs output)'
        timeContext: { durationMs: 86400000 }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'linechart'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'AzureMetrics\n| where ResourceProvider == "MICROSOFT.DOCUMENTDB"\n| where MetricName == "TotalRequestUnits"\n| extend container = tostring(parse_url(ResourceId).Path)\n| summarize ru = sum(Total) by bin(TimeGenerated, 1h), container\n| order by TimeGenerated desc'
        size: 0
        title: 'Cosmos RU/s by container'
        timeContext: { durationMs: 86400000 }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'linechart'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'AzureMetrics\n| where ResourceProvider == "MICROSOFT.SEARCH"\n| where MetricName == "SearchUnits"\n| summarize search_units = avg(Total) by bin(TimeGenerated, 1h)\n| order by TimeGenerated desc'
        size: 0
        title: 'AI Search SU (search units) over time'
        timeContext: { durationMs: 86400000 }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'linechart'
      }
    }
    {
      type: 1
      content: {
        json: '> **Cap reference**: per-session Realtime cap = `${voiceMaxSessionMinutes}` minutes (`voice:maxSessionMinutes`, `infra/modules/realtime.bicep`). A session approaching the cap is normal; sustained values at the cap point to a runaway-session investigation (see `tasks/006 TASK-105`).'
      }
    }
  ]
  fallbackResourceIds: [
    appInsightsId
  ]
}

resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  #disable-next-line use-stable-resource-identifiers
  name: guid(resourceGroup().id, workbookName)
  location: location
  tags: tags
  kind: 'shared'
  properties: {
    displayName: workbookDisplayName
    category: 'workbook'
    sourceId: appInsightsId
    serializedData: string(workbookContent)
    version: '1.0'
  }
}

output workbookId string = workbook.id
output workbookDisplayName string = workbookDisplayName

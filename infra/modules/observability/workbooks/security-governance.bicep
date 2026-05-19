// Security & Governance workbook (TASK-149 / `infra/README §10.4`).
//
// Operational expression of every GOV-* contract that emits an event.
// Tiles surface 24-hour rates per event name + a few targeted drill-
// downs the runbook references:
//
//   * 24h rate per `agent.*` event name (single bar chart).
//   * Top 10 topics with `agent.coverage_gap` (content-team triage).
//   * `agent.injection_detected` by `payload_encoding` (encoded-attack trend).
//   * `agent.prompt_hash_mismatch` highlight — single occurrence = P0.
//   * `sweeper.*` counts per hour (operational health).
//
// All queries read `customEvents` (App Insights). The events
// themselves are emitted via `src/observability/events.py` — that
// module enforces the dimension policy, so the queries here can
// assume the shape.

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

var workbookName = '${prefix}-${environmentName}-security-governance'
var workbookDisplayName = 'Security & Governance'

var workbookContent = {
  version: 'Notebook/1.0'
  items: [
    {
      type: 1
      content: {
        json: '## Security & Governance\n\nOperational view of the GOV-* contracts. **A single `agent.prompt_hash_mismatch` is a P0** — page on-call immediately if the highlight tile shows any value.'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customEvents\n| where name startswith "agent." or name startswith "audit." or name startswith "sweeper."\n| summarize events_24h = count() by name\n| order by events_24h desc'
        size: 0
        title: '24h rate per governance event'
        timeContext: { durationMs: 86400000 }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'barchart'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customEvents\n| where name == "agent.coverage_gap"\n| extend topic = tostring(customDimensions.topic), requested_language = tostring(customDimensions.requested_language)\n| summarize coverage_gaps_24h = count() by topic, requested_language\n| order by coverage_gaps_24h desc\n| take 10'
        size: 0
        title: 'Top 10 topics with `agent.coverage_gap` (content-team triage)'
        timeContext: { durationMs: 86400000 }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'table'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customEvents\n| where name == "agent.injection_detected"\n| extend payload_encoding = tostring(customDimensions.payload_encoding), language = tostring(customDimensions.language)\n| summarize attempts = count() by bin(timestamp, 1h), payload_encoding\n| order by timestamp desc'
        size: 0
        title: 'Injection attempts by payload encoding (plain / base64 / rot13 / leet)'
        timeContext: { durationMs: 604800000 }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'linechart'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customEvents\n| where name == "agent.prompt_hash_mismatch"\n| extend session_id = tostring(customDimensions.session_id), expected = tostring(customDimensions.expected_hash), actual = tostring(customDimensions.actual_hash)\n| project timestamp, session_id, expected_hash_prefix = substring(expected, 0, 12), actual_hash_prefix = substring(actual, 0, 12)\n| order by timestamp desc\n| take 50'
        size: 0
        title: '⚠️ `agent.prompt_hash_mismatch` highlight (P0 — page on-call if any rows)'
        timeContext: { durationMs: 604800000 }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'table'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customEvents\n| where name startswith "sweeper."\n| extend count_value = toint(customDimensions.count)\n| summarize total = sum(count_value) by bin(timestamp, 1h), name\n| order by timestamp desc'
        size: 0
        title: 'Sweeper counts per hour (operational health)'
        timeContext: { durationMs: 604800000 }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'linechart'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customEvents\n| where name == "agent.unknown_tool"\n| extend requested_tool_name = tostring(customDimensions.requested_tool_name), session_id = tostring(customDimensions.session_id)\n| summarize rejections_24h = count() by requested_tool_name\n| order by rejections_24h desc'
        size: 0
        title: '`agent.unknown_tool` rejections (model-drift signal)'
        timeContext: { durationMs: 86400000 }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'table'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customEvents\n| where name in ("audit.user_erased", "audit.user_erased.repeat", "audit.erasure_archive_locked", "audit.erasure_denied")\n| project timestamp, name, pseudo_userid = tostring(customDimensions.pseudo_userid), ticket_ref = tostring(customDimensions.ticket_ref)\n| order by timestamp desc\n| take 100'
        size: 0
        title: 'GDPR erasure audit-of-audit (last 100 events)'
        timeContext: { durationMs: 2592000000 }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'table'
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

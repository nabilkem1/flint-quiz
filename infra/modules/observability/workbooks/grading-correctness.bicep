// Quiz Correctness workbook (TASK-143 / NFR-009 / NFR-010).
//
// The headline metric for an exam system is **correctness per
// language**, not uptime. This workbook makes that visible.
//
// Tiles:
//   * Overall correctness % (24h).
//   * Per-language correctness % (24h, with a baseline annotation).
//   * Per-topic correctness % (24h).
//   * Per-question verdict heatmap — find chronically wrong questions
//     for author review.
//
// All queries read `customEvents` for `grading_event` (008-api §4.5.1).
// `expected` and `receivedRaw` are intentionally absent from that
// event; this workbook surfaces verdict counts only.

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

var workbookName = '${prefix}-${environmentName}-grading-correctness'
var workbookDisplayName = 'Quiz Correctness'

// `grading_event` schema (from `src/observability/events.py`):
//   { session_id, question_id, user_id, language, received, verdict,
//     channel, score_delta, latency_ms, timestamp }
//
// Notice: no `expected` field — by design.
var workbookContent = {
  version: 'Notebook/1.0'
  items: [
    {
      type: 1
      content: {
        json: '## Quiz Correctness\n\nPer-language correctness is the headline metric for an exam system. Use the heatmap to find chronically wrong questions; flag those for author review (per-question evaluation lives in `tasks/009-testing`).'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customEvents\n| where name == "grading_event"\n| extend verdict = tostring(customDimensions.verdict)\n| summarize total = count(), correct = countif(verdict == "correct") by bin(timestamp, 1h)\n| project timestamp, correctness_pct = todouble(correct) / total * 100.0\n| order by timestamp desc'
        size: 0
        title: 'Overall correctness % (1h bins)'
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
        query: 'customEvents\n| where name == "grading_event"\n| extend verdict = tostring(customDimensions.verdict), language = tostring(customDimensions.language)\n| summarize total = count(), correct = countif(verdict == "correct") by bin(timestamp, 1h), language\n| project timestamp, language, correctness_pct = todouble(correct) / total * 100.0\n| order by timestamp desc'
        size: 0
        title: 'Per-language correctness % (1h bins)'
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
        query: 'customEvents\n| where name == "grading_event"\n| extend topic = tostring(customDimensions.topic), verdict = tostring(customDimensions.verdict)\n| where isnotempty(topic)\n| summarize total = count(), correct = countif(verdict == "correct") by topic\n| project topic, correctness_pct = todouble(correct) / total * 100.0, sample = total\n| order by correctness_pct asc'
        size: 0
        title: 'Per-topic correctness % (24h)'
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
        query: 'customEvents\n| where name == "grading_event"\n| extend question_id = tostring(customDimensions.question_id), verdict = tostring(customDimensions.verdict)\n| summarize correct = countif(verdict == "correct"), incorrect = countif(verdict == "incorrect"), partial = countif(verdict == "partial"), unanswered = countif(verdict == "unanswered") by question_id\n| extend total = correct + incorrect + partial + unanswered\n| where total >= 10\n| extend correctness_pct = todouble(correct) / total * 100.0\n| where correctness_pct < 50.0\n| order by correctness_pct asc, total desc\n| take 50'
        size: 0
        title: 'Chronically wrong questions (≥10 attempts, correctness < 50%)'
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
        query: 'customEvents\n| where name == "grading_event"\n| extend channel = tostring(customDimensions.channel), verdict = tostring(customDimensions.verdict)\n| summarize correct = countif(verdict == "correct"), total = count() by bin(timestamp, 1h), channel\n| project timestamp, channel, correctness_pct = todouble(correct) / total * 100.0\n| order by timestamp desc'
        size: 0
        title: 'Correctness by channel (text vs voice)'
        timeContext: { durationMs: 604800000 }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'linechart'
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

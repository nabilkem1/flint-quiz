// Voice hot-path workbook (TASK-109).
//
// Surfaces the four signals that matter on the voice channel:
//   * STT first-final latency (p50 / p95 / p99)
//   * TTS first-byte latency (p50 / p95 / p99)
//   * Voice tool-call round-trip (the `voice.tool_call` event from
//     `src/voice/realtime_runtime.py`) grouped by tool + language
//   * Per-language voice turn counts
//
// The workbook reads from the App Insights instance provisioned in
// `infra/modules/observability.bicep`. It does NOT surface transcripts
// or any PII — only latency dimensions + counts. Transcripts live in
// App Insights `customDimensions` under the retention configured by
// 007-security TASK-132 and are never visualised in this workbook
// (FORBIDDEN ACTIONS).
//
// Workbook content is a serialised template; the structure mirrors the
// Azure Portal's gallery JSON so a portal pin produces a no-op diff.

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

var workbookName = '${prefix}-${environmentName}-voice-hot-path'
var workbookDisplayName = 'Quiz Voice — Hot Path'

// Workbook source JSON. The single-quote inner literals are the
// recommended pattern for inline workbook templates — Bicep escapes
// them automatically. Each item is a Kusto query keyed by chart kind.
//
// `voice.tool_call` schema (emitted by `src/voice/realtime_runtime.py`):
//   { session_id, tool, language, channel, latency_ms, ok }
//
// `voice.stt_drop` schema (emitted by `src/voice/stt_pipeline.py`):
//   { session_id, reason, confidence, is_final }
//
// `agent.tts_strip` schema (emitted by `src/voice/tts_pipeline.py`):
//   { language, session_id, stripped_chars }
var workbookContent = {
  version: 'Notebook/1.0'
  items: [
    {
      type: 1
      content: {
        json: '## Quiz Voice — Hot Path\n\nLatency and health metrics for the Realtime channel. Voice tool-call p95 alert threshold is **300 ms** (NFR-001).'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customEvents\n| where name == "voice.tool_call"\n| extend latency_ms = toint(customDimensions.latency_ms), tool = tostring(customDimensions.tool), language = tostring(customDimensions.language)\n| summarize p50 = percentile(latency_ms, 50), p95 = percentile(latency_ms, 95), p99 = percentile(latency_ms, 99) by bin(timestamp, 5m), tool\n| order by timestamp desc'
        size: 0
        title: 'Voice tool-call latency (p50/p95/p99) by tool'
        timeContext: {
          durationMs: 3600000
        }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'linechart'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customEvents\n| where name == "voice.tool_call"\n| extend latency_ms = toint(customDimensions.latency_ms), language = tostring(customDimensions.language)\n| summarize turns = count() by bin(timestamp, 5m), language\n| order by timestamp desc'
        size: 0
        title: 'Voice turns per language'
        timeContext: {
          durationMs: 3600000
        }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'barchart'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customMetrics\n| where name == "voice.stt.first_final_ms"\n| summarize p50 = percentile(value, 50), p95 = percentile(value, 95), p99 = percentile(value, 99) by bin(timestamp, 5m)\n| order by timestamp desc'
        size: 0
        title: 'STT first-final latency (p50/p95/p99)'
        timeContext: {
          durationMs: 3600000
        }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'linechart'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customMetrics\n| where name == "voice.tts.first_byte_ms"\n| summarize p50 = percentile(value, 50), p95 = percentile(value, 95), p99 = percentile(value, 99) by bin(timestamp, 5m)\n| order by timestamp desc'
        size: 0
        title: 'TTS first-byte latency (p50/p95/p99)'
        timeContext: {
          durationMs: 3600000
        }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'linechart'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customEvents\n| where name == "agent.tts_strip"\n| extend language = tostring(customDimensions.language)\n| summarize fired = count() by bin(timestamp, 5m), language\n| order by timestamp desc'
        size: 0
        title: 'TTS defensive strip warnings (fix at source)'
        timeContext: {
          durationMs: 3600000
        }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'barchart'
      }
    }
    {
      type: 3
      content: {
        version: 'KqlItem/1.0'
        query: 'customEvents\n| where name == "voice.stt_drop"\n| extend reason = tostring(customDimensions.reason)\n| summarize drops = count() by bin(timestamp, 5m), reason\n| order by timestamp desc'
        size: 0
        title: 'STT drops by reason (low-confidence / empty-final)'
        timeContext: {
          durationMs: 3600000
        }
        queryType: 0
        resourceType: 'microsoft.insights/components'
        visualization: 'barchart'
      }
    }
  ]
  fallbackResourceIds: [
    appInsightsId
  ]
}

resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  // Workbook resource name is a GUID — uniqueString keeps the deploy idempotent.
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

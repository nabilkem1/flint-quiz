// Runbook-hook saved queries (TASK-148 / §007-operational-runbook §9).
//
// Every symptom in the operational runbook §9 has a saved query that
// surfaces evidence — first responders should be able to triage by
// clicking through from the runbook entry to the workspace.
//
// Saved queries live in the LAW workspace, not App Insights, so the
// same queries can be embedded in workbooks AND run interactively by
// on-call.

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name, e.g., dev')
param environmentName string

@description('Mandatory tags')
param tags object

@description('Log Analytics workspace resource ID.')
param logAnalyticsWorkspaceId string

var logAnalyticsName = last(split(logAnalyticsWorkspaceId, '/'))

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsName
}

// ---------------------------------------------------------------------------
// §9.1 — Double-scoring incident.
// "Did a session get >1 audit row for the same (sessionId, questionId)?"
// Audit rows live in Cosmos, but the canonical telemetry shadow is the
// `grading_event` stream — duplicate events are the symptom.
// ---------------------------------------------------------------------------

resource doubleScoring 'Microsoft.OperationalInsights/workspaces/savedSearches@2020-08-01' = {
  parent: workspace
  name: '${prefix}-${environmentName}-runbook-double-scoring'
  properties: {
    category: 'Flint Quiz · Runbook'
    displayName: 'Runbook §9 · Duplicate grading_event per (session, question)'
    query: 'customEvents\n| where name == "grading_event"\n| extend session_id = tostring(customDimensions.session_id), question_id = tostring(customDimensions.question_id)\n| summarize cnt = count() by session_id, question_id\n| where cnt > 1\n| order by cnt desc'
    version: 2
  }
}

// ---------------------------------------------------------------------------
// §9.2 — Voice latency spike.
// Pivots straight from the runbook to the voice workbook query.
// ---------------------------------------------------------------------------

resource voiceLatencySpike 'Microsoft.OperationalInsights/workspaces/savedSearches@2020-08-01' = {
  parent: workspace
  name: '${prefix}-${environmentName}-runbook-voice-latency-spike'
  properties: {
    category: 'Flint Quiz · Runbook'
    displayName: 'Runbook §9 · Voice tool-call p95 per tool (last 1h)'
    query: 'customEvents\n| where name == "voice.tool_call"\n| extend tool = tostring(customDimensions.tool), latency_ms = toint(customDimensions.latency_ms), language = tostring(customDimensions.language)\n| where timestamp >= ago(1h)\n| summarize p50 = percentile(latency_ms, 50), p95 = percentile(latency_ms, 95), p99 = percentile(latency_ms, 99) by tool, language\n| order by p95 desc'
    version: 2
  }
}

// ---------------------------------------------------------------------------
// §9.3 — Wrong-language served.
// Identifies sessions where `language != requested_language` without a
// user-driven `set_language` between the start and that turn.
// ---------------------------------------------------------------------------

resource wrongLanguage 'Microsoft.OperationalInsights/workspaces/savedSearches@2020-08-01' = {
  parent: workspace
  name: '${prefix}-${environmentName}-runbook-wrong-language'
  properties: {
    category: 'Flint Quiz · Runbook'
    displayName: 'Runbook §9 · Sessions served in non-requested language (no set_language override)'
    // Requires emission of `agent.session_started` carrying both
    // `language` + `requested_language`; the dispatcher emits it on
    // every `start_quiz` success path.
    query: 'customEvents\n| where name == "agent.dispatch.start_quiz" and tostring(customDimensions.outcome) == "ok"\n| extend session_id = tostring(customDimensions.session_id), language = tostring(customDimensions.language), requested_language = tostring(customDimensions.requested_language)\n| where isnotempty(requested_language) and language != requested_language\n| project timestamp, session_id, requested_language, language\n| order by timestamp desc'
    version: 2
  }
}

// ---------------------------------------------------------------------------
// §9.4 — Answer key in agent text (P0).
// SEC-001 leak. The defensive strip emits an `agent.tts_strip` (voice)
// or runtime defensive-strip warning when it had to act; this query
// surfaces the warnings.
// ---------------------------------------------------------------------------

resource answerKeyLeak 'Microsoft.OperationalInsights/workspaces/savedSearches@2020-08-01' = {
  parent: workspace
  name: '${prefix}-${environmentName}-runbook-answer-key-leak'
  properties: {
    category: 'Flint Quiz · Runbook'
    displayName: 'Runbook §9 · Defensive-strip warnings — answer-key leak signal (P0)'
    query: 'customEvents\n| where name in ("agent.tts_strip", "tool.defensive_strip.answer_key_present")\n| project timestamp, name, language = tostring(customDimensions.language), session_id = tostring(customDimensions.session_id)\n| order by timestamp desc'
    version: 2
  }
}

// ---------------------------------------------------------------------------
// §9.5 — Coverage gap surge by topic. Drives the content team's
// triage queue when a topic is missing in the active language.
// ---------------------------------------------------------------------------

resource coverageGapSurge 'Microsoft.OperationalInsights/workspaces/savedSearches@2020-08-01' = {
  parent: workspace
  name: '${prefix}-${environmentName}-runbook-coverage-gap-surge'
  properties: {
    category: 'Flint Quiz · Runbook'
    displayName: 'Runbook §9 · Top topics with coverage gaps (last 24h)'
    query: 'customEvents\n| where name == "agent.coverage_gap" and timestamp >= ago(24h)\n| extend topic = tostring(customDimensions.topic), requested_language = tostring(customDimensions.requested_language), consent_path = tostring(customDimensions.consent_path)\n| summarize gaps = count() by topic, requested_language, consent_path\n| order by gaps desc'
    version: 2
  }
}

output doubleScoringId string = doubleScoring.id
output voiceLatencyId string = voiceLatencySpike.id
output wrongLanguageId string = wrongLanguage.id
output answerKeyLeakId string = answerKeyLeak.id
output coverageGapSurgeId string = coverageGapSurge.id

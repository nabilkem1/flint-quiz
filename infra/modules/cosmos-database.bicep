// Cosmos DB database `flint-quiz` and the four containers owned by Phase 2.
//
// Container layout (matches specs/003-data-contracts §4 and specs/008-api §2):
//   sessions  pk=/userId      TTL=-1 default (per-doc TTL set on terminal state, TASK-050)
//   users     pk=/userId      no TTL
//   topics    pk=/topicId     no TTL
//   audit     pk=/sessionId   TTL=365d default (auditHotDays; ADR-006)
//
// Throughput is provisioned at the database level (autoscale 4000 RU/s) so all
// four containers share a pool — cheaper than per-container at this scale and
// re-evaluable when a hot container emerges (TASK-040 risk note).
//
// Identity discipline: `disableLocalAuth: true` is enforced on the account in
// modules/cosmos.bicep (SEC-004). No keys are emitted here either. All
// data-plane access flows through the agent UAMI via the SqlRoleAssignment
// declared at the bottom of this module — colocating the assignment with the
// container definitions is what the cross-pack note in `modules/rbac.bicep`
// defers to (the original design called for a custom role whose DataActions
// would reference these container IDs; for v1 we use the built-in Data
// Contributor at the account scope and tighten in a follow-up).

@description('Cosmos DB account name (output of modules/cosmos.bicep)')
param cosmosAccountName string

@description('Agent UAMI principal ID (`uami.outputs.agentPrincipalId`). When empty, the role assignment is skipped — useful for scratch deploys that do not need data-plane access yet.')
param uamiAgentPrincipalId string = ''

@description('Indexer UAMI principal ID (`uami.outputs.indexerPrincipalId`). Needs Cosmos write access on the `topics` container so the seed-loader job can chain `seed_topics` after `seed_index`. Same `if (!empty(...))` guard pattern as the agent.')
param uamiIndexerPrincipalId string = ''

@description('Database name. Stable across environments.')
param databaseName string = 'flint-quiz'

@description('Autoscale max RU/s at the database level. Containers inherit.')
@minValue(1000)
@maxValue(1000000)
param autoscaleMaxThroughput int = 4000

@description('TTL applied by default to the audit container (server-side hot retention).')
@minValue(86400)
param auditTtlSeconds int = 31536000 // 365 days; retention:auditHotDays (ADR-006)

// The sessions default TTL is -1 (off): per-doc TTL is set by the repository
// on terminal-state transition (TASK-050). Active sessions never get reclaimed
// prematurely. Override via the param so a short-TTL integration test can
// flip it without forking the module.
@description('Default container-level TTL for sessions. -1 disables; per-doc TTL set on terminal state (TASK-050).')
param sessionsDefaultTtlSeconds int = -1

// ---- Existing account -----------------------------------------------------

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-08-15' existing = {
  name: cosmosAccountName
}

// ---- Database -------------------------------------------------------------

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-08-15' = {
  parent: cosmos
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
    options: {
      autoscaleSettings: {
        maxThroughput: autoscaleMaxThroughput
      }
    }
  }
}

// ---- Containers -----------------------------------------------------------
//
// Indexing notes mirror 008-api §2:
//   sessions: include status, topic, language, startedAt; exclude shuffledIds
//             (large; never queried). answers[] excluded from index too
//             (point-read-only access pattern).
//   audit:    include language, verdict, channel for analytics queries;
//             exclude expected and received to save RU and to keep the
//             answer-key-shaped `expected` out of the index footprint.

resource sessionsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-08-15' = {
  parent: database
  name: 'sessions'
  properties: {
    resource: {
      id: 'sessions'
      partitionKey: {
        paths: [
          '/userId'
        ]
        kind: 'Hash'
      }
      defaultTtl: sessionsDefaultTtlSeconds
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/status/?' }
          { path: '/topic/?' }
          { path: '/language/?' }
          { path: '/startedAt/?' }
          { path: '/userId/?' }
        ]
        excludedPaths: [
          { path: '/shuffledIds/*' }
          { path: '/answers/*' }
          { path: '/seed/?' }
          { path: '/*' }
        ]
      }
    }
  }
}

resource usersContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-08-15' = {
  parent: database
  name: 'users'
  properties: {
    resource: {
      id: 'users'
      partitionKey: {
        paths: [
          '/userId'
        ]
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/*' }
        ]
        excludedPaths: [
          { path: '/_etag/?' }
        ]
      }
    }
  }
}

resource topicsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-08-15' = {
  parent: database
  name: 'topics'
  properties: {
    resource: {
      id: 'topics'
      partitionKey: {
        paths: [
          '/topicId'
        ]
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/*' }
        ]
        excludedPaths: [
          { path: '/_etag/?' }
        ]
      }
    }
  }
}

resource auditContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-08-15' = {
  parent: database
  name: 'audit'
  properties: {
    resource: {
      id: 'audit'
      partitionKey: {
        paths: [
          '/sessionId'
        ]
        kind: 'Hash'
      }
      defaultTtl: auditTtlSeconds
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/language/?' }
          { path: '/verdict/?' }
          { path: '/channel/?' }
          { path: '/sessionId/?' }
          { path: '/timestamp/?' }
        ]
        excludedPaths: [
          { path: '/expected/*' }
          { path: '/received/?' }
          { path: '/receivedRaw/?' }
          { path: '/*' }
        ]
      }
    }
  }
}

output databaseName string = database.name
output sessionsContainerName string = sessionsContainer.name
output usersContainerName string = usersContainer.name
output topicsContainerName string = topicsContainer.name
output auditContainerName string = auditContainer.name

// Container resource IDs so RBAC can scope to a single container if needed
// (the original design called for the sweeper UAMI to get Cosmos Data
// Contributor on `sessions` only — kept here for the eventual custom-role
// follow-up; v1 grants account-scope Data Contributor below).
output sessionsContainerId string = sessionsContainer.id
output usersContainerId string = usersContainer.id
output topicsContainerId string = topicsContainer.id
output auditContainerId string = auditContainer.id

// ---- Agent UAMI data-plane role assignment --------------------------------
//
// Built-in role `00000000-0000-0000-0000-000000000002` is "Cosmos DB
// Built-in Data Contributor". It covers `readMetadata` (which the SDK's
// account-routing prefetch needs) plus read/write on all containers. The
// quiz-agent runtime and the sweeper Container Apps Job both authenticate
// as `uami-agent-*`; this assignment is what makes their startup data
// reads stop returning 403 / substatus 5301.
//
// Account-scope is broader than the original design (custom role scoped to
// `sessions` / `users` / `audit` rw + `topics` r/o). Tighten in a follow-up
// once the custom role definition lives next to these container resources.

resource agentDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-08-15' = if (!empty(uamiAgentPrincipalId)) {
  parent: cosmos
  name: guid(cosmos.id, uamiAgentPrincipalId, 'cosmos-data-contributor')
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    principalId: uamiAgentPrincipalId
    scope: cosmos.id
  }
}

// Indexer UAMI: account-scope Data Contributor so the seed-loader CAJ can
// write to the `topics` container after `seed_index` upserts the question
// rows. Original design wanted this scoped to the `topics` container only
// (separate custom role) — same tighten-later note as agent above.
resource indexerDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-08-15' = if (!empty(uamiIndexerPrincipalId)) {
  parent: cosmos
  name: guid(cosmos.id, uamiIndexerPrincipalId, 'cosmos-data-contributor')
  properties: {
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    principalId: uamiIndexerPrincipalId
    scope: cosmos.id
  }
}

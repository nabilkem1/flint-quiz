// Immutable Blob container `audit-archive` — stage 2 of the audit retention
// policy from ADR-006. Cosmos `audit` holds rows hot for 365 days; this
// container holds the byte-equivalent archive for **7 years** under a
// time-based immutability policy (legal hold off, locked).
//
// Wiring: the storage account is provisioned in modules/storage.bicep; this
// module attaches a container + immutability policy underneath the existing
// blob service. The archive writer (src/data/audit_archive.py) uses the
// agent UAMI with Storage Blob Data Contributor scoped to this container —
// that role assignment lives next to the other agent assignments in
// modules/rbac.bicep when this module is wired in.

@description('Storage account name (output of modules/storage.bicep)')
param storageAccountName string

@description('Container name for archived audit rows. Stable across environments.')
param containerName string = 'audit-archive'

@description('Immutability retention in days. 7 years = 2557 days. ADR-006.')
@minValue(365)
param immutabilityPeriodDays int = 2557

@description('Whether to allow protected append writes. Audit archive is append-only; keep true.')
param allowProtectedAppendWrites bool = true

// ---- Existing parents -----------------------------------------------------

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: storage
  name: 'default'
}

// ---- audit-archive container ---------------------------------------------

resource auditArchiveContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
    metadata: {
      purpose: 'audit-archive'
      retentionDriver: 'ADR-006'
    }
  }
}

// Time-based immutability policy — applied in **unlocked** state so dev/qa
// can rotate without operator burden; locking is performed by the
// post-provision hook in prod (operational runbook).
//
// Once locked, the period can only be extended (never shortened or removed) —
// that is the property that gives the archive its evidentiary weight.
resource immutabilityPolicy 'Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies@2023-05-01' = {
  parent: auditArchiveContainer
  name: 'default'
  properties: {
    immutabilityPeriodSinceCreationInDays: immutabilityPeriodDays
    allowProtectedAppendWrites: allowProtectedAppendWrites
  }
}

output auditArchiveContainerName string = auditArchiveContainer.name
output auditArchiveContainerId string = auditArchiveContainer.id
output immutabilityPolicyState string = immutabilityPolicy.properties.state

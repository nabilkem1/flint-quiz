// Cost-budget alerts (TASK-211 / NFR-013 / `infra/README §10.4`).
//
// Three thresholds (50% / 80% / 100%) on the env's monthly budget. The
// budget itself is provisioned at the subscription scope and refers to
// the resource group named `${prefix}-${environmentName}-rg`. Alerts
// route to the supplied action groups; in dev the group is typically
// empty (cost telemetry without paging).
//
// The KPI "Realtime audio minutes per session" lives on the Quiz Cost
// workbook (`workbooks/cost.bicep`); the voice session cap
// (`voice:maxSessionMinutes`) is rendered alongside so a runaway
// session is observable against the cap, not just against the budget.

// NOTE: `Microsoft.Consumption/budgets` is a subscription-scoped
// resource; this module is invoked from `main.bicep` at the
// subscription deployment target. We model it as a module so the
// target-scope decoration stays here and `main.bicep` stays focused
// on the resource graph.

targetScope = 'subscription'

@description('Naming prefix, e.g., fq')
param prefix string

@description('Environment name, e.g., dev / qa / prod')
param environmentName string

@description('Resource group name the budget is scoped to')
param resourceGroupName string

@description('Monthly budget amount in USD')
param monthlyBudgetUsd int

@description('Budget start date in YYYY-MM-DD. ARM rejects past dates more than one month old.')
param budgetStartDate string

@description('Notification email addresses to alert on threshold breach. Empty in dev.')
param notificationEmails array = []

@description('Action group resource IDs (when paging is wired). Empty in dev.')
param actionGroupIds array = []

@description('Mandatory tags (carried for telemetry — not applied to budget object)')
param tags object = {}

var budgetName = '${prefix}-${environmentName}-monthly-budget'

resource budget 'Microsoft.Consumption/budgets@2024-08-01' = {
  name: budgetName
  properties: {
    timePeriod: {
      startDate: budgetStartDate
    }
    timeGrain: 'Monthly'
    amount: monthlyBudgetUsd
    category: 'Cost'
    filter: {
      dimensions: {
        name: 'ResourceGroupName'
        operator: 'In'
        values: [
          resourceGroupName
        ]
      }
    }
    // Three notifications: 50/80/100 percent of monthly actual.
    notifications: {
      '50-percent-monthly': {
        enabled: true
        operator: 'GreaterThan'
        threshold: 50
        thresholdType: 'Actual'
        contactEmails: notificationEmails
        contactGroups: actionGroupIds
        locale: 'en-us'
      }
      '80-percent-monthly': {
        enabled: true
        operator: 'GreaterThan'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: notificationEmails
        contactGroups: actionGroupIds
        locale: 'en-us'
      }
      '100-percent-monthly': {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Actual'
        contactEmails: notificationEmails
        contactGroups: actionGroupIds
        locale: 'en-us'
      }
      // Forecasted overrun — fires before actual breach. Catches
      // runaway-session classes (NFR-013) earlier than the 100% rule
      // would.
      '100-percent-forecast': {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: notificationEmails
        contactGroups: actionGroupIds
        locale: 'en-us'
      }
    }
  }
}

output budgetName string = budgetName
output budgetId string = budget.id

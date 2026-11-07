// ADF Forge application — Azure Container Apps deployment (IMPLEMENTATION_PLAN.md
// gaps B-1/B-4/B-6). Consumption-plan Container Apps + `minReplicas: 0` (scale to
// zero) was chosen as the lowest-cost option that still fits this stack (3
// independent services, low/bursty traffic) — see .github/workflows/deploy.yml
// for the pipeline that builds images and invokes this template per environment.
//
// Scope boundary unchanged (§20/§23.7): this deploys the ADF Forge *application*
// (frontend/backend/worker) only. It has nothing to do with, and never touches,
// the customer's Azure Data Factory factory or the Generated Artifacts repo —
// those stay entirely outside this product, reached only via the Git adapter.
//
// Out of scope for this template, by design (§1.6/§20 — never a literal secret
// in Bicep, and this app assumes the customer's own infra for anything
// stateful it doesn't own):
//   - PostgreSQL: bring your own (Azure Database for PostgreSQL Flexible
//     Server, or any reachable Postgres) and put its connection string in
//     Key Vault before deploying.
//   - The Key Vault itself: referenced as `existing`, never created here —
//     provisioning and populating it (DATABASE_URL, GIT_TOKEN_FERNET_KEY,
//     the container registry password, any GIT_TOKEN fallback) is a one-time
//     manual/out-of-band step per environment.
//
// This template has not been deployed against a real subscription as part of
// this change — validate with `az deployment group what-if` before the first
// real run in each environment.

@description('Deployment environment name, e.g. dev, qa, or prod. Used only for resource naming/tagging — never read by the application itself (environment-specific values all come from Key Vault / Container Apps secrets).')
param environment string

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Base name shared by this environment\'s resources.')
param platformName string = 'adf-forge-${environment}'

@description('Name of an existing Key Vault holding this environment\'s secrets. Provisioned and populated out of band.')
param keyVaultName string

@description('Container registry server images are pulled from.')
param containerRegistryServer string = 'ghcr.io'

@description('Container registry username for pulling private images.')
param containerRegistryUsername string

@description('Key Vault secret name holding the container registry password/PAT.')
param keyVaultRegistryPasswordSecretName string = 'container-registry-password'

@description('Key Vault secret name holding DATABASE_URL.')
param keyVaultDatabaseUrlSecretName string = 'database-url'

@description('Key Vault secret name holding GIT_TOKEN_FERNET_KEY (must be identical for api and worker).')
param keyVaultFernetKeySecretName string = 'git-token-fernet-key'

@description('Key Vault secret name holding KEY_VAULT_NAME\'s own fallback GIT_TOKEN, if used.')
param keyVaultGitTokenSecretName string = 'git-token'

@description('Fully-qualified backend image repository, e.g. ghcr.io/org/adf-accelerator-backend (no tag).')
param backendImage string

@description('Fully-qualified worker image repository (no tag).')
param workerImage string

@description('Fully-qualified frontend image repository (no tag).')
param frontendImage string

@description('Image tag to deploy — the CD workflow passes the Git SHA it just built and pushed.')
param imageTag string

@description('Scale-to-zero by default (lowest cost); raise for an environment that must stay warm.')
param minReplicas int = 0

@description('Upper bound so a runaway traffic spike cannot scale cost unboundedly.')
param maxReplicas int = 2

@description('Sign-in mode (AUTH_MODE in config/platform.env). password = admin-created username/password accounts; mock = no sign-in, never for a real environment; oidc/saml = scaffolded only, every request 501s.')
@allowed(['password', 'mock', 'oidc', 'saml'])
param authMode string = 'password'

var logAnalyticsName = take('${platformName}-logs', 63)
var containerAppsEnvName = take('${platformName}-cae', 63)
var registrySecretName = 'registry-password'
var databaseUrlSecretName = 'database-url'
var fernetKeySecretName = 'fernet-key'
var gitTokenSecretName = 'git-token'
var secretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6' // built-in "Key Vault Secrets User"

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource containerAppsEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerAppsEnvName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// Shared secret block every app registers against Key Vault via its own
// managed identity (granted "Key Vault Secrets User" below) — never a
// literal value in this file (§1.6/§20).
var keyVaultSecrets = [
  { name: registrySecretName, secretName: keyVaultRegistryPasswordSecretName }
  { name: databaseUrlSecretName, secretName: keyVaultDatabaseUrlSecretName }
  { name: fernetKeySecretName, secretName: keyVaultFernetKeySecretName }
  { name: gitTokenSecretName, secretName: keyVaultGitTokenSecretName }
]

resource backendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: take('${platformName}-api', 32)
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'auto' }
      registries: [
        { server: containerRegistryServer, username: containerRegistryUsername, passwordSecretRef: registrySecretName }
      ]
      secrets: [for s in keyVaultSecrets: { name: s.name, keyVaultUrl: '${keyVault.properties.vaultUri}secrets/${s.secretName}', identity: 'system' }]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: '${backendImage}:${imageTag}'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'APP_ENV', value: environment }
            { name: 'AUTH_MODE', value: authMode }
            { name: 'DATABASE_URL', secretRef: databaseUrlSecretName }
            { name: 'GIT_TOKEN_FERNET_KEY', secretRef: fernetKeySecretName }
            { name: 'KEY_VAULT_NAME', value: keyVaultName }
          ]
        }
      ]
      scale: { minReplicas: minReplicas, maxReplicas: maxReplicas }
    }
  }
}

resource workerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: take('${platformName}-worker', 32)
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      // No ingress — the worker is a stateless poll loop (§23.4), never called over HTTP.
      registries: [
        { server: containerRegistryServer, username: containerRegistryUsername, passwordSecretRef: registrySecretName }
      ]
      secrets: [for s in keyVaultSecrets: { name: s.name, keyVaultUrl: '${keyVault.properties.vaultUri}secrets/${s.secretName}', identity: 'system' }]
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: '${workerImage}:${imageTag}'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'APP_ENV', value: environment }
            { name: 'DATABASE_URL', secretRef: databaseUrlSecretName }
            { name: 'GIT_TOKEN_FERNET_KEY', secretRef: fernetKeySecretName }
            { name: 'GIT_TOKEN', secretRef: gitTokenSecretName }
            { name: 'KEY_VAULT_NAME', value: keyVaultName }
          ]
        }
      ]
      // The worker polls the jobs table — never scale to zero or a queued
      // job could sit unclaimed until the next inbound request wakes a
      // revision (Container Apps only scales HTTP apps on request traffic).
      scale: { minReplicas: 1, maxReplicas: maxReplicas }
    }
  }
}

resource frontendApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: take('${platformName}-web', 32)
  location: location
  properties: {
    managedEnvironmentId: containerAppsEnv.id
    configuration: {
      ingress: { external: true, targetPort: 3000, transport: 'auto' }
      registries: [
        { server: containerRegistryServer, username: containerRegistryUsername, passwordSecretRef: registrySecretName }
      ]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: '${frontendImage}:${imageTag}'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            // Server-only, read at request time by next.config.ts's
            // frontend/middleware.ts — never NEXT_PUBLIC_* (IMPLEMENTATION_PLAN.md B-3).
            // Container Apps' internal DNS resolves other apps in the same
            // environment by name.
            { name: 'BACKEND_INTERNAL_URL', value: 'https://${backendApp.properties.configuration.ingress.fqdn}' }
          ]
        }
      ]
      scale: { minReplicas: minReplicas, maxReplicas: maxReplicas }
    }
  }
}

// Grant each app's managed identity read access to the secrets it references above.
resource backendKvAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, backendApp.id, secretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsUserRoleId)
    principalId: backendApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource workerKvAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, workerApp.id, secretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsUserRoleId)
    principalId: workerApp.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output platformName string = platformName
output environment string = environment
output location string = location
output backendUrl string = 'https://${backendApp.properties.configuration.ingress.fqdn}'
output frontendUrl string = 'https://${frontendApp.properties.configuration.ingress.fqdn}'

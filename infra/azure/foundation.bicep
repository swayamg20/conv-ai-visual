targetScope = 'resourceGroup'

@description('Azure region for the isolated Murmur pilot resources.')
param location string = resourceGroup().location

@description('Name of the Container Apps managed environment.')
param environmentName string = 'murmur-pilot-env'

@description('Name of the backend user-assigned identity with ACR pull and Key Vault access.')
param identityName string = 'murmur-pilot-identity'

@description('Name of the frontend user-assigned identity with ACR pull access only.')
param frontendIdentityName string = 'murmur-web-identity'

@description('Name of the Log Analytics workspace.')
param logWorkspaceName string = 'murmur-pilot-logs'

@description('Name used by the backend Container App.')
param backendAppName string = 'murmur-api'

@description('Name used by the frontend Container App.')
param frontendAppName string = 'murmur-web'

@description('Object ID of the human or service principal running this deployment.')
param deploymentPrincipalObjectId string

@allowed([
  'User'
  'ServicePrincipal'
])
@description('Azure principal type of the deployment operator.')
param deploymentPrincipalType string

@description('Create backend read grants after both versioned secrets exist.')
param grantBackendSecretRead bool = false

var uniqueSuffix = uniqueString(subscription().id, resourceGroup().id)
var registryName = 'murmur${uniqueSuffix}'
var keyVaultName = 'murmur-${uniqueSuffix}-kv'
var deploymentLockStorageName = 'murlock${uniqueSuffix}'
var lockContainerName = 'deployment-locks'
var lockBlobName = 'azure-pilot.lock'

var acrPullRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)
var keyVaultSecretsUserRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)
var storageBlobDataContributorRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logWorkspaceName
  location: location
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    zoneRedundant: false
  }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

resource frontendIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: frontendIdentityName
  location: location
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    dataEndpointEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource registryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, acrPullRoleDefinitionId)
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleDefinitionId
  }
}

resource frontendRegistryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, frontendIdentity.id, acrPullRoleDefinitionId)
  scope: registry
  properties: {
    principalId: frontendIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleDefinitionId
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
    sku: {
      family: 'A'
      name: 'standard'
    }
  }
}

resource azureOpenAiSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  name: '${keyVault.name}/azure-openai-api-key'
}

resource firebaseRuntimeSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  name: '${keyVault.name}/firebase-runtime-service-account-json'
}

resource azureOpenAiSecretRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (grantBackendSecretRead) {
  name: guid(azureOpenAiSecret.id, identity.id, keyVaultSecretsUserRoleDefinitionId)
  scope: azureOpenAiSecret
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleDefinitionId
  }
}

resource firebaseRuntimeSecretRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (grantBackendSecretRead) {
  name: guid(firebaseRuntimeSecret.id, identity.id, keyVaultSecretsUserRoleDefinitionId)
  scope: firebaseRuntimeSecret
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleDefinitionId
  }
}

resource deploymentLockStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: deploymentLockStorageName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Enabled'
    supportsHttpsTrafficOnly: true
  }
}

resource deploymentLockBlobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: deploymentLockStorage
  name: 'default'
}

resource deploymentLockContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: deploymentLockBlobService
  name: lockContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource deploymentLockAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(deploymentLockContainer.id, deploymentPrincipalObjectId, storageBlobDataContributorRoleDefinitionId)
  scope: deploymentLockContainer
  properties: {
    principalId: deploymentPrincipalObjectId
    principalType: deploymentPrincipalType
    roleDefinitionId: storageBlobDataContributorRoleDefinitionId
  }
}

output environmentId string = environment.id
output environmentName string = environment.name
output environmentDefaultDomain string = environment.properties.defaultDomain
output identityId string = identity.id
output identityPrincipalId string = identity.properties.principalId
output frontendIdentityId string = frontendIdentity.id
output frontendIdentityPrincipalId string = frontendIdentity.properties.principalId
output registryId string = registry.id
output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer
output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output deploymentLockStorageAccountName string = deploymentLockStorage.name
output deploymentLockContainerName string = deploymentLockContainer.name
output deploymentLockBlobName string = lockBlobName
output deploymentLockBlobUrl string = '${deploymentLockStorage.properties.primaryEndpoints.blob}${deploymentLockContainer.name}/${lockBlobName}'
output backendUrl string = 'https://${backendAppName}.${environment.properties.defaultDomain}'
output frontendUrl string = 'https://${frontendAppName}.${environment.properties.defaultDomain}'

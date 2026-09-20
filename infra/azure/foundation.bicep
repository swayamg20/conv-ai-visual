targetScope = 'resourceGroup'

@description('Azure region for the isolated Murmur pilot resources.')
param location string = resourceGroup().location

@description('Name of the Container Apps managed environment.')
param environmentName string = 'murmur-pilot-env'

@description('Name of the shared user-assigned identity.')
param identityName string = 'murmur-pilot-identity'

@description('Name of the Log Analytics workspace.')
param logWorkspaceName string = 'murmur-pilot-logs'

@description('Name used by the backend Container App.')
param backendAppName string = 'murmur-api'

@description('Name used by the frontend Container App.')
param frontendAppName string = 'murmur-web'

var uniqueSuffix = uniqueString(subscription().id, resourceGroup().id)
var registryName = 'murmur${uniqueSuffix}'
var keyVaultName = 'murmur-${uniqueSuffix}-kv'

var acrPullRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)
var keyVaultSecretsUserRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
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

resource keyVaultRead 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, identity.id, keyVaultSecretsUserRoleDefinitionId)
  scope: keyVault
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: keyVaultSecretsUserRoleDefinitionId
  }
}

output environmentId string = environment.id
output environmentName string = environment.name
output environmentDefaultDomain string = environment.properties.defaultDomain
output identityId string = identity.id
output identityPrincipalId string = identity.properties.principalId
output registryId string = registry.id
output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer
output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output backendUrl string = 'https://${backendAppName}.${environment.properties.defaultDomain}'
output frontendUrl string = 'https://${frontendAppName}.${environment.properties.defaultDomain}'

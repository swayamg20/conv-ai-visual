targetScope = 'resourceGroup'

@description('Azure region containing the Container Apps environment.')
param location string = resourceGroup().location

param environmentName string = 'murmur-pilot-env'
param identityName string = 'murmur-pilot-identity'
param backendAppName string = 'murmur-api'
param frontendAppName string = 'murmur-web'
param environmentStorageName string = 'murmur-data'

@description('Immutable backend image including its full registry host and manifest digest.')
param backendImage string

@description('Immutable frontend image including its full registry host and manifest digest.')
param frontendImage string

@description('Full accepted source revision represented by both images.')
@minLength(40)
@maxLength(64)
param releaseSha string

@description('Azure OpenAI resource root or /openai/v1 endpoint. This is not a secret.')
param azureOpenAiEndpoint string

@description('Existing Azure OpenAI deployment name.')
param azureOpenAiDeployment string = 'murmur-gpt-oss-120b'

@description('Firebase project used by browser and backend authentication.')
param firebaseProjectId string

var uniqueSuffix = uniqueString(subscription().id, resourceGroup().id)
var registryName = 'murmur${uniqueSuffix}'
var keyVaultName = 'murmur-${uniqueSuffix}-kv'
var backendUrl = 'https://${backendAppName}.${environment.properties.defaultDomain}'
var frontendUrl = 'https://${frontendAppName}.${environment.properties.defaultDomain}'

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: identityName
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource backend 'Microsoft.App/containerApps@2024-03-01' = {
  name: backendAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        allowInsecure: false
        external: true
        targetPort: 8000
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
        transport: 'auto'
      }
      registries: [
        {
          identity: identity.id
          server: registry.properties.loginServer
        }
      ]
      secrets: [
        {
          identity: identity.id
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/azure-openai-api-key'
          name: 'azure-openai-api-key'
        }
        {
          identity: identity.id
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/firebase-service-account-json'
          name: 'firebase-service-account-json'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: backendImage
          env: [
            {
              name: 'PYTHON_DOTENV_DISABLED'
              value: '1'
            }
            {
              name: 'MURMUR_ENVIRONMENT'
              value: 'production'
            }
            {
              name: 'MURMUR_RELEASE_SHA'
              value: releaseSha
            }
            {
              name: 'MURMUR_DATA_DIR'
              value: '/data'
            }
            {
              name: 'MURMUR_SQLITE_JOURNAL_MODE'
              value: 'DELETE'
            }
            {
              name: 'ALLOWED_CORS_ORIGINS'
              value: frontendUrl
            }
            {
              name: 'LLM_PROVIDER'
              value: 'azure_openai'
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: azureOpenAiEndpoint
            }
            {
              name: 'AZURE_OPENAI_DEPLOYMENT'
              value: azureOpenAiDeployment
            }
            {
              name: 'AZURE_OPENAI_API_KEY'
              secretRef: 'azure-openai-api-key'
            }
            {
              name: 'FIREBASE_PROJECT_ID'
              value: firebaseProjectId
            }
            {
              name: 'FIREBASE_SERVICE_ACCOUNT_JSON'
              secretRef: 'firebase-service-account-json'
            }
            {
              name: 'MURMUR_SCENE_ENABLED'
              value: 'true'
            }
            {
              name: 'MURMUR_SCENE_LLM_PROVIDER'
              value: 'azure_openai'
            }
            {
              name: 'MURMUR_SCENE_LLM_MODEL'
              value: azureOpenAiDeployment
            }
            {
              name: 'MURMUR_SCENE_LLM_MAX_TOKENS'
              value: '2048'
            }
            {
              name: 'MURMUR_SCENE_LLM_TIMEOUT_SECONDS'
              value: '30'
            }
            {
              name: 'MURMUR_SCENE_GLOBAL_CONCURRENCY'
              value: '1'
            }
            {
              name: 'MURMUR_SCENE_PER_USER_CONCURRENCY'
              value: '1'
            }
            {
              name: 'MURMUR_SCENE_REQUESTS_PER_MINUTE'
              value: '1'
            }
            {
              name: 'MURMUR_SCENE_PROVIDER_DISPATCHES_PER_MINUTE'
              value: '1'
            }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/healthz'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 2
              periodSeconds: 3
              timeoutSeconds: 2
              failureThreshold: 30
              successThreshold: 1
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 15
              timeoutSeconds: 3
              failureThreshold: 3
              successThreshold: 1
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/readyz'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 3
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 3
              successThreshold: 1
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          volumeMounts: [
            {
              mountPath: '/data'
              volumeName: 'murmur-data'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '5'
              }
            }
          }
        ]
      }
      volumes: [
        {
          name: 'murmur-data'
          storageName: environmentStorageName
          storageType: 'AzureFile'
        }
      ]
    }
  }
}

resource frontend 'Microsoft.App/containerApps@2024-03-01' = {
  name: frontendAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        allowInsecure: false
        external: true
        targetPort: 3000
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
        transport: 'auto'
      }
      registries: [
        {
          identity: identity.id
          server: registry.properties.loginServer
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: frontendImage
          env: [
            {
              name: 'HOSTNAME'
              value: '0.0.0.0'
            }
            {
              name: 'PORT'
              value: '3000'
            }
            {
              name: 'MURMUR_RELEASE_SHA'
              value: releaseSha
            }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/healthz'
                port: 3000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 1
              periodSeconds: 3
              timeoutSeconds: 2
              failureThreshold: 30
              successThreshold: 1
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 3000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 3
              periodSeconds: 15
              timeoutSeconds: 3
              failureThreshold: 3
              successThreshold: 1
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/healthz'
                port: 3000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 2
              periodSeconds: 10
              timeoutSeconds: 3
              failureThreshold: 3
              successThreshold: 1
            }
          ]
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
}

output backendUrl string = backendUrl
output frontendUrl string = frontendUrl
output backendLatestRevisionName string = backend.properties.latestRevisionName
output frontendLatestRevisionName string = frontend.properties.latestRevisionName

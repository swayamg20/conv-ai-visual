targetScope = 'resourceGroup'

@description('Azure region containing the Container Apps environment.')
param location string = resourceGroup().location

param environmentName string = 'murmur-pilot-env'
param identityName string = 'murmur-pilot-identity'
param frontendIdentityName string = 'murmur-web-identity'
param backendAppName string = 'murmur-api'
param frontendAppName string = 'murmur-web'

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

@description('Exact Key Vault version containing the Azure OpenAI key for this release.')
@minLength(32)
@maxLength(32)
param azureOpenAiSecretVersion string

@description('Exact Key Vault version containing the Firebase runtime credential for this release.')
@minLength(32)
@maxLength(32)
param firebaseRuntimeSecretVersion string

@description('LiveKit Cloud WebSocket origin used by the browser control plane and worker.')
param livekitUrl string

@description('ElevenLabs voice identifier selected by the fixed Voice V2 cascade.')
param elevenLabsVoiceId string

@description('Exact Key Vault version containing the LiveKit API key for this release.')
@minLength(32)
@maxLength(32)
param livekitApiKeySecretVersion string

@description('Exact Key Vault version containing the LiveKit API secret for this release.')
@minLength(32)
@maxLength(32)
param livekitApiSecretSecretVersion string

@description('Exact Key Vault version containing the Voice V2 dispatch-signing secret.')
@minLength(32)
@maxLength(32)
param voiceV2SigningSecretVersion string

@description('Exact Key Vault version containing the Deepgram API key for this release.')
@minLength(32)
@maxLength(32)
param deepgramApiKeySecretVersion string

@description('Exact Key Vault version containing the Groq API key for this release.')
@minLength(32)
@maxLength(32)
param groqApiKeySecretVersion string

@description('Exact Key Vault version containing the ElevenLabs API key for this release.')
@minLength(32)
@maxLength(32)
param elevenLabsApiKeySecretVersion string

var uniqueSuffix = uniqueString(subscription().id, resourceGroup().id)
var registryName = 'murmur${uniqueSuffix}'
var keyVaultName = 'murmur-${uniqueSuffix}-kv'
var voiceWorkerName = 'murmur-voice-v2-${substring(releaseSha, 0, 8)}-${substring(voiceV2SigningSecretVersion, 0, 8)}'
var backendUrl = 'https://${backendAppName}.${environment.properties.defaultDomain}'
var frontendUrl = 'https://${frontendAppName}.${environment.properties.defaultDomain}'

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: environmentName
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: identityName
}

resource frontendIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: frontendIdentityName
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource backend 'Microsoft.App/containerApps@2025-01-01' = {
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
      identitySettings: [
        {
          identity: identity.id
          lifecycle: 'None'
        }
      ]
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
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/azure-openai-api-key/${azureOpenAiSecretVersion}'
          name: 'azure-openai-api-key'
        }
        {
          identity: identity.id
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/firebase-runtime-service-account-json/${firebaseRuntimeSecretVersion}'
          name: 'firebase-runtime-service-account-json'
        }
        {
          identity: identity.id
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/livekit-api-key/${livekitApiKeySecretVersion}'
          name: 'livekit-api-key'
        }
        {
          identity: identity.id
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/livekit-api-secret/${livekitApiSecretSecretVersion}'
          name: 'livekit-api-secret'
        }
        {
          identity: identity.id
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/voice-v2-signing-secret/${voiceV2SigningSecretVersion}'
          name: 'voice-v2-signing-secret'
        }
        {
          identity: identity.id
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/deepgram-api-key/${deepgramApiKeySecretVersion}'
          name: 'deepgram-api-key'
        }
        {
          identity: identity.id
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/groq-api-key/${groqApiKeySecretVersion}'
          name: 'groq-api-key'
        }
        {
          identity: identity.id
          keyVaultUrl: '${keyVault.properties.vaultUri}secrets/elevenlabs-api-key/${elevenLabsApiKeySecretVersion}'
          name: 'elevenlabs-api-key'
        }
      ]
    }
    template: {
      terminationGracePeriodSeconds: 600
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
              value: '/home/murmur/data'
            }
            {
              name: 'MURMUR_SQLITE_JOURNAL_MODE'
              value: 'WAL'
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
              name: 'LLM_MAX_TOKENS'
              value: '1024'
            }
            {
              name: 'MURMUR_CHAT_GLOBAL_CONCURRENCY'
              value: '1'
            }
            {
              name: 'MURMUR_CHAT_PER_USER_CONCURRENCY'
              value: '1'
            }
            {
              name: 'MURMUR_CHAT_REQUESTS_PER_MINUTE'
              value: '2'
            }
            {
              name: 'MURMUR_CHAT_MAX_TOOL_ROUNDS'
              value: '2'
            }
            {
              name: 'MURMUR_CHAT_LLM_TRANSPORT_MAX_RETRIES'
              value: '0'
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
              secretRef: 'firebase-runtime-service-account-json'
            }
            {
              name: 'VOICE_RUNTIME'
              value: 'livekit_v2'
            }
            {
              name: 'LIVEKIT_URL'
              value: livekitUrl
            }
            {
              name: 'LIVEKIT_API_KEY'
              secretRef: 'livekit-api-key'
            }
            {
              name: 'LIVEKIT_API_SECRET'
              secretRef: 'livekit-api-secret'
            }
            {
              name: 'VOICE_V2_SIGNING_SECRET'
              secretRef: 'voice-v2-signing-secret'
            }
            {
              name: 'VOICE_V2_PROFILE_ID'
              value: 'livekit-agents-cascade-v1'
            }
            {
              name: 'VOICE_V2_WORKER_NAME'
              value: voiceWorkerName
            }
            {
              name: 'VOICE_V2_MAX_ACTIVE_CALLS'
              value: '1'
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
              periodSeconds: 9
              timeoutSeconds: 2
              failureThreshold: 10
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
              mountPath: '/home/murmur/data'
              volumeName: 'murmur-data'
            }
          ]
        }
        {
          name: 'voice-worker'
          image: backendImage
          command: [
            'python'
            '-m'
            'livekit.agents'
            'start'
          ]
          args: [
            '--log-level'
            'INFO'
            'backend/murmur/voice/worker.py'
          ]
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
              value: '/home/murmur/data'
            }
            {
              name: 'MURMUR_SQLITE_JOURNAL_MODE'
              value: 'WAL'
            }
            {
              name: 'VOICE_RUNTIME'
              value: 'livekit_v2'
            }
            {
              name: 'LIVEKIT_URL'
              value: livekitUrl
            }
            {
              name: 'LIVEKIT_API_KEY'
              secretRef: 'livekit-api-key'
            }
            {
              name: 'LIVEKIT_API_SECRET'
              secretRef: 'livekit-api-secret'
            }
            {
              name: 'VOICE_V2_SIGNING_SECRET'
              secretRef: 'voice-v2-signing-secret'
            }
            {
              name: 'VOICE_V2_PROFILE_ID'
              value: 'livekit-agents-cascade-v1'
            }
            {
              name: 'VOICE_V2_WORKER_NAME'
              value: voiceWorkerName
            }
            {
              name: 'VOICE_V2_PROVIDER_PROBE_TIMEOUT_SECONDS'
              value: '4'
            }
            {
              name: 'VOICE_V2_DRAIN_TIMEOUT_SECONDS'
              value: '540'
            }
            {
              name: 'DEEPGRAM_KEY'
              secretRef: 'deepgram-api-key'
            }
            {
              name: 'GROQ_API_KEY'
              secretRef: 'groq-api-key'
            }
            {
              name: 'ELEVENLABS_API_KEY'
              secretRef: 'elevenlabs-api-key'
            }
            {
              name: 'ELEVENLABS_VOICE_ID'
              value: elevenLabsVoiceId
            }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/'
                port: 8082
                scheme: 'HTTP'
              }
              initialDelaySeconds: 2
              periodSeconds: 12
              timeoutSeconds: 2
              failureThreshold: 10
              successThreshold: 1
            }
            {
              type: 'Liveness'
              httpGet: {
                path: '/'
                port: 8081
                scheme: 'HTTP'
              }
              initialDelaySeconds: 10
              periodSeconds: 15
              timeoutSeconds: 3
              failureThreshold: 3
              successThreshold: 1
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/'
                port: 8082
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 10
              timeoutSeconds: 3
              failureThreshold: 3
              successThreshold: 1
            }
          ]
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          volumeMounts: [
            {
              mountPath: '/home/murmur/data'
              volumeName: 'murmur-data'
            }
          ]
        }
      ]
      volumes: [
        {
          name: 'murmur-data'
          storageType: 'EmptyDir'
        }
      ]
      scale: {
        minReplicas: 1
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
    }
  }
}

resource frontend 'Microsoft.App/containerApps@2025-01-01' = {
  name: frontendAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${frontendIdentity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      identitySettings: [
        {
          identity: frontendIdentity.id
          lifecycle: 'None'
        }
      ]
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
          identity: frontendIdentity.id
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
              periodSeconds: 9
              timeoutSeconds: 2
              failureThreshold: 10
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
        minReplicas: 1
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
  dependsOn: [
    backend
  ]
}

output backendUrl string = backendUrl
output frontendUrl string = frontendUrl
output backendLatestRevisionName string = backend.properties.latestRevisionName
output frontendLatestRevisionName string = frontend.properties.latestRevisionName
output azureOpenAiSecretVersion string = azureOpenAiSecretVersion
output firebaseRuntimeSecretVersion string = firebaseRuntimeSecretVersion
output livekitApiKeySecretVersion string = livekitApiKeySecretVersion
output livekitApiSecretSecretVersion string = livekitApiSecretSecretVersion
output voiceV2SigningSecretVersion string = voiceV2SigningSecretVersion
output deepgramApiKeySecretVersion string = deepgramApiKeySecretVersion
output groqApiKeySecretVersion string = groqApiKeySecretVersion
output elevenLabsApiKeySecretVersion string = elevenLabsApiKeySecretVersion

targetScope = 'resourceGroup'

@description('Unique environment name used for resource naming.')
param environmentName string

@description('Primary Azure region for all resources.')
param location string

@description('Azure region for the AI Foundry account and model deployments.')
param aiDeploymentsLocation string

@description('Azure region for the AI Search service.')
param searchServiceLocation string

@description('Tags applied to all resources.')
param tags object

@description('Id of the user or app to assign application roles.')
param principalId string

@description('Principal type of user or app.')
param principalType string

@description('Optional salt to diversify resource names across project recreations.')
param resourceTokenSalt string

@description('Optional. Name of the AI Foundry (AI Services) account.')
param aiFoundryResourceName string

@description('Name of the AI Foundry project.')
param aiFoundryProjectName string

@description('Model deployments requested by the azd AI agent extension (JSON).')
param aiProjectDeploymentsJson string

@description('Project connections requested by the azd AI agent extension (JSON).')
param aiProjectConnectionsJson string

@secure()
@description('JSON map of connection name to credentials object.')
param aiProjectConnectionCredentialsJson string

@description('Dependent resources requested by the azd AI agent extension (JSON).')
param aiProjectDependentResourcesJson string

@description('Enable hosted agent support on the Foundry project.')
param enableHostedAgents bool

@description('Enable the capability host for hosted agents.')
param enableCapabilityHost bool

@description('Enable monitoring for the Foundry project.')
param enableMonitoring bool

@description('Model deployment name used by the agent and gateway.')
param foundryModel string

@description('Capacity (TPM, in thousands) for the Foundry model deployment.')
param foundryModelCapacity int

@description('Name of the Foundry Toolbox the agent connects to.')
param toolboxName string

@description('Name of the AI Search knowledge base.')
param knowledgeBaseName string

@description('Name of the AI Search index for FoundryIQ documents.')
param searchIndexName string

@description('Container image for the ui service.')
param uiImageName string

@description('Container image for the gateway service.')
param gatewayImageName string

@description('Container image for the agent-service service.')
param agentServiceImageName string

@description('Container image for the inventory-mcp service.')
param inventoryMcpImageName string

@description('Container image for the work-orders-api service.')
param workOrdersApiImageName string

@description('Container image for the status-dashboard service.')
param statusDashboardImageName string

var aiProjectDeployments = json(aiProjectDeploymentsJson)
var aiProjectConnections = json(aiProjectConnectionsJson)
var aiProjectConnectionCreds = json(aiProjectConnectionCredentialsJson)
var aiProjectDependentResources = json(aiProjectDependentResourcesJson)

// When the azd AI agent extension hasn't requested any model deployments
// (e.g. plain `azd provision`), fall back to deploying the default model.
// When the extension does supply deployments, override the capacity of the
// primary model so `azd up` doesn't reset a manually tuned TPM back to the
// extension default.
var effectiveDeployments = empty(aiProjectDeployments) ? [
  {
    name: foundryModel
    model: {
      name: foundryModel
      format: 'OpenAI'
      version: '2025-04-14'
    }
    sku: {
      name: 'GlobalStandard'
      capacity: foundryModelCapacity
    }
  }
] : map(aiProjectDeployments, d => d.name == foundryModel ? union(d, {
  sku: union(d.sku, { capacity: foundryModelCapacity })
}) : d)

var resourceToken = toLower(uniqueString(subscription().subscriptionId, resourceGroup().id, environmentName))
var sanitizedEnvironmentName = replace(toLower(environmentName), '-', '')

var containerAppsEnvironmentName = '${environmentName}-cae'
var logAnalyticsWorkspaceName = '${environmentName}-logs'
var registryName = 'acr${take(sanitizedEnvironmentName, 15)}${take(resourceToken, 8)}'
var storageAccountName = 'st${take(sanitizedEnvironmentName, 10)}${take(resourceToken, 12)}'
var searchServiceName = '${environmentName}-search'

var uiAppName = '${environmentName}-ui'
var gatewayAppName = '${environmentName}-gateway'
var agentServiceAppName = '${environmentName}-agent-service'
var inventoryMcpAppName = '${environmentName}-inventory-mcp'
var workOrdersApiAppName = '${environmentName}-work-orders-api'
var statusDashboardAppName = '${environmentName}-status-dashboard'

var searchEndpoint = 'https://${searchServiceName}.search.windows.net'
var knowledgeBaseMcpEndpoint = '${searchEndpoint}/knowledgebases/${knowledgeBaseName}/mcp'
var kbConnectionName = 'kb-${knowledgeBaseName}'

// Built-in role definition IDs
var roleSearchIndexDataReader = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var roleStorageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var roleStorageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

module logAnalytics 'modules/log-analytics.bicep' = {
  name: 'log-analytics'
  params: {
    name: logAnalyticsWorkspaceName
    location: location
    tags: tags
  }
}

module containerRegistry 'modules/container-registry.bicep' = {
  name: 'container-registry'
  params: {
    name: registryName
    location: location
    tags: tags
  }
}

module storageAccount 'modules/storage-account.bicep' = {
  name: 'storage-account'
  params: {
    name: storageAccountName
    location: location
    tags: tags
  }
}

module aiSearch 'modules/ai-search.bicep' = {
  name: 'ai-search'
  params: {
    name: searchServiceName
    location: searchServiceLocation
    tags: tags
    sku: 'basic'
  }
}

// ─── AI Foundry account, project, model deployments, hosted agent support ───

module aiProject 'core/ai/ai-project.bicep' = {
  name: 'ai-project'
  params: {
    tags: tags
    location: aiDeploymentsLocation
    resourceTokenSalt: resourceTokenSalt
    aiFoundryProjectName: aiFoundryProjectName
    existingAiAccountName: aiFoundryResourceName
    principalId: principalId
    principalType: principalType
    deployments: effectiveDeployments
    connections: union(aiProjectConnections, [
      {
        // Foundry connection to the AI Search knowledge base MCP endpoint.
        // The knowledge base itself is created post-provision by
        // scripts/setup-knowledge-base.sh.
        name: kbConnectionName
        category: 'RemoteTool'
        target: knowledgeBaseMcpEndpoint
        authType: 'ProjectManagedIdentity'
        isSharedToAll: true
        audience: 'https://search.azure.com/'
        metadata: {
          ApiType: 'Azure'
        }
      }
    ])
    connectionCredentials: aiProjectConnectionCreds
    additionalDependentResources: aiProjectDependentResources
    enableMonitoring: enableMonitoring
    enableHostedAgents: enableHostedAgents
    enableCapabilityHost: enableCapabilityHost
    existingContainerRegistryResourceId: containerRegistry.outputs.id
    existingContainerRegistryEndpoint: containerRegistry.outputs.loginServer
  }
}

var foundryProjectEndpoint = aiProject.outputs.FOUNDRY_PROJECT_ENDPOINT
var toolboxMcpUrl = '${foundryProjectEndpoint}/toolboxes/${toolboxName}/mcp'

// ─── RBAC ───

// AI Search managed identity reads FoundryIQ documents from blob storage
resource storageAccountRef 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
  dependsOn: [
    storageAccount
  ]
}

resource searchBlobReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccountRef.id, searchServiceName, 'Storage Blob Data Reader')
  scope: storageAccountRef
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataReader)
    principalId: aiSearch.outputs.principalId
    principalType: 'ServicePrincipal'
  }
}

// Deploying user uploads FoundryIQ documents in the postprovision hook
// (key-based auth is disabled on the storage account)
resource deployerBlobContributorRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(storageAccountRef.id, principalId, roleStorageBlobDataContributor)
  scope: storageAccountRef
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataContributor)
    principalId: principalId
    principalType: principalType
  }
}

// Foundry project + account identities query the knowledge base on AI Search
resource searchServiceRef 'Microsoft.Search/searchServices@2024-06-01-preview' existing = {
  name: searchServiceName
  dependsOn: [
    aiSearch
  ]
}

resource projectSearchReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchServiceRef.id, aiFoundryProjectName, roleSearchIndexDataReader)
  scope: searchServiceRef
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleSearchIndexDataReader)
    principalId: aiProject.outputs.projectPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource accountSearchReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(searchServiceRef.id, 'ai-account', roleSearchIndexDataReader)
  scope: searchServiceRef
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleSearchIndexDataReader)
    principalId: aiProject.outputs.aiServicesPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ─── Container Apps ───

module containerAppsEnvironment 'modules/container-apps-environment.bicep' = {
  name: 'container-apps-environment'
  params: {
    name: containerAppsEnvironmentName
    location: location
    logAnalyticsWorkspaceCustomerId: logAnalytics.outputs.customerId
    logAnalyticsWorkspaceSharedKey: logAnalytics.outputs.sharedKey
    tags: tags
  }
}

module inventoryMcp 'modules/container-app.bicep' = {
  name: 'inventory-mcp-app'
  params: {
    name: inventoryMcpAppName
    serviceName: 'inventory-mcp'
    location: location
    environmentId: containerAppsEnvironment.outputs.id
    containerImage: inventoryMcpImageName
    targetPort: 8001
    env: []
    registryServer: containerRegistry.outputs.loginServer
    registryUsername: containerRegistry.outputs.username
    registryPassword: containerRegistry.outputs.password
    tags: tags
  }
}

module workOrdersApi 'modules/container-app.bicep' = {
  name: 'work-orders-api-app'
  params: {
    name: workOrdersApiAppName
    serviceName: 'work-orders-api'
    location: location
    environmentId: containerAppsEnvironment.outputs.id
    containerImage: workOrdersApiImageName
    targetPort: 8002
    env: []
    registryServer: containerRegistry.outputs.loginServer
    registryUsername: containerRegistry.outputs.username
    registryPassword: containerRegistry.outputs.password
    tags: tags
  }
}

module statusDashboard 'modules/container-app.bicep' = {
  name: 'status-dashboard-app'
  params: {
    name: statusDashboardAppName
    serviceName: 'status-dashboard'
    location: location
    environmentId: containerAppsEnvironment.outputs.id
    containerImage: statusDashboardImageName
    targetPort: 8003
    env: []
    registryServer: containerRegistry.outputs.loginServer
    registryUsername: containerRegistry.outputs.username
    registryPassword: containerRegistry.outputs.password
    tags: tags
  }
}

module agentService 'modules/container-app.bicep' = {
  name: 'agent-service-app'
  params: {
    name: agentServiceAppName
    serviceName: 'agent-service'
    location: location
    environmentId: containerAppsEnvironment.outputs.id
    containerImage: agentServiceImageName
    targetPort: 8000
    enableSystemIdentity: true
    env: [
      {
        name: 'FOUNDRY_PROJECT_ENDPOINT'
        value: foundryProjectEndpoint
      }
      {
        name: 'FOUNDRY_MODEL'
        value: foundryModel
      }
      {
        name: 'TOOLBOX_MCP_URL'
        value: toolboxMcpUrl
      }
      {
        name: 'AZURE_SEARCH_ENDPOINT'
        value: searchEndpoint
      }
      {
        name: 'AZURE_SEARCH_INDEX'
        value: searchIndexName
      }
    ]
    registryServer: containerRegistry.outputs.loginServer
    registryUsername: containerRegistry.outputs.username
    registryPassword: containerRegistry.outputs.password
    tags: tags
  }
}

module gateway 'modules/container-app.bicep' = {
  name: 'gateway-app'
  params: {
    name: gatewayAppName
    serviceName: 'gateway'
    location: location
    environmentId: containerAppsEnvironment.outputs.id
    containerImage: gatewayImageName
    targetPort: 8000
    enableSystemIdentity: true
    env: [
      {
        name: 'AGENT_MODE'
        value: 'containerapp'
      }
      {
        name: 'CONTAINERAPP_AGENT_URL'
        value: 'https://${agentService.outputs.fqdn}'
      }
      {
        name: 'HOSTED_AGENT_ENDPOINT'
        value: foundryProjectEndpoint
      }
      {
        name: 'HOSTED_AGENT_NAME'
        value: 'fibey-agent'
      }
      {
        name: 'FOUNDRY_PROJECT_ENDPOINT'
        value: foundryProjectEndpoint
      }
      {
        name: 'FOUNDRY_MODEL'
        value: foundryModel
      }
      {
        name: 'TOOLBOX_MCP_URL'
        value: toolboxMcpUrl
      }
      {
        name: 'INVENTORY_MCP_URL'
        value: 'https://${inventoryMcp.outputs.fqdn}'
      }
      {
        name: 'WORK_ORDERS_API_URL'
        value: 'https://${workOrdersApi.outputs.fqdn}'
      }
      {
        name: 'STATUS_DASHBOARD_URL'
        value: 'https://${statusDashboard.outputs.fqdn}'
      }
    ]
    registryServer: containerRegistry.outputs.loginServer
    registryUsername: containerRegistry.outputs.username
    registryPassword: containerRegistry.outputs.password
    tags: tags
  }
}

module ui 'modules/container-app.bicep' = {
  name: 'ui-app'
  params: {
    name: uiAppName
    serviceName: 'ui'
    location: location
    environmentId: containerAppsEnvironment.outputs.id
    containerImage: uiImageName
    targetPort: 80
    env: [
      {
        name: 'VITE_API_URL'
        value: '/api'
      }
      {
        // nginx proxies /api/ to the gateway (see ui/nginx.conf)
        name: 'GATEWAY_URL'
        value: 'https://${gateway.outputs.fqdn}/api/'
      }
      {
        name: 'GATEWAY_HOST'
        value: gateway.outputs.fqdn
      }
    ]
    registryServer: containerRegistry.outputs.loginServer
    registryUsername: containerRegistry.outputs.username
    registryPassword: containerRegistry.outputs.password
    tags: tags
  }
}

// Agent-service and gateway identities call the Foundry project and models
module agentServiceAiRoles 'modules/ai-account-role-assignments.bicep' = {
  name: 'agent-service-ai-roles'
  params: {
    aiAccountName: aiProject.outputs.aiServicesAccountName
    principalId: agentService.outputs.principalId
  }
}

module gatewayAiRoles 'modules/ai-account-role-assignments.bicep' = {
  name: 'gateway-ai-roles'
  params: {
    aiAccountName: aiProject.outputs.aiServicesAccountName
    principalId: gateway.outputs.principalId
  }
}

// ─── Outputs ───

output aiAccountId string = aiProject.outputs.accountId
output aiProjectId string = aiProject.outputs.projectId
output aiAccountName string = aiProject.outputs.aiServicesAccountName
output aiProjectName string = aiProject.outputs.projectName
output foundryProjectEndpoint string = foundryProjectEndpoint
output azureOpenAiEndpoint string = aiProject.outputs.AZURE_OPENAI_ENDPOINT
output applicationInsightsConnectionString string = aiProject.outputs.APPLICATIONINSIGHTS_CONNECTION_STRING
output applicationInsightsResourceId string = aiProject.outputs.APPLICATIONINSIGHTS_RESOURCE_ID
output acrConnectionName string = aiProject.outputs.dependentResources.registry.connectionName
output toolboxMcpUrl string = toolboxMcpUrl
output searchServiceName string = aiSearch.outputs.name
output searchServiceEndpoint string = aiSearch.outputs.endpoint
output storageAccountName string = storageAccount.outputs.name
output registryLoginServer string = containerRegistry.outputs.loginServer
output uiFqdn string = ui.outputs.fqdn
output gatewayFqdn string = gateway.outputs.fqdn
output agentServiceFqdn string = agentService.outputs.fqdn
output inventoryMcpFqdn string = inventoryMcp.outputs.fqdn
output workOrdersApiFqdn string = workOrdersApi.outputs.fqdn
output statusDashboardFqdn string = statusDashboard.outputs.fqdn

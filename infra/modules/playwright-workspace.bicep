targetScope = 'resourceGroup'

@description('Name of the Playwright workspace.')
param name string

@description('Azure region supporting both Browser Automation and hosted agents.')
param location string

@description('Tags applied to the workspace.')
param tags object

resource workspace 'Microsoft.LoadTestService/playwrightWorkspaces@2025-09-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    localAuth: 'Disabled'
    regionalAffinity: 'Disabled'
  }
}

output name string = workspace.name
output id string = workspace.id
output mcpEndpoint string = '${replace(workspace.properties.dataplaneUri, '.api.playwright.microsoft.com', '.mcp.playwright.microsoft.com')}/mcp'

$ErrorActionPreference = 'Stop'
& docker compose -p gql-commerce-demo -f (Join-Path $PSScriptRoot 'compose.demo.yaml') up --build -d --wait
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host 'FORMA shop: http://localhost:9110/'
Write-Host 'GraphQL API: http://localhost:9100/graphql'

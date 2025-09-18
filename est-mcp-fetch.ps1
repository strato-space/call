param(
  [string]$HostUrl = "https://voice-mcp.stratospace.fun",
  [string]$ToolName = "fetch",
  [string]$SessionId = "6874fe3f597bcc5c117cfe1a",
  [ValidateSet("full","compact")]
  [string]$Mode = "full",
  [string]$JsonRpcId = "1"
)

# Construct JSON-RPC request
$body = @{
  jsonrpc = "2.0"
  id      = $JsonRpcId
  method  = "tools/call"
  params  = @{
    name      = $ToolName
    arguments = @{
      id   = $SessionId
      mode = $Mode
    }
  }
} | ConvertTo-Json -Depth 6

# Required headers:
# - Accept must include both application/json and text/event-stream (per StreamableHTTP)
# - Content-Type must be application/json
# - mcp-protocol-version optional (server defaults if missing), but we set it explicitly
$headers = @{
  "Accept"                 = "application/json, text/event-stream"
  "Content-Type"           = "application/json"
  "mcp-protocol-version"   = "2024-11-05"
}

# Initialize MCP session to obtain mcp-session-id
$initBody = @{
  jsonrpc = "2.0"
  id      = "init-1"
  method  = "initialize"
  params  = @{
    capabilities = @{
      roots = @(@{ uri = "file:///" })
    }
    clientInfo = @{
      name    = "ps-mcp-test"
      version = "0.1.0"
    }
  }
} | ConvertTo-Json -Depth 6

Write-Host "Initializing MCP session at $HostUrl ..."
try {
  $initResp = Invoke-WebRequest -Method POST -Uri $HostUrl -Headers $headers -Body $initBody -ErrorAction Stop
  $mcpSessionId = $initResp.Headers["mcp-session-id"]
  if (-not $mcpSessionId) {
    Write-Host "Error: no 'mcp-session-id' header found in initialize response." -ForegroundColor Red
    Write-Host "Raw init response body:"
    $initResp.Content | Write-Host
    exit 1
  }
  Write-Host "Session established. mcp-session-id = $mcpSessionId"
} catch {
  Write-Host "Initialize failed:" -ForegroundColor Red
  $_ | Out-String | Write-Host
  exit 1
}

# Use session header for tool call
$toolHeaders = $headers.Clone()
$toolHeaders["mcp-session-id"] = $mcpSessionId

Write-Host "POST $HostUrl"
Write-Host "tool: $ToolName, id: $SessionId, mode: $Mode"
try {
  $resp = Invoke-RestMethod -Method POST -Uri $HostUrl -Headers $toolHeaders -Body $body -ErrorAction Stop
  # Pretty print whole JSON-RPC response
  "Full JSON-RPC response:"
  $resp | ConvertTo-Json -Depth 30
  ""
  # If you just want the tool result payload:
  if ($resp.result) {
    "Tool result:"
    $resp.result | ConvertTo-Json -Depth 30
    if ($resp.result.content) {
      "Tool result.content:"
      $resp.result.content | ConvertTo-Json -Depth 30
    }
  } else { "No result field received." }
}
catch {
  Write-Host "Error:" -ForegroundColor Red
  $_ | Out-String | Write-Host
}
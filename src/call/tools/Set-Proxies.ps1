# Set-Proxies.ps1
# Set system environment variables for proxies

# === Proxy settings ===
$httpProxy  = "http://localhost:10809"
$httpsProxy = "http://localhost:10809"
$allProxy   = "socks5h://localhost:10808"

# NO_PROXY list (comma-separated)
$noProxy = "localhost,127.0.0.1,::1,github.com,*.github.com,api.telegram.org"

# === Set via setx (persistent for the current user) ===
setx HTTP_PROXY  $httpProxy
setx HTTPS_PROXY $httpsProxy
setx ALL_PROXY   $allProxy
setx NO_PROXY    $noProxy

# === Also set for the current session ===
$env:HTTP_PROXY  = $httpProxy
$env:HTTPS_PROXY = $httpsProxy
$env:ALL_PROXY   = $allProxy
$env:NO_PROXY    = $noProxy

Write-Host "Proxy variables set. Restart your terminal/IDE to apply."

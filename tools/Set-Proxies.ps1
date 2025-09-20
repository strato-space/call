# Set-Proxies.ps1
# Установить системные переменные окружения для прокси

# === Настройки прокси ===
$httpProxy  = "http://strato:way-to-win@prompt.stratospace.fun:10809"
$httpsProxy = "http://strato:way-to-win@prompt.stratospace.fun:10809"
$allProxy   = "socks5h://strato:way-to-win@prompt.stratospace.fun:10808"

# NO_PROXY список (через запятую)
$noProxy = "localhost,127.0.0.1,::1,github.com,*.github.com,api.telegram.org"

# === Установка через setx (перманентно для текущего пользователя) ===
setx HTTP_PROXY  $httpProxy
setx HTTPS_PROXY $httpsProxy
setx ALL_PROXY   $allProxy
setx NO_PROXY    $noProxy

# === Также сразу выставляем в текущей сессии ===
$env:HTTP_PROXY  = $httpProxy
$env:HTTPS_PROXY = $httpsProxy
$env:ALL_PROXY   = $allProxy
$env:NO_PROXY    = $noProxy

Write-Host "Прокси переменные установлены. Перезапусти терминал/IDE для применения."

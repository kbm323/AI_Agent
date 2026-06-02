$ErrorActionPreference = "Stop"

function ConvertTo-WslPath {
  param([Parameter(Mandatory = $true)][string]$Path)

  $fullPath = [System.IO.Path]::GetFullPath($Path)
  if ($fullPath -notmatch '^([A-Za-z]):\\(.*)$') {
    throw "Only Windows drive-letter paths can be converted to WSL paths: $fullPath"
  }

  $drive = $matches[1].ToLowerInvariant()
  $rest = $matches[2].Replace('\', '/')
  return "/mnt/$drive/$rest"
}

$distro = if ($env:AI_AGENT_WSL_DISTRO) { $env:AI_AGENT_WSL_DISTRO } else { "Ubuntu" }
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$logDir = Join-Path $repoRoot "data\autostart"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logDir "wsl-gateways-$timestamp.log"

$keepAliveName = "ai-agent-wsl-keepalive"
$keepAliveCheck = "pgrep -f '[t]ail -f /dev/null' >/dev/null 2>&1"

$bash = @'
set -eu
systemctl --user daemon-reload
systemctl --user enable openclaw-gateway.service hermes-gateway.service
systemctl --user start openclaw-gateway.service hermes-gateway.service
printf 'openclaw='
systemctl --user --plain --no-pager is-active openclaw-gateway.service
printf 'hermes='
systemctl --user --plain --no-pager is-active hermes-gateway.service
'@

"[$(Get-Date -Format o)] Starting WSL gateways in distro: $distro" | Tee-Object -FilePath $logPath

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"

& wsl.exe -d $distro --exec bash -lc $keepAliveCheck 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
  "[$(Get-Date -Format o)] Starting hidden WSL keepalive: $keepAliveName" | Tee-Object -FilePath $logPath -Append
  Start-Process -WindowStyle Hidden -FilePath "wsl.exe" -ArgumentList @("-d", $distro, "--exec", "/usr/bin/tail", "-f", "/dev/null") | Out-Null
  Start-Sleep -Seconds 3
  & wsl.exe -d $distro --exec bash -lc $keepAliveCheck 2>&1 | Out-Null
  if ($LASTEXITCODE -eq 0) {
    "[$(Get-Date -Format o)] WSL keepalive started: $keepAliveName" | Tee-Object -FilePath $logPath -Append
  } else {
    "[$(Get-Date -Format o)] WSL keepalive failed to start: $keepAliveName" | Tee-Object -FilePath $logPath -Append
  }
} else {
  "[$(Get-Date -Format o)] WSL keepalive already running: $keepAliveName" | Tee-Object -FilePath $logPath -Append
}

& wsl.exe -d $distro --exec bash -lc $bash 2>&1 | Tee-Object -FilePath $logPath -Append
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorActionPreference
"[$(Get-Date -Format o)] ExitCode=$exitCode" | Tee-Object -FilePath $logPath -Append

exit $exitCode

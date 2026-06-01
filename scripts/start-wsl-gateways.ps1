$ErrorActionPreference = "Stop"

$distro = if ($env:AI_AGENT_WSL_DISTRO) { $env:AI_AGENT_WSL_DISTRO } else { "Ubuntu" }
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$logDir = Join-Path $repoRoot "data\autostart"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logDir "wsl-gateways-$timestamp.log"

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
& wsl.exe -d $distro --exec bash -lc $bash 2>&1 | Tee-Object -FilePath $logPath -Append
$exitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorActionPreference
"[$(Get-Date -Format o)] ExitCode=$exitCode" | Tee-Object -FilePath $logPath -Append

exit $exitCode

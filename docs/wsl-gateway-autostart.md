# WSL Gateway Autostart

This project uses the existing WSL user systemd services:

```text
openclaw-gateway.service
hermes-gateway.service
```

The Windows user Startup folder starts WSL at user logon through:

```text
C:\Users\KBM\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\AI_Agent_WSL_Gateways_Autostart.vbs
```

The VBS file runs:

```powershell
scripts/start-wsl-gateways.ps1
```

The script:

- starts a hidden Windows-attached WSL keepalive process:
  `wsl.exe -d Ubuntu --exec /usr/bin/tail -f /dev/null`
- reloads user systemd
- enables both gateway services
- starts both gateway services
- writes status logs under `data/autostart/`

The keepalive process is required because WSL may shut down shortly after the
startup script exits. If WSL shuts down, the user systemd services are stopped
and the Discord bots appear offline.

Task Scheduler registration was attempted first, but Windows denied access in
this environment. Startup-folder registration is the active autostart method.

Manual start:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File F:\ai-projects\AI_Agent\scripts\start-wsl-gateways.ps1
```

Manual status:

```powershell
wsl.exe -d Ubuntu --exec bash -lc "systemctl --user is-active openclaw-gateway.service hermes-gateway.service"
```

Check keepalive:

```powershell
wsl.exe -d Ubuntu --exec bash -lc "pgrep -a -f '[t]ail -f /dev/null'"
```

Stop all WSL background services:

```powershell
wsl.exe --shutdown
```

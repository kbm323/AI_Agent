# WSL Gateway Autostart

This project uses the existing WSL user systemd services:

```text
openclaw-gateway.service
hermes-gateway.service
```

The Windows scheduled task starts WSL at user logon and runs:

```powershell
scripts/start-wsl-gateways.ps1
```

The script:

- reloads user systemd
- enables both gateway services
- starts both gateway services
- writes status logs under `data/autostart/`

Task name:

```text
AI_Agent_WSL_Gateways_Autostart
```

Manual start:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File F:\ai-projects\AI_Agent\scripts\start-wsl-gateways.ps1
```

Manual status:

```powershell
wsl.exe -d Ubuntu --exec bash -lc "systemctl --user is-active openclaw-gateway.service hermes-gateway.service"
```


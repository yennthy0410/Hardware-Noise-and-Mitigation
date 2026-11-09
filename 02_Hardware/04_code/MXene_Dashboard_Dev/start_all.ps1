param([switch]$Restart)
$ErrorActionPreference = 'Stop'
# Inherit all provider/Ollama settings. Never invoke an automation control.
$launcherPython = Join-Path $PSScriptRoot '.venv_dashboard\Scripts\python.exe'
$launcherArgs = @('-B', (Join-Path $PSScriptRoot 'service_launcher.py'))
if ($Restart) { $launcherArgs += '--restart' }
& $launcherPython @launcherArgs
if ($LASTEXITCODE -ne 0) { throw 'Some services could not be started. Review status and state/service-*.log. No automation action was requested.' }

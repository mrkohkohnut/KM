# One-time setup on the host PC (PC1). Run in PowerShell *as Administrator* from the KM folder:
#   powershell -ExecutionPolicy Bypass -File setup-windows.ps1 -BackupDir "E:\km-backup"
param([string]$BackupDir = "$PSScriptRoot\backup")
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot

# Python from python.org (tick "Add python.exe to PATH" when installing)
$py = (Get-Command python -ErrorAction Stop).Source
if ($py -like '*WindowsApps*') { throw 'Install Python from python.org, not the Microsoft Store alias.' }
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'   # runs without a console window
& $py -m pip install --upgrade flask waitress

# Let the other PCs on the local network reach port 8080
if (-not (Get-NetFirewallRule -DisplayName 'KM (port 8080)' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -DisplayName 'KM (port 8080)' -Direction Inbound -Protocol TCP -LocalPort 8080 -RemoteAddress LocalSubnet -Action Allow | Out-Null
}

# Start KM at boot (before anyone logs in), restart it if it crashes
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'KM server' -Force -User SYSTEM -RunLevel Highest -Settings $settings `
  -Trigger (New-ScheduledTaskTrigger -AtStartup) `
  -Action (New-ScheduledTaskAction -Execute $pyw -Argument "`"$here\app.py`"" -WorkingDirectory $here) | Out-Null

# Nightly backup at 02:00
Register-ScheduledTask -TaskName 'KM backup' -Force -User SYSTEM `
  -Trigger (New-ScheduledTaskTrigger -Daily -At 2am) `
  -Action (New-ScheduledTaskAction -Execute $pyw -Argument "`"$here\app.py`" backup `"$BackupDir`"" -WorkingDirectory $here) | Out-Null

# The host must never sleep
powercfg /change standby-timeout-ac 0

Start-ScheduledTask -TaskName 'KM server'
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -in 'Dhcp','Manual' -and $_.IPAddress -notlike '169.*' } | Select-Object -First 1).IPAddress
Write-Host "KM is running. Other PCs open: http://${ip}:8080"

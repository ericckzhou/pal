# Registers a per-user scheduled task that starts pal (and Docker Desktop) at logon.
# Remove with:  Unregister-ScheduledTask -TaskName pal -Confirm:$false
$script = Join-Path $PSScriptRoot 'start-pal.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'pal' -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description 'Start pal (private assistant) on 127.0.0.1:8765' -Force | Out-Null
Write-Output 'Registered scheduled task "pal" (runs at logon).'

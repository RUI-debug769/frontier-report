#requires -RunAsAdministrator
<#
.SYNOPSIS
  注册 frontier-report 的 Windows 计划任务（日报每天 + 周报每周六）。

.DESCRIPTION
  电池可启动 / 切换电池不中断 / 错过补跑 / 单次日报 2h、周报 3h 时限。
  任务以当前用户交互身份运行（LogonType Interactive），仅在用户登录时触发。

.PARAMETER FrontierReportExe
  frontier-report.exe 完整路径。缺省自动从 PATH 解析（where.exe frontier-report）。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File Register-FrontierReportTasks.ps1
.EXAMPLE
  .\Register-FrontierReportTasks.ps1 -FrontierReportExe "$env:LOCALAPPDATA\Programs\Python\Python312\Scripts\frontier-report.exe" -DailyTime 7:30AM
#>
[CmdletBinding()]
param(
    [string]$FrontierReportExe = "",
    [datetime]$DailyTime = "8:00AM",
    [datetime]$WeeklyTime = "8:30AM",
    [string]$DataDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $FrontierReportExe) {
    $cmd = Get-Command frontier-report -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "未在 PATH 找到 frontier-report，请用 -FrontierReportExe 指定完整路径" }
    $FrontierReportExe = $cmd.Source
}
if (-not (Test-Path $FrontierReportExe)) { throw "frontier-report 不存在: $FrontierReportExe" }

$dailyArgs = "daily"
$weeklyArgs = "weekly"
if ($DataDir) {
    $dailyArgs = "--data-dir `"$DataDir`" daily"
    $weeklyArgs = "--data-dir `"$DataDir`" weekly"
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

function Register-FrontierTask($Name, $Arguments, $Trigger, [int]$LimitHours) {
    $action = New-ScheduledTaskAction -Execute $FrontierReportExe -Argument $Arguments
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours $LimitHours)
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Trigger `
        -Principal $principal -Settings $settings -Force | Out-Null
    $info = Get-ScheduledTaskInfo -TaskName $Name
    [pscustomobject]@{ Task = $Name; NextRunTime = $info.NextRunTime }
}

$dailyTrigger = New-ScheduledTaskTrigger -Daily -At $DailyTime
$weeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At $WeeklyTime

Register-FrontierTask "Frontier-Report-Daily" $dailyArgs $dailyTrigger 2 | Format-List
Register-FrontierTask "Frontier-Report-Weekly" $weeklyArgs $weeklyTrigger 3 | Format-List

Write-Host "完成。SMTP 凭据请用 setx 配置（setx SMTP_USER ... 等），或写入数据目录的 config.toml。"
Write-Host "卸载：Unregister-ScheduledTask -TaskName Frontier-Report-Daily,Frontier-Report-Weekly"

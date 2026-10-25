$ErrorActionPreference = 'Stop'
$taskOldPid = [int](Get-Content "$PSScriptRoot\baseline.pid")
$taskOld = Get-CimInstance Win32_Process -Filter "ProcessId=$taskOldPid"
if ($taskOld -and $taskOld.CommandLine -like '*run_baseline.py*') { Stop-Process -Id $taskOldPid }
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:NO_PROXY = '127.0.0.1,localhost,::1'
$taskPy = 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
$taskRun = Start-Process -FilePath $taskPy -ArgumentList '-B','-u',"$PSScriptRoot\run_baseline.py" -WorkingDirectory 'F:\worktable\research\bench' -RedirectStandardOutput "$PSScriptRoot\baseline.resume.out.log" -RedirectStandardError "$PSScriptRoot\baseline.resume.err.log" -WindowStyle Hidden -PassThru
$taskRun.Id | Set-Content "$PSScriptRoot\baseline.pid"
Write-Output "baseline_resume_pid=$($taskRun.Id)"

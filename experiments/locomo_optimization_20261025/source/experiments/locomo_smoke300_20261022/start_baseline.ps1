$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:NO_PROXY = '127.0.0.1,localhost,::1'
$taskPy = 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
$taskRun = Start-Process -FilePath $taskPy -ArgumentList '-B','-u',"$PSScriptRoot\run_baseline.py" -WorkingDirectory 'F:\worktable\research\bench' -RedirectStandardOutput "$PSScriptRoot\baseline.out.log" -RedirectStandardError "$PSScriptRoot\baseline.err.log" -WindowStyle Hidden -PassThru
$taskRun.Id | Set-Content "$PSScriptRoot\baseline.pid"
Write-Output "baseline_pid=$($taskRun.Id)"

$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:NO_PROXY = '127.0.0.1,localhost,::1'
$taskPy = 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
$taskRun = Start-Process -FilePath $taskPy -ArgumentList '-B','-u',"$PSScriptRoot\run_variant.py" -WorkingDirectory 'F:\worktable\research\bench' -RedirectStandardOutput "$PSScriptRoot\variant.out.log" -RedirectStandardError "$PSScriptRoot\variant.err.log" -WindowStyle Hidden -PassThru
$taskRun.Id | Set-Content "$PSScriptRoot\variant.pid"
Write-Output "variant_pid=$($taskRun.Id)"

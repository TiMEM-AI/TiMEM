$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:NO_PROXY = '127.0.0.1,localhost,::1'
$env:AML_BACKEND_DIR = 'F:\worktable\research\timem-aml'
$env:AML_BACKEND_NOTE = 'Full LoCoMo-Refined; existing retrieval configuration; ownership verification offloaded to thread to avoid event-loop deadlock; NO_PROXY localhost; short run ID'
$taskPy = 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
$taskOut = $PSScriptRoot
$taskServer = Start-Process -FilePath $taskPy -ArgumentList '-B','-u','-m','server' -WorkingDirectory 'F:\worktable\research\timem-aml' -RedirectStandardOutput "$taskOut\server.final.out.log" -RedirectStandardError "$taskOut\server.final.err.log" -WindowStyle Hidden -PassThru
$taskServer.Id | Set-Content -LiteralPath "$taskOut\server.pid"
$taskReady = $false
for ($taskAttempt = 0; $taskAttempt -lt 20; $taskAttempt++) {
    try {
        $taskHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -NoProxy -TimeoutSec 2
        if ($taskHealth.status -eq 'ok') { $taskReady = $true; break }
    } catch { }
    Start-Sleep -Seconds 1
}
if (-not $taskReady) { throw 'TiMEM failed to become ready' }
& $taskPy -B "$taskOut\preflight.py"
if ($LASTEXITCODE -ne 0) { throw 'Add/search preflight failed; evaluation not started' }
$taskEval = Start-Process -FilePath $taskPy -ArgumentList '-B','-u','main.py','aml','--dataset','locomo_refined','--run-id','f1022b' -WorkingDirectory 'F:\worktable\research\bench' -RedirectStandardOutput "$taskOut\eval.final.out.log" -RedirectStandardError "$taskOut\eval.final.err.log" -WindowStyle Hidden -PassThru
$taskEval.Id | Set-Content -LiteralPath "$taskOut\eval.pid"
Write-Output "server_pid=$($taskServer.Id) eval_pid=$($taskEval.Id) run_id=f1022b"

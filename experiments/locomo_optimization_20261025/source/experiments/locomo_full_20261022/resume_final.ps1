$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:NO_PROXY = '127.0.0.1,localhost,::1'
$env:AML_BACKEND_DIR = 'F:\worktable\research\timem-aml'
$env:AML_BACKEND_NOTE = 'Resume f1022b: retain 747 deepseek-chat scores and 748 searches; remaining 114 answer/judge use deepseek-v4.1-flash at kspmas.ksyun.com/v1; aggregate is mixed-model, report batches separately'
$taskPy = 'C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'
$taskHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -NoProxy -TimeoutSec 5
if ($taskHealth.status -ne 'ok') { throw 'TiMEM service is not ready' }
$taskStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
& $taskPy -B -c "import json; from pathlib import Path; p=Path('F:/worktable/research/bench/reports/f1022b'); d=json.loads((p/'aml_score.partial.json').read_text(encoding='utf-8')); assert len(d['results'])==747; s=json.loads((p/'aml_search.json').read_text(encoding='utf-8')); assert len(s['results'])==748; print('resume_checkpoint: 747 scored; 748 searched; 114 pending')"
if ($LASTEXITCODE -ne 0) { throw 'Checkpoint differs from expected; do not start a duplicate evaluation' }
$taskEval = Start-Process -FilePath $taskPy -ArgumentList '-B','-u','main.py','aml-search','f1022b','--score' -WorkingDirectory 'F:\worktable\research\bench' -RedirectStandardOutput "$PSScriptRoot\resume.$taskStamp.out.log" -RedirectStandardError "$PSScriptRoot\resume.$taskStamp.err.log" -WindowStyle Hidden -PassThru
$taskEval.Id | Set-Content -LiteralPath "$PSScriptRoot\eval.pid"
Write-Output "resume_pid=$($taskEval.Id) run_id=f1022b logs=resume.$taskStamp"

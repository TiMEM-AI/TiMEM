"""Active-run orchestration, not a scheduled task."""
import argparse, asyncio, time
from build_hierarchy import ROOT, read
from audit_hierarchy import main as audit
from run_full import main as evaluate

last = -1
last_progress = time.monotonic()
target = len(read(ROOT/'hierarchy_plan.json')['nodes'])
while True:
    try:
        count = len(read(ROOT/'generated_memories.json')['records'])
    except (FileNotFoundError, ValueError):
        count = 0
    if count != last:
        print('Waiting for hierarchy:',count,'/',target,flush=True)
        last = count
        last_progress = time.monotonic()
    if count == target:
        break
    if time.monotonic()-last_progress>900:
        raise RuntimeError('Hierarchy generation stopped progressing; evaluation not started')
    time.sleep(15)
audit()
asyncio.run(evaluate(argparse.Namespace(retrieval='offline',concurrency=24,limit=0)))

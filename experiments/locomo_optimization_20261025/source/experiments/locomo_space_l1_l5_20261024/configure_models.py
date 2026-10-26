from pathlib import Path
import re,json,hashlib
p=Path('F:/worktable/research/bench/.env')
s=p.read_text(encoding='utf-8-sig');before=s
for key in ['ANSWER_MODEL','JUDGE_MODEL']:
 pat=re.compile(r'^'+key+r'=.*$',re.M)
 if pat.search(s):s=pat.sub(key+'=deepseek-v4.1-flash',s)
 else:s+='\n'+key+'=deepseek-v4.1-flash\n'
def rest(t):return '\n'.join(x for x in t.splitlines() if not x.startswith(('ANSWER_MODEL=','JUDGE_MODEL=')))
assert rest(before)==rest(s)
p.write_text(s,encoding='utf-8');print(json.dumps({'answer_model':'deepseek-v4.1-flash','judge_model':'deepseek-v4.1-flash','other_settings_unchanged':True}))

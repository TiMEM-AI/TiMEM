"""Change only the benchmark judge model; preserve existing credentials/URLs."""
from pathlib import Path
import json,re,sys
from dotenv import dotenv_values

path=Path('F:/worktable/research/bench/.env')
before=dotenv_values(path)
text=path.read_text(encoding='utf-8')
pattern=r'(?m)^(JUDGE_MODEL\s*=)[^\r\n]*'
if len(re.findall(pattern,text))>1:
    raise RuntimeError('duplicate JUDGE_MODEL entries')
if re.search(pattern,text):
    revised=re.sub(pattern,lambda m:m.group(1)+'glm-5.3-flash',text)
else:
    revised=text.rstrip('\r\n')+'\nJUDGE_MODEL=glm-5.3-flash\n'
after_values=dotenv_values(stream=__import__('io').StringIO(revised))
assert {k:v for k,v in before.items() if k!='JUDGE_MODEL'}=={k:v for k,v in after_values.items() if k!='JUDGE_MODEL'}
path.write_text(revised,encoding='utf-8')
after=dotenv_values(path)
assert after['JUDGE_MODEL']=='glm-5.3-flash'
print(json.dumps({'updated':str(path),'judge_model':after['JUDGE_MODEL'],
    'answer_model':after.get('ANSWER_MODEL'),'other_configuration_unchanged':True}))

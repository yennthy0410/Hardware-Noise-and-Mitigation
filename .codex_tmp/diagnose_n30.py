import sys,json
from pathlib import Path
root=Path.cwd();sys.path.insert(0,str(root/'02_Hardware/04_code/03_ai_assistant'))
from job_registry import JobRegistry
from independent_n_campaign import campaign_root
reg=JobRegistry(JobRegistry.default_database(root),readonly=True);cid='vqe-independent-n-01'
s=reg.independent_campaign_status(cid)
out={'state':s['state'],'index':s['current_level_index'],'checkpoint':s['checkpoint'],
 'levels':[{k:l.get(k) for k in ('target_n','state','checkpoint_json')} for l in s['levels']],
 'recent_events':s['events'][-12:],
 'n30_jobs':reg.rows(cid+'-n30-block-001'),
 'pause_marker':(campaign_root(root,cid)/'.campaign.pause').exists(),
 'stop_marker':(campaign_root(root,cid)/'.campaign.stop').exists()}
(root/'.codex_tmp/n30_inspection.json').write_text(json.dumps(out,indent=2,default=str))
print(json.dumps({k:v for k,v in out.items() if k not in ('checkpoint','n30_jobs','recent_events')},indent=2))
print('checkpoint',json.dumps(s['checkpoint'],default=str)[:13000])
print('jobs',json.dumps([{k:r.get(k) for k in ('id','job_id','batch_id','account_label','treatment_id','submission_state')} for r in out['n30_jobs']]))
for e in s['events'][-12:]:print(e['created_at'],e['event_type'],str(e.get('details_json'))[:1800])

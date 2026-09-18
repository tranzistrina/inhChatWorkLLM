from server import app, llm as base_llm
import work_routes

@app.get('/api/work/continue/<cid>')
@auth
def work_continue_get(cid):
    with db() as c:r=c.execute('SELECT * FROM work_runs WHERE chat_id=? AND user_id=? ORDER BY iteration DESC,created_at DESC LIMIT 1',(cid,session['uid'])).fetchone()
    if not r:return jsonify(active=False,tasks=[],next_index=None,can_finalize=False)
    tasks=json.loads(r['plan'] or '[]');results=json.loads(r['results'] or '[]');done={int(x['index']) for x in results if 'index' in x};missing=[i for i in range(len(tasks)) if i not in done]
    return jsonify(active=not bool(r['final_answer']),run_id=r['id'],iteration=r['iteration'],tasks=tasks,next_index=(missing[0] if missing else None),can_finalize=bool(tasks) and not missing and not r['final_answer'])

@app.post('/api/work/continue/<cid>')
@auth
def work_continue_post(cid):
    return work_continue_get(cid)

import work_recovery
import os
from llm_retry import with_retries

def resilient_llm(provider, messages):
    return with_retries(base_llm, provider, messages, attempts=3)

# Work Mode and ordinary chat routes use the resilient wrapper without changing their API.
work_routes.llm = resilient_llm
app.run(host='127.0.0.1',port=int(os.getenv('PORT','6767')),debug=False)

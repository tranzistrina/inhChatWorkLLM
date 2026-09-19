import json
from flask import request, jsonify, session
from server import app, db, auth, provider, llm
from work_routes import run_row

@app.get('/api/work/continue/<cid>')
@auth
def continuation_state(cid):
    r=run_row(cid,request.args.get('run_id'))
    if not r:return jsonify(error='Рабочая сессия не найдена'),404
    tasks=json.loads(r['plan'] or '[]');results=json.loads(r['results'] or '[]')
    done={int(x.get('index',-1)) for x in results}
    next_index=next((i for i in range(len(tasks)) if i not in done),None)
    return jsonify(run_id=r['id'],iteration=r['iteration'],tasks=tasks,results=results,next_index=next_index,can_finalize=bool(tasks) and next_index is None,failed=bool(tasks) and next_index is not None)

@app.post('/api/work/continue/<cid>')
@auth
def continue_work(cid):
    d=request.json or {};p=provider(int(d.get('provider_id') or 0));r=run_row(cid,d.get('run_id'))
    if not p or not r:return jsonify(error='Рабочая сессия или провайдер не найдены'),400
    tasks=json.loads(r['plan'] or '[]');results=json.loads(r['results'] or '[]');done={int(x.get('index',-1)) for x in results}
    requested=d.get('index');idx=int(requested) if requested is not None else next((i for i in range(len(tasks)) if i not in done),-1)
    if idx<0 or idx>=len(tasks) or idx in done:return jsonify(error='Нет незавершённой задачи для продолжения'),400
    return jsonify(run_id=r['id'],index=idx,message='Сессию можно продолжить с незавершённой задачи без повторного планирования',can_finalize=False)

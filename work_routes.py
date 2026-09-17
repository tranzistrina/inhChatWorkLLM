import json,uuid,subprocess,re
from pathlib import Path
from flask import request,jsonify,send_file
from server import app,db,auth,provider,llm,WORK,UPLOADS,DEEPSEEK
from workmode import ingest_uploads,source_context,create_archive,parse_archive_requests

def run_row(cid):
 with db() as c:return c.execute('SELECT * FROM work_runs WHERE chat_id=? AND user_id=? ORDER BY created_at DESC LIMIT 1',(cid,__import__('flask').session['uid'])).fetchone()
def artifact_info(paths):
 out=[]
 for rel in paths:
  p=(WORK/rel).resolve()
  if WORK in p.parents and p.is_file():out.append({'name':p.name,'path':str(p.relative_to(WORK)),'size':p.stat().st_size,'url':'/api/files/'+str(p.relative_to(WORK))})
 return out
def save(cid,items,title,pid):
 with db() as c:
  r=c.execute('SELECT * FROM chats WHERE id=? AND user_id=?',(cid,__import__('flask').session['uid'])).fetchone();m=json.loads(r['messages']);m.extend(items);c.execute('UPDATE chats SET title=?,messages=?,provider_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(title,json.dumps(m,ensure_ascii=False),pid,cid))
def materialize_artifacts(raw):
 made=[]
 for m in re.finditer(r'FILE:([^\n]+)\n([\s\S]*?)(?=\nFILE:|\nARCHIVE:|\Z)',raw or ''):
  rel=m.group(1).strip();path=(WORK/rel).resolve()
  if WORK not in path.parents:continue
  path.parent.mkdir(parents=True,exist_ok=True);path.write_text(m.group(2),encoding='utf-8');made.append(str(path.relative_to(WORK)))
 for archive_rel,files in parse_archive_requests(raw):
  try: made.append(create_archive(WORK,archive_rel,files))
  except ValueError: pass
 return made
@app.post('/api/chats/<cid>/message')
@auth
def message(cid):
 text=request.form.get('content','').strip();pid=int(request.form.get('provider_id') or 0);p=provider(pid)
 if not text or not p:return jsonify(error='Введите сообщение и выберите провайдера'),400
 with db() as c:r=c.execute('SELECT * FROM chats WHERE id=? AND user_id=?',(cid,__import__('flask').session['uid'])).fetchone()
 if not r:return jsonify(error='not_found'),404
 try:
  run=UPLOADS/cid;source,_,_=ingest_uploads(request.files.getlist('files'),run);m=json.loads(r['messages']);m.append({'role':'user','content':text,'files':[{'name':x.filename} for x in request.files.getlist('files')]});answer=llm(p,m+[{'role':'system','content':'Attached source files:\n'+source_context(source)}]);made=materialize_artifacts(answer);title=r['title'] if r['title']!='Новый чат' else text[:48];save(cid,[{'role':'assistant','content':answer,'files':artifact_info(made)}],title,p['id']);return jsonify(answer=answer,files=artifact_info(made),title=title)
 except Exception as e:return jsonify(error=str(e)),500
@app.post('/api/work/intake/<cid>')
@auth
def intake(cid):
 pid=int(request.form.get('provider_id') or 0);p=provider(pid)
 if not p:return jsonify(error='Выберите провайдера'),400
 rid=str(uuid.uuid4());dest=UPLOADS/rid
 try:
  source,count,total=ingest_uploads(request.files.getlist('files'),dest);text=request.form.get('content','').strip()
  with db() as c:c.execute('INSERT INTO work_runs(id,user_id,chat_id,request,source_files,plan,results) VALUES(?,?,?,?,?,?,?)',(rid,__import__('flask').session['uid'],cid,text,json.dumps(source,ensure_ascii=False),'[]','[]'))
  return jsonify(run_id=rid,files_count=count,bytes=total,source=[{'path':x['path'],'size':len(x['text'])} for x in source])
 except Exception as e:return jsonify(error=str(e)),400
@app.post('/api/work/plan/<cid>')
@auth
def plan(cid):
 d=request.json or {};p=provider(int(d.get('provider_id') or 0));r=run_row(cid)
 if not p or not r:return jsonify(error='Рабочая сессия или провайдер не найдены'),400
 src=json.loads(r['source_files']);prompt=[{'role':'system','content':'Planning stage. Return ONLY a JSON array with 1-12 sequential tasks. Each object has title and description. Do not execute anything.'},{'role':'user','content':r['request']+'\n\nFILES:\n'+source_context(src)}]
 try:
  raw=llm(p,prompt);from workmode import parse_plan;tasks=parse_plan(raw)
  with db() as c:c.execute('UPDATE work_runs SET plan=?,results=? WHERE id=?',(json.dumps(tasks,ensure_ascii=False),'[]',r['id']))
  return jsonify(tasks=tasks,plan_text=raw)
 except Exception as e:return jsonify(error=str(e)),500
@app.post('/api/work/task/<cid>')
@auth
def task(cid):
 d=request.json or {};idx=int(d.get('index',-1));p=provider(int(d.get('provider_id') or 0));r=run_row(cid)
 if not p or not r:return jsonify(error='Рабочая сессия или провайдер не найдены'),400
 tasks=json.loads(r['plan']);results=json.loads(r['results'])
 if idx<0 or idx>=len(tasks):return jsonify(error='Неверный номер задачи'),400
 src=json.loads(r['source_files']);previous='\n\n'.join(f'TASK {x["index"]+1}: {x["result"]}' for x in results)
 prompt=[{'role':'system','content':'Execution stage. Execute ONLY the assigned task. You have source files and previous task results. If you need to create a text artifact, output exactly FILE:path on one line followed by its full content. If you need to package existing generated files, output exactly ARCHIVE: path/to/archive.zip on one line followed by FILES: path/one, path/two. Do not execute other tasks.'},{'role':'user','content':f'Original request:\n{r["request"]}\n\nAssigned task:\n{json.dumps(tasks[idx],ensure_ascii=False)}\n\nSource files:\n{source_context(src)}\n\nPrevious results:\n{previous}'}]
 try:raw=llm(p,prompt)
 except Exception as e:return jsonify(error=str(e)),500
 made=materialize_artifacts(raw)
 results=[x for x in results if x['index']!=idx];results.append({'index':idx,'result':raw,'files':made})
 with db() as c:c.execute('UPDATE work_runs SET results=? WHERE id=?',(json.dumps(sorted(results,key=lambda x:x['index']),ensure_ascii=False),r['id']))
 return jsonify(result=raw,files=artifact_info(made))
@app.post('/api/work/final/<cid>')
@auth
def final(cid):
 d=request.json or {};p=provider(int(d.get('provider_id') or 0));r=run_row(cid)
 if not p or not r:return jsonify(error='Рабочая сессия или провайдер не найдены'),400
 results=json.loads(r['results']);src=json.loads(r['source_files']);work='\n\n'.join(f'TASK {x["index"]+1}:\n{x["result"]}' for x in results);made=[]
 for x in results:made.extend(x.get('files',[]))
 prompt=[{'role':'system','content':'Final synthesis stage. Prepare the final answer to the user from completed task results. Do not invent work. Mention generated files. Do not create new tasks.'},{'role':'user','content':r['request']+'\n\nSOURCE FILES:\n'+source_context(src)+'\n\nCOMPLETED TASKS:\n'+work}]
 try:answer=llm(p,prompt)
 except Exception as e:return jsonify(error=str(e)),500
 title=r['request'][:48] or 'Рабочая задача';files=artifact_info(made);save(cid,[{'role':'user','content':r['request']},{'role':'assistant','content':answer,'files':files}],title,p['id'])
 with db() as c:c.execute('UPDATE work_runs SET final_answer=? WHERE id=?',(answer,r['id']))
 return jsonify(answer=answer,files=files,title=title)
@app.get('/api/workspace')
@auth
def workspace():
 return jsonify(files=[{'name':p.name,'path':str(p.relative_to(WORK)),'url':'/api/files/'+str(p.relative_to(WORK))} for p in WORK.rglob('*') if p.is_file() and 'uploads' not in p.parts])
@app.get('/api/files/<path:rel>')
@auth
def file(rel):
 p=(WORK/rel).resolve()
 if WORK not in p.parents or not p.is_file():return jsonify(error='Файл не найден'),404
 return send_file(p,as_attachment=True,download_name=p.name)
@app.post('/api/deepseek/auth')
def deepseek_auth():
 v=Path(__file__).resolve().parent/'.vendor'/'FreeDeepseekAPI'
 if not v.exists():return jsonify(error='FreeDeepseekAPI не установлен'),503
 log=open(Path(__file__).resolve().parent/'data'/'deepseek-auth.log','a');subprocess.Popen(['npm','run','auth'],cwd=v,stdout=log,stderr=log,start_new_session=True);return jsonify(ok=True,message='Открывается окно Chrome для входа в DeepSeek.')
@app.get('/api/deepseek/status')
def deepseek_status():
 import requests
 try:r=requests.get(DEEPSEEK.rstrip('/')+'/models',timeout=3);return jsonify(ok=r.ok,models=r.json().get('data',[]))
 except Exception as e:return jsonify(ok=False,error=str(e))

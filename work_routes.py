import json, re, shutil, uuid, zipfile, subprocess, urllib.parse
from pathlib import Path
from flask import request, jsonify, send_file, session
from server import app, db, auth, provider, llm, WORK, DEEPSEEK
from workmode import ingest_uploads, source_context, parse_plan, create_archive
from report_builder import build_report_files, strict_system_prompt
from server import LIBRARY

CHAT_ROOT = WORK / 'chats'; CHAT_ROOT.mkdir(parents=True, exist_ok=True)
TEXT_EXT={'.txt','.md','.markdown','.rst','.py','.js','.ts','.tsx','.jsx','.json','.yaml','.yml','.toml','.ini','.cfg','.conf','.env','.log','.csv','.tsv','.html','.htm','.css','.scss','.xml','.sql','.sh','.bash','.zsh','.bat','.ps1','.java','.kt','.kts','.c','.h','.cpp','.hpp','.cs','.go','.rs','.rb','.php','.swift','.vue','.svelte','.tex'}

with db() as c:
    cols={r['name'] for r in c.execute('PRAGMA table_info(work_runs)').fetchall()}
    if 'iteration' not in cols: c.execute('ALTER TABLE work_runs ADD COLUMN iteration INTEGER DEFAULT 1')
    if 'strict_formatting' not in cols: c.execute('ALTER TABLE work_runs ADD COLUMN strict_formatting INTEGER DEFAULT 0')
    c.execute('''CREATE TABLE IF NOT EXISTS work_events(id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,chat_id TEXT NOT NULL,user_id INTEGER NOT NULL,kind TEXT NOT NULL,message TEXT NOT NULL,data TEXT DEFAULT '{}',created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

def meta_chat_context():
    with db() as c:
        rows=c.execute('SELECT title,messages FROM chats WHERE user_id=? AND id<>? AND temporary=0 ORDER BY updated_at DESC LIMIT 20',(session['uid'],request.view_args.get('cid'))).fetchall()
    out=[]
    for r in rows:
        try:
            ms=json.loads(r['messages']);txt='\n'.join(str(m.get('content','')) for m in ms[-6:] if m.get('role') in ('user','assistant'))
            if txt:out.append('CHAT: '+r['title']+'\n'+txt[:12000])
        except Exception:pass
    return '\n\n'.join(out)[:250000] if out else '(Другие чаты недоступны или пусты)'

def shared_library_context():
    root=(LIBRARY/str(session['uid'])).resolve();root.mkdir(parents=True,exist_ok=True);out=[]
    for p in root.rglob('*'):
        if p.is_file() and p.suffix.lower() in TEXT_EXT:
            try:out.append({'path':'library/'+str(p.relative_to(root)),'text':p.read_text(encoding='utf-8',errors='replace')[:120000]})
            except OSError:pass
    return source_context(out)[:800000] if out else '(Общая библиотека пуста)'

def urlparse_path(url):
    u=urllib.parse.urlparse(url);parts=[x for x in u.path.strip('/').split('/') if x];return parts[1] if len(parts)>1 else 'github_repo'

def chat_root(cid):
    p=(CHAT_ROOT/str(cid)).resolve()
    if CHAT_ROOT not in p.parents: raise ValueError('Небезопасный путь чата')
    p.mkdir(parents=True,exist_ok=True); return p

def iteration_root(cid,n):
    p=chat_root(cid)/'iterations'/str(int(n));p.mkdir(parents=True,exist_ok=True);return p

def safe_file(root,rel):
    p=(root/rel).resolve()
    if p!=root and root not in p.parents: raise ValueError('Путь выходит за пределы рабочего пространства')
    return p

def emit(rid,cid,kind,message,data=None):
    with db() as c:c.execute('INSERT INTO work_events(run_id,chat_id,user_id,kind,message,data) VALUES(?,?,?,?,?,?)',(rid,cid,session['uid'],kind,message,json.dumps(data or {},ensure_ascii=False)))

def run_row(cid):
    with db() as c:return c.execute('SELECT * FROM work_runs WHERE chat_id=? AND user_id=? ORDER BY created_at DESC LIMIT 1',(cid,session['uid'])).fetchone()

def all_chat_files(cid,upto=None):
    root=chat_root(cid)/'iterations';out=[]
    if not root.exists():return out
    for d in root.iterdir():
        if not d.is_dir() or not d.name.isdigit():continue
        n=int(d.name)
        if upto is not None and n>upto:continue
        for p in d.rglob('*'):
            if p.is_file():out.append({'iteration':n,'path':str(p.relative_to(chat_root(cid)))})
    return sorted(out,key=lambda x:(x['iteration'],x['path']))

def readable_source(cid,upto):
    out=[]
    for x in all_chat_files(cid,upto):
        p=chat_root(cid)/x['path']
        if p.suffix.lower() not in TEXT_EXT:continue
        try:out.append({'path':x['path'],'text':p.read_text(encoding='utf-8',errors='replace')[:150000]})
        except OSError:pass
    return out

def context_for_task(cid,iteration,current):
    merged=[];seen=set()
    for x in current+readable_source(cid,iteration-1):
        if x['path'] not in seen:merged.append(x);seen.add(x['path'])
    return source_context(merged)[:2000000]

def artifact_info(cid,paths):
    root=chat_root(cid);out=[]
    for rel in paths:
        try:p=safe_file(root,rel)
        except ValueError:continue
        if p.is_file():out.append({'name':p.name,'path':str(p.relative_to(root)),'size':p.stat().st_size,'url':'/api/files/'+cid+'/'+str(p.relative_to(root))})
    return out

def save_chat(cid,items,title,pid):
    with db() as c:
        r=c.execute('SELECT * FROM chats WHERE id=? AND user_id=?',(cid,session['uid'])).fetchone()
        if not r:return
        m=json.loads(r['messages']);m.extend(items);c.execute('UPDATE chats SET title=?,messages=?,provider_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?',(title,json.dumps(m,ensure_ascii=False),pid,cid,session['uid']))

def parse_directives(raw):
    copies=[];attaches=[];archives=[]
    for m in re.finditer(r'COPY_FROM:\s*([^\n]+?)\s*=>\s*([^\n]+)',raw or '',re.I):copies.append((m.group(1).strip(),m.group(2).strip()))
    for m in re.finditer(r'ATTACH:\s*([^\n]+)',raw or '',re.I):attaches += [x.strip() for x in re.split(r'[,;]',m.group(1)) if x.strip()]
    for m in re.finditer(r'\[\[ATTACH:\s*([^\]]+)\]\]',raw or '',re.I):attaches.append(m.group(1).strip().replace('\\_', '_'))
    for m in re.finditer(r'ARCHIVE:\s*([^\n]+)\nFILES:\s*([^\n]+)',raw or '',re.I):archives.append((m.group(1).strip(),[x.strip() for x in re.split(r'[,;]',m.group(2)) if x.strip()]))
    return copies,attaches,archives

def materialize_task(cid,iteration,raw):
    root=chat_root(cid);current=iteration_root(cid,iteration);made=[]
    for m in re.finditer(r'FILE:\s*([^\n]+)\n([\s\S]*?)(?=\nFILE:|\nCOPY_FROM:|\nARCHIVE:|\Z)',raw or '',re.I):
        try:p=safe_file(current,m.group(1).strip());p.parent.mkdir(parents=True,exist_ok=True);p.write_text(m.group(2),encoding='utf-8');made.append(str(p.relative_to(root)))
        except ValueError:continue
    for m in re.finditer(r'GITHUB:\s*(https?://github\.com/[^\s]+?)(?:\s*=>\s*([^\n]+))?\n',raw or '',re.I):
        url=m.group(1).rstrip('/').rstrip('.');dst=(m.group(2) or urlparse_path(url)).strip()
        try:
            u=urllib.parse.urlparse(url);parts=[x for x in u.path.strip('/').split('/') if x]
            if u.netloc.lower()!='github.com' or len(parts)<2:continue
            repo_url='https://github.com/'+parts[0]+'/'+parts[1].removesuffix('.git')
            target=safe_file(current,dst);target.mkdir(parents=True,exist_ok=True)
            subprocess.run(['git','clone','--depth','1',repo_url,str(target)],check=True,timeout=180,capture_output=True,text=True)
            made.append(str(target.relative_to(root)))
        except (ValueError,OSError,subprocess.SubprocessError):continue
    copies,_,archives=parse_directives(raw)
    for src_rel,dst_rel in copies:
        try:
            src=safe_file(root,src_rel);dst=safe_file(current,dst_rel)
            if src.is_file() and root in src.parents and dst!=src:
                dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst);made.append(str(dst.relative_to(root)))
        except (ValueError,OSError):continue
    for name,paths in archives:
        try:
            rels=[]
            for rel in paths:
                p=safe_file(root,rel)
                if p.is_file() and root in p.parents:rels.append(str(p.relative_to(root)))
            if rels:made.append(create_archive(root,name if name.lower().endswith('.zip') else name+'.zip',rels))
        except (ValueError,OSError,zipfile.BadZipFile):continue
    return made

@app.post('/api/work/intake/<cid>')
@auth
def intake(cid):
    pid=int(request.form.get('provider_id') or 0);p=provider(pid);text=request.form.get('content','').strip();files=request.files.getlist('files');strict_formatting=request.form.get('strict_formatting','0').lower() in ('1','true','yes','on')
    if not p:return jsonify(error='Выберите провайдера'),400
    with db() as c:
        if not c.execute('SELECT id FROM chats WHERE id=? AND user_id=?',(cid,session['uid'])).fetchone():return jsonify(error='not_found'),404
        n=c.execute('SELECT COALESCE(MAX(iteration),0) n FROM work_runs WHERE chat_id=? AND user_id=?',(cid,session['uid'])).fetchone()['n']+1
    rid=str(uuid.uuid4());dest=iteration_root(cid,n)
    try:
        source,count,total=ingest_uploads(files,dest);source=[{'path':'iterations/'+str(n)+'/'+x['path'],'text':x['text']} for x in source]
        with db() as c:c.execute('INSERT INTO work_runs(id,user_id,chat_id,request,source_files,plan,results,iteration,strict_formatting) VALUES(?,?,?,?,?,?,?,?,?)',(rid,session['uid'],cid,text,json.dumps(source,ensure_ascii=False),'[]','[]',n,1 if strict_formatting else 0))
        emit(rid,cid,'intake','Материалы загружены',{'iteration':n,'files':count,'bytes':total})
        return jsonify(run_id=rid,iteration=n,files_count=count,bytes=total,source=[{'path':x['path'],'size':len(x['text'])} for x in source])
    except Exception as e:return jsonify(error=str(e)),400

@app.get('/api/work/status/<cid>')
@auth
def work_status(cid):
    r=run_row(cid)
    if not r:return jsonify(active=False,events=[])
    after=int(request.args.get('after',0))
    with db() as c:ev=c.execute('SELECT id,kind,message,data,created_at FROM work_events WHERE run_id=? AND id>? ORDER BY id LIMIT 100',(r['id'],after)).fetchall()
    return jsonify(active=not bool(r['final_answer']),run_id=r['id'],iteration=r['iteration'],events=[{'id':x['id'],'kind':x['kind'],'message':x['message'],'data':json.loads(x['data'] or '{}'),'created_at':x['created_at']} for x in ev])

@app.post('/api/work/plan/<cid>')
@auth
def plan(cid):
    d=request.json or {};p=provider(int(d.get('provider_id') or 0));r=run_row(cid)
    if not p or not r:return jsonify(error='Рабочая сессия или провайдер не найдены'),400
    src=json.loads(r['source_files']);meta=bool(r['meta_analysis']) if 'meta_analysis' in r.keys() else False;emit(r['id'],cid,'plan_start','Составляю план задач')
    try:
        raw=llm(p,[{'role':'system','content':'Planning stage. Return ONLY a JSON array with 1-12 sequential tasks. Each object has title and description. Do not execute anything.'},{'role':'user','content':r['request']+'\n\nSHARED LIBRARY:\n'+shared_library_context()+'\n\nCROSS-CHAT META ANALYSIS:\n'+(meta_chat_context() if meta else '(Выключен. Другие чаты недоступны.)')+'\n\nFILES:\n'+context_for_task(cid,r['iteration'],src)}]);tasks=parse_plan(raw)
        with db() as c:c.execute('UPDATE work_runs SET plan=?,results=? WHERE id=?',(json.dumps(tasks,ensure_ascii=False),'[]',r['id']))
        emit(r['id'],cid,'plan_done',f'План готов: {len(tasks)} задач',{'count':len(tasks)});return jsonify(tasks=tasks,plan_text=raw)
    except Exception as e:emit(r['id'],cid,'error',str(e));return jsonify(error=str(e)),500

@app.post('/api/work/task/<cid>')
@auth
def task(cid):
    d=request.json or {};idx=int(d.get('index',-1));p=provider(int(d.get('provider_id') or 0));r=run_row(cid)
    if not p or not r:return jsonify(error='Рабочая сессия или провайдер не найдены'),400
    tasks=json.loads(r['plan']);results=json.loads(r['results'])
    if idx<0 or idx>=len(tasks):return jsonify(error='Неверный номер задачи'),400
    src=json.loads(r['source_files']);meta=bool(r['meta_analysis']) if 'meta_analysis' in r.keys() else False;previous='\n\n'.join(f'TASK {x["index"]+1}:\n{x["result"]}' for x in results);available='\n'.join('- '+x['path'] for x in all_chat_files(cid,r['iteration']))
    prompt=[{'role':'system','content':'''Execution stage. Execute ONLY the assigned task. This chat is isolated. You can read any AVAILABLE FILE from this chat, including previous iterations. You can copy a previous file into the current iteration.
Create text artifacts with FILE:path followed by full content. Paths after FILE are relative to the CURRENT iteration.
Copy with COPY_FROM: iterations/N/path => destination/path.
Create a ZIP with ARCHIVE: name.zip followed by FILES: path1, path2. FILES may reference any iteration in this chat.
Clone a public GitHub repository with GITHUB: https://github.com/owner/repo => repo_name. Only github.com public repository URLs are allowed.
Do not perform other tasks.'''.strip()},{'role':'user','content':f'Original request:\n{r["request"]}\n\nAssigned task:\n{json.dumps(tasks[idx],ensure_ascii=False)}\n\nAVAILABLE FILES:\n{available}\n\nSOURCE TEXT:\n{context_for_task(cid,r["iteration"],src)}\n\nPREVIOUS TASK RESULTS:\n{previous}'}]
    emit(r['id'],cid,'task_start',f'Выполняю задачу {idx+1} из {len(tasks)}',{'index':idx})
    try:raw=llm(p,prompt);made=materialize_task(cid,r['iteration'],raw)
    except Exception as e:emit(r['id'],cid,'error',str(e),{'index':idx});return jsonify(error=str(e)),500
    results=[x for x in results if x['index']!=idx];results.append({'index':idx,'result':raw,'files':made})
    with db() as c:c.execute('UPDATE work_runs SET results=? WHERE id=?',(json.dumps(sorted(results,key=lambda x:x['index']),ensure_ascii=False),r['id']))
    emit(r['id'],cid,'task_done',f'Задача {idx+1} завершена',{'index':idx,'files':len(made)});return jsonify(result=raw,files=artifact_info(cid,made))

@app.post('/api/work/final/<cid>')
@auth
def final(cid):
    d=request.json or {};p=provider(int(d.get('provider_id') or 0));r=run_row(cid)
    if not p or not r:return jsonify(error='Рабочая сессия или провайдер не найдены'),400
    results=json.loads(r['results']);src=json.loads(r['source_files']);meta=bool(r['meta_analysis']) if 'meta_analysis' in r.keys() else False;work='\n\n'.join(f'TASK {x["index"]+1}:\n{x["result"]}' for x in results);available='\n'.join('- '+x['path'] for x in all_chat_files(cid,r['iteration']))
    emit(r['id'],cid,'final_start','Готовлю итоговый ответ и выбираю вложения')
    strict=bool(r['strict_formatting']) if 'strict_formatting' in r.keys() else False
    if strict:
        prompt=[{'role':'system','content':strict_system_prompt()},{'role':'user','content':r['request']+'\n\nSOURCE FILES:\n'+context_for_task(cid,r['iteration'],src)+'\n\nCOMPLETED TASKS:\n'+work+'\n\nAVAILABLE FILES:\n'+available}]
    else:
        prompt=[{'role':'system','content':'Final synthesis stage. Prepare the final answer from completed task results. Do not invent work. Decide which files, if any, should be attached. Insert a file anywhere in the response with [[ATTACH: path]]. A selected file may be from any previous iteration of this same chat. Attach only files materially useful to the user.'},{'role':'user','content':r['request']+'\n\nSHARED LIBRARY:\n'+shared_library_context()+'\n\nCROSS-CHAT META ANALYSIS:\n'+(meta_chat_context() if meta else '(Выключен. Другие чаты недоступны.)')+'\n\nAVAILABLE FILES:\n'+available+'\n\nSOURCE FILES:\n'+context_for_task(cid,r['iteration'],src)+'\n\nCOMPLETED TASKS:\n'+work}]
    try:answer=llm(p,prompt)
    except Exception as e:emit(r['id'],cid,'error',str(e));return jsonify(error=str(e)),500
    if strict:
        try:
            generated=build_report_files(answer,iteration_root(cid,r['iteration'])/'report','Отчет_ЛЗ_'+str(r['iteration']))
            generated=[str(x.relative_to(chat_root(cid))) for x in generated]
            answer='Готовый отчет сформирован в строгом формате. В документе оставлены места для рисунков и скриншотов.\n\n[[ATTACH: '+generated[0]+']]\n\n[[ATTACH: '+generated[1]+']]'
            emit(r['id'],cid,'report_done','DOCX и PDF отчета сформированы',{'files':generated})
        except Exception as e:
            emit(r['id'],cid,'error','Не удалось сформировать отчет: '+str(e));return jsonify(error='Не удалось сформировать DOCX/PDF: '+str(e)),500
    _,attaches,_=parse_directives(answer);valid=[];root=chat_root(cid)
    for rel in attaches:
        try:pth=safe_file(root,rel)
        except ValueError:continue
        if pth.is_file() and root in pth.parents:valid.append(str(pth.relative_to(root)))
    files=artifact_info(cid,valid);title=r['request'][:48] or 'Рабочая задача';save_chat(cid,[{'role':'user','content':r['request']},{'role':'assistant','content':answer,'files':files}],title,p['id'])
    with db() as c:c.execute('UPDATE work_runs SET final_answer=? WHERE id=?',(answer,r['id']))
    emit(r['id'],cid,'final_done','Готово',{'attachments':len(files)});return jsonify(answer=answer,files=files,title=title)

@app.get('/api/workspace')
@auth
def workspace():
    cid=request.args.get('chat_id')
    if not cid:return jsonify(files=[])
    with db() as c:
        if not c.execute('SELECT id FROM chats WHERE id=? AND user_id=?',(cid,session['uid'])).fetchone():return jsonify(error='not_found'),404
    return jsonify(files=artifact_info(cid,[x['path'] for x in all_chat_files(cid)]))

@app.get('/api/files/<cid>/<path:rel>')
@auth
def file(cid,rel):
    with db() as c:
        if not c.execute('SELECT id FROM chats WHERE id=? AND user_id=?',(cid,session['uid'])).fetchone():return jsonify(error='not_found'),404
    try:p=safe_file(chat_root(cid),rel)
    except ValueError:return jsonify(error='Небезопасный путь'),403
    if not p.is_file():return jsonify(error='Файл не найден'),404
    return send_file(p,as_attachment=True,download_name=p.name)

@app.post('/api/deepseek/auth')
@auth
def deepseek_auth():
    v=Path(__file__).resolve().parent/'.vendor'/'FreeDeepseekAPI'
    if not v.exists():return jsonify(error='FreeDeepseekAPI не установлен'),503
    log=open(Path(__file__).resolve().parent/'data'/'deepseek-auth.log','a');subprocess.Popen(['npm','run','auth'],cwd=v,stdout=log,stderr=log,start_new_session=True);return jsonify(ok=True,message='Открывается окно Chrome для входа в DeepSeek.')

@app.get('/api/deepseek/status')
def deepseek_status():
    import requests
    try:r=requests.get(DEEPSEEK.rstrip('/')+'/models',timeout=3);return jsonify(ok=r.ok,models=r.json().get('data',[]))
    except Exception as e:return jsonify(ok=False,error=str(e))


@app.get('/api/work/continue/<cid>')
@auth
def work_continue(cid):
    r=run_row(cid)
    if not r:
        return jsonify(active=False,tasks=[],next_index=None,can_finalize=False)
    tasks=json.loads(r['plan'] or '[]')
    results=json.loads(r['results'] or '[]')
    done={int(x['index']) for x in results if 'index' in x}
    missing=[i for i in range(len(tasks)) if i not in done]
    return jsonify(active=not bool(r['final_answer']),run_id=r['id'],iteration=r['iteration'],tasks=tasks,next_index=(missing[0] if missing else None),can_finalize=bool(tasks) and not missing and not r['final_answer'])

@app.post('/api/work/continue/<cid>')
@auth
def work_continue_post(cid):
    return work_continue(cid)

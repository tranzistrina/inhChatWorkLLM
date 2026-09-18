import os,json,uuid,sqlite3,subprocess
from pathlib import Path
from flask import Flask,request,jsonify,session,send_from_directory,send_file
from werkzeug.security import generate_password_hash,check_password_hash
from dotenv import load_dotenv
from workmode import ingest_uploads,source_context,parse_plan
load_dotenv()
ROOT=Path(__file__).resolve().parent;DATA=ROOT/'data';DATA.mkdir(exist_ok=True);WORK=ROOT/'workspace';WORK.mkdir(exist_ok=True);UPLOADS=WORK/'uploads';UPLOADS.mkdir(exist_ok=True);LIBRARY=WORK/'library';LIBRARY.mkdir(exist_ok=True);DB=DATA/'inhchat.db';USERS=ROOT/'users.json';PORT=int(os.getenv('PORT','6767'));DEEPSEEK=os.getenv('DEEPSEEK_BASE_URL','http://127.0.0.1:9655/v1')
app=Flask(__name__,static_folder='static',static_url_path='/static');app.secret_key=os.getenv('APP_SECRET_KEY','change-me-in-.env');app.config['MAX_CONTENT_LENGTH']=32*1024*1024

def db():c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c
with db() as c:c.executescript('CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created_at DATETIME DEFAULT CURRENT_TIMESTAMP);CREATE TABLE IF NOT EXISTS providers(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,name TEXT NOT NULL,base_url TEXT NOT NULL,api_key TEXT DEFAULT "",model TEXT NOT NULL,kind TEXT DEFAULT "openai",created_at DATETIME DEFAULT CURRENT_TIMESTAMP,UNIQUE(user_id,name));CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,title TEXT NOT NULL,messages TEXT NOT NULL,provider_id INTEGER,archived INTEGER DEFAULT 0,created_at DATETIME DEFAULT CURRENT_TIMESTAMP,updated_at DATETIME DEFAULT CURRENT_TIMESTAMP);CREATE TABLE IF NOT EXISTS work_runs(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,chat_id TEXT NOT NULL,request TEXT NOT NULL,source_files TEXT NOT NULL,plan TEXT NOT NULL,results TEXT NOT NULL,final_answer TEXT DEFAULT "",created_at DATETIME DEFAULT CURRENT_TIMESTAMP);')

with db() as c:
    cols={r['name'] for r in c.execute('PRAGMA table_info(chats)').fetchall()}
    if 'archived' not in cols: c.execute('ALTER TABLE chats ADD COLUMN archived INTEGER DEFAULT 0')

def users():
 if not USERS.exists():USERS.write_text(json.dumps([{'username':'admin','password':'676769'}],ensure_ascii=False,indent=2)+'\n');USERS.chmod(0o600)
 return json.loads(USERS.read_text())
def sync():
 with db() as c:
  for u in users():
   name=str(u['username']).strip().lower();h=generate_password_hash(str(u['password']),method='pbkdf2:sha256:600000');r=c.execute('SELECT id FROM users WHERE email=?',(name,)).fetchone()
   if r:c.execute('UPDATE users SET password_hash=? WHERE id=?',(h,r['id']))
   else:c.execute('INSERT INTO users(email,password_hash) VALUES(?,?)',(name,h))
sync()
def auth(f):
 def w(*a,**k):
  if 'uid' not in session:return jsonify(error='auth_required'),401
  active={str(x['username']).lower() for x in users()}
  with db() as c:u=c.execute('SELECT * FROM users WHERE id=?',(session['uid'],)).fetchone()
  if not u or u['email'] not in active:session.clear();return jsonify(error='auth_required'),401
  return f(*a,**k)
 w.__name__=f.__name__;return w
def provider(pid):
 with db() as c:return c.execute('SELECT * FROM providers WHERE id=? AND user_id=?',(pid,session['uid'])).fetchone()
def llm(p,messages):
 import requests
 h={'Content-Type':'application/json'}
 if p['api_key']:h['Authorization']='Bearer '+p['api_key']
 r=requests.post(p['base_url'].rstrip('/')+'/chat/completions',json={'model':p['model'],'messages':messages,'temperature':0.3},headers=h,timeout=300);r.raise_for_status();return r.json()['choices'][0]['message'].get('content','')
def library_text():
    root=(LIBRARY/str(session['uid'])).resolve();root.mkdir(parents=True,exist_ok=True);out=[]
    for p in root.rglob('*'):
        if p.is_file() and p.suffix.lower() in {'.txt','.md','.markdown','.json','.yaml','.yml','.csv','.tsv','.py','.js','.ts','.html','.css','.xml','.sql'}:
            try:out.append({'path':str(p.relative_to(root)),'text':p.read_text(encoding='utf-8',errors='replace')[:120000]})
            except OSError:pass
    return source_context(out)[:800000] if out else '(Библиотека пуста)'

def files_out(paths):
 out=[]
 for rel in paths:
  p=(WORK/rel).resolve()
  if WORK in p.parents and p.is_file():out.append({'name':p.name,'size':p.stat().st_size,'url':'/api/files/'+str(p.relative_to(WORK))})
 return out
@app.get('/')
def index():return send_from_directory('static','index.html')
@app.get('/api/me')
def me():
 with db() as c:u=c.execute('SELECT id,email FROM users WHERE id=?',(session.get('uid',-1),)).fetchone()
 return jsonify({'user':dict(u) if u else None})
@app.post('/api/auth/login')
def login():
 d=request.json or {};name=str(d.get('username','')).lower().strip();u=next((x for x in users() if str(x['username']).lower()==name),None)
 if not u:return jsonify(error='Неверный логин или пароль'),401
 with db() as c:r=c.execute('SELECT * FROM users WHERE email=?',(name,)).fetchone()
 if not r or not check_password_hash(r['password_hash'],str(d.get('password',''))):return jsonify(error='Неверный логин или пароль'),401
 session['uid']=r['id'];return jsonify(ok=True,user={'id':r['id'],'username':name})
@app.post('/api/auth/logout')
def logout():session.clear();return jsonify(ok=True)
@app.get('/api/providers')
@auth
def providers():
 with db() as c:r=c.execute('SELECT id,name,base_url,model,kind FROM providers WHERE user_id=?',(session['uid'],)).fetchall()
 return jsonify([dict(x) for x in r])
@app.get('/api/providers/<int:pid>/models')
@auth
def provider_models(pid):
    import requests
    p=provider(pid)
    if not p:return jsonify(error='Провайдер не найден'),404
    try:
        h={'Authorization':'Bearer '+p['api_key']} if p['api_key'] else {}
        r=requests.get(p['base_url'].rstrip('/')+'/models',headers=h,timeout=15);r.raise_for_status();data=r.json()
        models=[x.get('id') for x in data.get('data',[]) if isinstance(x,dict) and x.get('id')]
        return jsonify(models=models)
    except Exception as e:return jsonify(error=f'Не удалось получить модели: {e}'),502

@app.post('/api/model-discovery')
@auth
def model_discovery():
    import requests
    d=request.json or {};base=str(d.get('base_url','')).strip();key=str(d.get('api_key',''))
    if not base:return jsonify(error='Укажите Base URL'),400
    try:
        headers={'Authorization':'Bearer '+key} if key else {}
        resp=requests.get(base.rstrip('/')+'/models',headers=headers,timeout=15);resp.raise_for_status()
        data=resp.json();models=[x.get('id') for x in data.get('data',[]) if isinstance(x,dict) and x.get('id')]
        return jsonify(models=models)
    except Exception as e:return jsonify(error='Не удалось получить модели: '+str(e)),502

@app.post('/api/providers')
@auth
def add_provider():
 d=request.json or {};name=d.get('name','').strip();base=d.get('base_url','').strip();model=d.get('model','').strip()
 if not name or not base or not model:return jsonify(error='Заполните название, Base URL и модель'),400
 with db() as c:
  try:r=c.execute('INSERT INTO providers(user_id,name,base_url,api_key,model,kind) VALUES(?,?,?,?,?,?)',(session['uid'],name,base,d.get('api_key',''),model,d.get('kind','openai')))
  except sqlite3.IntegrityError:return jsonify(error='Провайдер с таким именем уже существует'),409
 return jsonify(id=r.lastrowid,name=name)
@app.patch('/api/providers/<int:pid>')
@auth
def edit_provider(pid):
    d=request.json or {}
    name=str(d.get('name','')).strip();base=str(d.get('base_url','')).strip();model=str(d.get('model','')).strip()
    if not name or not base or not model:return jsonify(error='Заполните название, Base URL и модель'),400
    with db() as c:
        try:c.execute('UPDATE providers SET name=?,base_url=?,api_key=?,model=?,kind=? WHERE id=? AND user_id=?',(name,base,d.get('api_key',''),model,d.get('kind','openai'),pid,session['uid']))
        except sqlite3.IntegrityError:return jsonify(error='Провайдер с таким именем уже существует'),409
    return jsonify(ok=True)
@app.delete('/api/providers/<int:pid>')
@auth
def del_provider(pid):
 with db() as c:c.execute('DELETE FROM providers WHERE id=? AND user_id=?',(pid,session['uid']))
 return jsonify(ok=True)
@app.get('/api/library')
@auth
def library():
    root=(LIBRARY/str(session['uid'])).resolve();root.mkdir(parents=True,exist_ok=True)
    out=[]
    for p in root.rglob('*'):
        if p.is_file():out.append({'path':str(p.relative_to(root)),'name':p.name,'size':p.stat().st_size,'url':'/api/library/file/'+str(p.relative_to(root))})
    return jsonify(files=sorted(out,key=lambda x:x['path']))

@app.post('/api/library')
@auth
def library_upload():
    root=(LIBRARY/str(session['uid'])).resolve();root.mkdir(parents=True,exist_ok=True)
    files=request.files.getlist('files')
    if not files:return jsonify(error='Файлы не выбраны'),400
    made=[]
    for f in files:
        name=Path(f.filename or '').name
        if not name:continue
        p=(root/name).resolve()
        if root not in p.parents:continue
        f.save(p);made.append({'path':name,'name':name,'size':p.stat().st_size,'url':'/api/library/file/'+name})
    return jsonify(files=made)

@app.delete('/api/library/<path:rel>')
@auth
def library_delete(rel):
    root=(LIBRARY/str(session['uid'])).resolve()
    p=(root/rel).resolve()
    if root not in p.parents or not p.is_file():return jsonify(error='Файл не найден'),404
    p.unlink();return jsonify(ok=True)

@app.get('/api/library/file/<path:rel>')
@auth
def library_file(rel):
    root=(LIBRARY/str(session['uid'])).resolve()
    p=(root/rel).resolve()
    if root not in p.parents or not p.is_file():return jsonify(error='Файл не найден'),404
    return send_file(p,as_attachment=True,download_name=p.name)

@app.get('/api/chats')
@auth
def chats():
 with db() as c:
  empty=c.execute("SELECT id FROM chats WHERE user_id=? AND (messages='[]' OR messages='' OR messages IS NULL)",(session['uid'],)).fetchall()
  for r in empty:
   cid=r['id'];c.execute('DELETE FROM work_events WHERE chat_id=? AND user_id=?',(cid,session['uid']));c.execute('DELETE FROM work_runs WHERE chat_id=? AND user_id=?',(cid,session['uid']))
   import shutil;shutil.rmtree(WORK/'chats'/cid,ignore_errors=True);shutil.rmtree(UPLOADS/cid,ignore_errors=True)
  r=c.execute('SELECT id,title,provider_id,archived,updated_at FROM chats WHERE user_id=? ORDER BY archived ASC,updated_at DESC',(session['uid'],)).fetchall()
 return jsonify([dict(x) for x in r])
@app.post('/api/chats')
@auth
def new_chat():
 d=request.json or {};cid=str(uuid.uuid4());title=d.get('title','Новый чат')
 with db() as c:c.execute('INSERT INTO chats(id,user_id,title,messages,provider_id) VALUES(?,?,?,?,?)',(cid,session['uid'],title,'[]',d.get('provider_id')))
 return jsonify(id=cid,title=title)
@app.patch('/api/chats/<cid>')
@auth
def edit_chat(cid):
    d=request.json or {}
    title=d.get('title')
    archived=d.get('archived')
    with db() as c:
        r=c.execute('SELECT id FROM chats WHERE id=? AND user_id=?',(cid,session['uid'])).fetchone()
        if not r:return jsonify(error='not_found'),404
        if title is not None:
            title=str(title).strip()[:120]
            if not title:return jsonify(error='Название не может быть пустым'),400
            c.execute('UPDATE chats SET title=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?',(title,cid,session['uid']))
        if archived is not None:
            c.execute('UPDATE chats SET archived=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?',(1 if bool(archived) else 0,cid,session['uid']))
    return jsonify(ok=True)

@app.delete('/api/chats/<cid>')
@auth
def delete_chat(cid):
    with db() as c:
        r=c.execute('SELECT id FROM chats WHERE id=? AND user_id=?',(cid,session['uid'])).fetchone()
        if not r:return jsonify(error='not_found'),404
        c.execute('DELETE FROM work_events WHERE chat_id=? AND user_id=?',(cid,session['uid']))
        c.execute('DELETE FROM work_runs WHERE chat_id=? AND user_id=?',(cid,session['uid']))
        c.execute('DELETE FROM chats WHERE id=? AND user_id=?',(cid,session['uid']))
    import shutil
    shutil.rmtree(WORK/'chats'/cid,ignore_errors=True);shutil.rmtree(UPLOADS/cid,ignore_errors=True)
    return jsonify(ok=True)

@app.get('/api/chats/<cid>')
@auth
def get_chat(cid):
 with db() as c:r=c.execute('SELECT * FROM chats WHERE id=? AND user_id=?',(cid,session['uid'])).fetchone()
 if not r:return jsonify(error='not_found'),404
 x=dict(r);x['messages']=json.loads(x['messages']);return jsonify(x)
def save_chat(cid,items,title,pid):
 with db() as c:
  r=c.execute('SELECT * FROM chats WHERE id=? AND user_id=?',(cid,session['uid'])).fetchone();m=json.loads(r['messages']);m.extend(items);c.execute('UPDATE chats SET title=?,messages=?,provider_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(title,json.dumps(m,ensure_ascii=False),pid,cid))

@app.post('/api/chats/<cid>/message')
@auth
def message(cid):
    with db() as c:
        chat=c.execute('SELECT * FROM chats WHERE id=? AND user_id=?',(cid,session['uid'])).fetchone()
    if not chat:return jsonify(error='not_found'),404
    content=request.form.get('content','').strip()
    pid=request.form.get('provider_id') or chat['provider_id']
    p=provider(int(pid)) if pid else None
    if not content:return jsonify(error='Пустое сообщение'),400
    if not p:return jsonify(error='Провайдер не выбран'),400
    uploads=request.files.getlist('files')
    source=[]
    if uploads:
        dest=UPLOADS/cid
        try:source,_,_=ingest_uploads(uploads,dest)
        except Exception as e:return jsonify(error=str(e)),400
    history=json.loads(chat['messages'])
    messages=[{'role':'system','content':'Ты полезный ассистент. Отвечай по существу и используй доступные материалы. Общая библиотека файлов доступна между чатами. Используй её только когда это нужно.\\n\\nОБЩАЯ БИБЛИОТЕКА:\\n'+library_text()}]
    messages += [{'role':m['role'],'content':m.get('content','')} for m in history[-20:] if m.get('role') in ('user','assistant')]
    if source:messages.append({'role':'user','content':source_context(source)})
    messages.append({'role':'user','content':content})
    try:answer=llm(p,messages)
    except Exception as e:return jsonify(error=f'LLM: {e}'),502
    title=chat['title']
    if title=='Новый чат':title=content[:48] or title
    save_chat(cid,[{'role':'user','content':content},{'role':'assistant','content':answer}],title,int(pid))
    return jsonify(answer=answer,title=title,files=[])

import os,json,uuid,sqlite3,subprocess
from pathlib import Path
from flask import Flask,request,jsonify,session,send_from_directory,send_file
from werkzeug.security import generate_password_hash,check_password_hash
from dotenv import load_dotenv
from workmode import ingest_uploads,source_context,parse_plan
from provider_service import ProviderConfigError, auth_headers, candidate_base_urls, discover_models, normalize_base_url, endpoint
from media_service import is_image_path, multimodal_content, generate_image
load_dotenv()
ROOT=Path(__file__).resolve().parent;DATA=ROOT/'data';DATA.mkdir(exist_ok=True);WORK=ROOT/'workspace';WORK.mkdir(exist_ok=True);UPLOADS=WORK/'uploads';UPLOADS.mkdir(exist_ok=True);LIBRARY=WORK/'library';LIBRARY.mkdir(exist_ok=True);DB=DATA/'inhchat.db';USERS=ROOT/'users.json';PORT=int(os.getenv('PORT','6767'));DEEPSEEK=os.getenv('DEEPSEEK_BASE_URL','http://127.0.0.1:9655/v1')
app=Flask(__name__,static_folder='static',static_url_path='/static');app.secret_key=os.getenv('APP_SECRET_KEY','change-me-in-.env');app.config['MAX_CONTENT_LENGTH']=32*1024*1024

def db():c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;return c
with db() as c:c.executescript('CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,created_at DATETIME DEFAULT CURRENT_TIMESTAMP);CREATE TABLE IF NOT EXISTS providers(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,name TEXT NOT NULL,base_url TEXT NOT NULL,api_key TEXT DEFAULT "",model TEXT NOT NULL,kind TEXT DEFAULT "openai",created_at DATETIME DEFAULT CURRENT_TIMESTAMP,UNIQUE(user_id,name));CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,title TEXT NOT NULL,messages TEXT NOT NULL,provider_id INTEGER,archived INTEGER DEFAULT 0,meta_analysis INTEGER DEFAULT 0,temporary INTEGER DEFAULT 0,image_generation_enabled INTEGER DEFAULT 0,image_provider_id INTEGER,created_at DATETIME DEFAULT CURRENT_TIMESTAMP,updated_at DATETIME DEFAULT CURRENT_TIMESTAMP);CREATE TABLE IF NOT EXISTS work_runs(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,chat_id TEXT NOT NULL,request TEXT NOT NULL,source_files TEXT NOT NULL,plan TEXT NOT NULL,results TEXT NOT NULL,final_answer TEXT DEFAULT "",created_at DATETIME DEFAULT CURRENT_TIMESTAMP);')

with db() as c:
    cols={r['name'] for r in c.execute('PRAGMA table_info(chats)').fetchall()}
    if 'archived' not in cols: c.execute('ALTER TABLE chats ADD COLUMN archived INTEGER DEFAULT 0')
    if 'meta_analysis' not in cols: c.execute('ALTER TABLE chats ADD COLUMN meta_analysis INTEGER DEFAULT 0')
    if 'temporary' not in cols: c.execute('ALTER TABLE chats ADD COLUMN temporary INTEGER DEFAULT 0')
    if 'image_generation_enabled' not in cols: c.execute('ALTER TABLE chats ADD COLUMN image_generation_enabled INTEGER DEFAULT 0')
    if 'image_provider_id' not in cols: c.execute('ALTER TABLE chats ADD COLUMN image_provider_id INTEGER)

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
 h={'Content-Type':'application/json',**auth_headers(p['api_key'])}
 last_error=None
 for base in candidate_base_urls(p['base_url']):
  try:
   r=requests.post(endpoint(base,'chat/completions'),json={'model':p['model'],'messages':messages,'temperature':0.3},headers=h,timeout=300)
   if r.status_code == 404 and base != candidate_base_urls(p['base_url'])[-1]:
    last_error=r
    continue
   r.raise_for_status()
   data=r.json()
   choices=data.get('choices') or []
   if not choices: raise RuntimeError('API не вернул choices')
   content=choices[0].get('message',{}).get('content','')
   if isinstance(content,list):
    return '\n'.join(str(x.get('text','')) for x in content if isinstance(x,dict) and x.get('text'))
   return str(content or '')
  except requests.HTTPError as exc:
   last_error=exc
   raise
 if last_error: raise last_error
 raise RuntimeError('Не удалось обратиться к API')
def meta_chat_context(current_cid):
    with db() as c:
        rows=c.execute('SELECT title,messages FROM chats WHERE user_id=? AND id<>? AND temporary=0 ORDER BY updated_at DESC LIMIT 20',(session['uid'],current_cid)).fetchall()
    chunks=[]
    for r in rows:
        try:
            ms=json.loads(r['messages']);txt='\n'.join(str(m.get('content','')) for m in ms[-6:] if m.get('role') in ('user','assistant'))
            if txt:chunks.append('CHAT: '+r['title']+'\n'+txt[:12000])
        except Exception:pass
    return '\n\n'.join(chunks)[:250000] if chunks else '(Другие чаты недоступны или пусты)'

def library_text():
    root=(LIBRARY/str(session['uid'])).resolve();root.mkdir(parents=True,exist_ok=True);out=[]
    for p in root.rglob('*'):
        if p.is_file() and p.suffix.lower() in {'.txt','.md','.markdown','.json','.yaml','.yml','.csv','.tsv','.py','.js','.ts','.html','.css','.xml','.sql'}:
            try:out.append({'path':str(p.relative_to(root)),'text':p.read_text(encoding='utf-8',errors='replace')[:120000]})
            except OSError:pass
    return source_context(out)[:800000] if out else '(Библиотека пуста)'

def files_out(paths,cid=None):
 out=[]
 for rel in paths:
  rel=str(rel)
  if cid and not rel.startswith('uploads/'):
   p=(WORK/'chats'/cid/rel).resolve()
   if (WORK/'chats'/cid).resolve() not in p.parents:continue
   url='/api/files/'+cid+'/'+rel
  else:
   p=(WORK/rel).resolve()
   if WORK not in p.parents:continue
   if cid and rel.startswith('uploads/'+cid+'/'):
    url='/api/uploads/'+cid+'/'+rel.split('uploads/'+cid+'/',1)[1]
   else:
    url='/api/files/'+rel
  if p.is_file():
   mime='image/'+p.suffix.lower().lstrip('.') if p.suffix.lower() in {'.png','.jpg','.jpeg','.webp','.gif'} else ''
   out.append({'name':p.name,'path':rel,'size':p.stat().st_size,'mime':mime,'url':url})
 return out
@app.get('/')
def index():return send_from_directory('static','index.html')
@app.get('/api/me')
@app.get('/api/uploads/<cid>/<path:rel>')
@auth
def uploaded_file(cid,rel):
    with db() as c:
        if not c.execute('SELECT id FROM chats WHERE id=? AND user_id=?',(cid,session['uid'])).fetchone():return jsonify(error='not_found'),404
    root=(UPLOADS/cid).resolve()
    p=(root/rel).resolve()
    if root not in p.parents or not p.is_file():return jsonify(error='Файл не найден'),404
    return send_file(p,as_attachment=True,download_name=p.name)

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
    p=provider(pid)
    if not p:return jsonify(error='Провайдер не найден'),404
    try:
        result=discover_models(p['base_url'],p['api_key'])
        return jsonify(models=result.models,base_url=result.base_url)
    except ProviderConfigError as e:
        return jsonify(error=f'Не удалось получить модели: {e}'),502


@app.post('/api/model-discovery')
@auth
def model_discovery():
    d=request.json or {}
    try:
        result=discover_models(str(d.get('base_url','')),str(d.get('api_key','')))
        return jsonify(models=result.models,base_url=result.base_url)
    except ProviderConfigError as e:
        message=str(e) if str(e) else 'Не удалось получить модели'
        status=400 if message.startswith(('Укажите Base URL','Base URL должен')) else 502
        return jsonify(error=message),status


@app.post('/api/providers')
@auth
def add_provider():
 d=request.json or {}
 name=str(d.get('name','')).strip()
 try:base=normalize_base_url(d.get('base_url',''))
 except ProviderConfigError as e:return jsonify(error=str(e)),400
 model=str(d.get('model','')).strip()
 kind=str(d.get('kind','text')).strip().lower()
 if kind=='openai':kind='text'
 if kind not in {'text','multimodal','image'}:return jsonify(error='Неизвестный тип модели'),400
 if not name or not model:return jsonify(error='Заполните название, Base URL и модель'),400
 with db() as c:
  try:r=c.execute('INSERT INTO providers(user_id,name,base_url,api_key,model,kind) VALUES(?,?,?,?,?,?)',(session['uid'],name,base,str(d.get('api_key','')).strip(),model,kind))
  except sqlite3.IntegrityError:return jsonify(error='Провайдер с таким именем уже существует'),409
 return jsonify(id=r.lastrowid,name=name)


@app.patch('/api/providers/<int:pid>')
@auth
def edit_provider(pid):
    d=request.json or {}
    name=str(d.get('name','')).strip()
    try:base=normalize_base_url(d.get('base_url',''))
    except ProviderConfigError as e:return jsonify(error=str(e)),400
    model=str(d.get('model','')).strip()
    kind=str(d.get('kind','text')).strip().lower()
    if kind=='openai':kind='text'
    if kind not in {'text','multimodal','image'}:return jsonify(error='Неизвестный тип модели'),400
    if not name or not model:return jsonify(error='Заполните название, Base URL и модель'),400
    with db() as c:
        old=c.execute('SELECT api_key FROM providers WHERE id=? AND user_id=?',(pid,session['uid'])).fetchone()
        if not old:return jsonify(error='Провайдер не найден'),404
        key=str(d.get('api_key','')).strip() or old['api_key']
        try:
            c.execute('UPDATE providers SET name=?,base_url=?,api_key=?,model=?,kind=? WHERE id=? AND user_id=?',(name,base,key,model,kind,pid,session['uid']))
        except sqlite3.IntegrityError:return jsonify(error='Провайдер с таким именем уже существует'),409
    return jsonify(ok=True)


@app.delete('/api/providers/<int:pid>')
@auth
def del_provider(pid):
 with db() as c:
  c.execute('UPDATE chats SET image_generation_enabled=0,image_provider_id=NULL WHERE image_provider_id=? AND user_id=?',(pid,session['uid']))
  c.execute('DELETE FROM providers WHERE id=? AND user_id=?',(pid,session['uid']))
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

@app.post('/api/chats/cleanup-temporary')
@auth
def cleanup_temporary():
    with db() as c:
        rows=c.execute('SELECT id FROM chats WHERE user_id=? AND temporary=1',(session['uid'],)).fetchall()
        for r in rows:
            cid=r['id'];c.execute('DELETE FROM work_events WHERE chat_id=? AND user_id=?',(cid,session['uid']));c.execute('DELETE FROM work_runs WHERE chat_id=? AND user_id=?',(cid,session['uid']));c.execute('DELETE FROM chats WHERE id=? AND user_id=?',(cid,session['uid']))
    import shutil
    for r in rows:
        shutil.rmtree(WORK/'chats'/r['id'],ignore_errors=True);shutil.rmtree(UPLOADS/r['id'],ignore_errors=True)
    return jsonify(ok=True,deleted=len(rows))

@app.get('/api/chats')
@auth
def chats():
 with db() as c:
  empty=c.execute("SELECT id FROM chats WHERE user_id=? AND (messages='[]' OR messages='' OR messages IS NULL)",(session['uid'],)).fetchall()
  for r in empty:
   cid=r['id'];c.execute('DELETE FROM work_events WHERE chat_id=? AND user_id=?',(cid,session['uid']));c.execute('DELETE FROM work_runs WHERE chat_id=? AND user_id=?',(cid,session['uid']))
   import shutil;shutil.rmtree(WORK/'chats'/cid,ignore_errors=True);shutil.rmtree(UPLOADS/cid,ignore_errors=True)
  r=c.execute('SELECT id,title,provider_id,archived,meta_analysis,temporary,updated_at FROM chats WHERE user_id=? AND temporary=0 ORDER BY archived ASC,updated_at DESC',(session['uid'],)).fetchall()
 return jsonify([dict(x) for x in r])
@app.post('/api/chats')
@auth
def new_chat():
 d=request.json or {};cid=str(uuid.uuid4());title=d.get('title','Новый чат');temporary=1 if bool(d.get('temporary')) else 0;meta=1 if bool(d.get('meta_analysis')) else 0;image_enabled=1 if bool(d.get('image_generation_enabled')) else 0;image_provider_id=d.get('image_provider_id')
 with db() as c:c.execute('INSERT INTO chats(id,user_id,title,messages,provider_id,meta_analysis,temporary,image_generation_enabled,image_provider_id) VALUES(?,?,?,?,?,?,?,?,?)',(cid,session['uid'],title,'[]',d.get('provider_id'),meta,temporary,image_enabled,image_provider_id))
 return jsonify(id=cid,title=title)
@app.patch('/api/chats/<cid>')
@auth
def edit_chat(cid):
    d=request.json or {}
    title=d.get('title')
    archived=d.get('archived');meta=d.get('meta_analysis');temporary=d.get('temporary');image_enabled=d.get('image_generation_enabled');image_provider_id=d.get('image_provider_id')
    with db() as c:
        r=c.execute('SELECT id FROM chats WHERE id=? AND user_id=?',(cid,session['uid'])).fetchone()
        if not r:return jsonify(error='not_found'),404
        if title is not None:
            title=str(title).strip()[:120]
            if not title:return jsonify(error='Название не может быть пустым'),400
            c.execute('UPDATE chats SET title=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?',(title,cid,session['uid']))
        if archived is not None:
            c.execute('UPDATE chats SET archived=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?',(1 if bool(archived) else 0,cid,session['uid']))
        if meta is not None:c.execute('UPDATE chats SET meta_analysis=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?',(1 if bool(meta) else 0,cid,session['uid']))
        if temporary is not None:c.execute('UPDATE chats SET temporary=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?',(1 if bool(temporary) else 0,cid,session['uid']))
        if image_enabled is not None:c.execute('UPDATE chats SET image_generation_enabled=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?',(1 if bool(image_enabled) else 0,cid,session['uid']))
        if image_provider_id is not None:
            ip=c.execute('SELECT id FROM providers WHERE id=? AND user_id=? AND kind=?',(int(image_provider_id),session['uid'],'image')).fetchone()
            if not ip:return jsonify(error='Провайдер генерации изображений не найден'),400
            c.execute('UPDATE chats SET image_provider_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?',(int(image_provider_id),cid,session['uid']))
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
    if p['kind']=='image':return jsonify(error='Выбран провайдер генерации изображений, а не чат-модель'),400
    uploads=request.files.getlist('files')
    source=[];image_paths=[];uploaded_files=[]
    if uploads:
        dest=UPLOADS/cid
        try:
            source,_,_=ingest_uploads(uploads,dest)
            for f in uploads:
                name=Path(f.filename or '').name
                if name:
                    saved=(dest/name).resolve()
                    if saved.is_file() and is_image_path(saved):image_paths.append(saved)
                    if saved.is_file():uploaded_files.append(str(saved.relative_to(WORK)))
        except Exception as e:return jsonify(error=str(e)),400
    if image_paths and p['kind']!='multimodal':
        return jsonify(error='Для отправки изображений выберите мультимодальную модель в настройках провайдера'),400
    history=json.loads(chat['messages'])
    messages=[{'role':'system','content':'Ты полезный ассистент. Отвечай по существу и используй доступные материалы. Общая библиотека файлов доступна между чатами. Используй её только когда это нужно.\n\nОБЩАЯ БИБЛИОТЕКА:\n'+library_text()}]
    messages += [{'role':m['role'],'content':m.get('content','')} for m in history[-20:] if m.get('role') in ('user','assistant')]
    if chat['meta_analysis']:messages.append({'role':'system','content':'МЕТААНАЛИЗ ВКЛЮЧЕН. Контекст других чатов пользователя:\n\n'+meta_chat_context(cid)})
    if source:messages.append({'role':'user','content':source_context(source)})
    messages.append({'role':'user','content':multimodal_content(content,image_paths) if image_paths else content})
    try:answer=llm(p,messages)
    except Exception as e:return jsonify(error=f'LLM: {e}'),502
    title=chat['title']
    if title=='Новый чат':title=content[:48] or title
    user_files=files_out(uploaded_files,cid)
    save_chat(cid,[{'role':'user','content':content,'files':user_files},{'role':'assistant','content':answer,'files':[]}],title,int(pid))
    return jsonify(answer=answer,title=title,files=[])

@app.post('/api/chats/<cid>/generate-image')
@auth
def chat_generate_image(cid):
    with db() as c:
        chat=c.execute('SELECT * FROM chats WHERE id=? AND user_id=?',(cid,session['uid'])).fetchone()
    if not chat:return jsonify(error='not_found'),404
    image_pid=chat['image_provider_id']
    if not chat['image_generation_enabled'] or not image_pid:return jsonify(error='Генерация изображений выключена для этого чата'),400
    image_provider=provider(int(image_pid))
    if not image_provider or image_provider['kind']!='image':return jsonify(error='Провайдер изображений не найден'),400
    text=str(request.json.get('prompt','')).strip() if request.is_json else ''
    if not text:return jsonify(error='Укажите, что должно быть изображено'),400
    chat_pid=chat['provider_id']
    chat_provider=provider(int(chat_pid)) if chat_pid else None
    if not chat_provider or chat_provider['kind']=='image':return jsonify(error='Для подготовки промпта нужна текстовая или мультимодальная модель чата'),400
    try:
        prompt=llm(chat_provider,[{'role':'system','content':'Ты режиссёр промптов для генерации изображений. Преврати запрос пользователя в один точный промпт для image generation. Не добавляй пояснений, кавычек, списков и мета-комментариев. Сохраняй намерение пользователя и при необходимости уточняй композицию, стиль, свет, камеру и формат.'},{'role':'user','content':text}]).strip()
        out=generate_image(image_provider,prompt,WORK/'chats'/cid/'generated',prefix='image')
        rels=[str(x.relative_to(WORK/'chats'/cid)) for x in out]
        files=files_out(rels,cid)
        save_chat(cid,[{'role':'user','content':text},{'role':'assistant','content':'Сгенерировано изображение.\n\n[[ATTACH: '+rels[0]+']]','files':files}],chat['title'],chat['provider_id'])
        return jsonify(prompt=prompt,files=files)
    except Exception as e:return jsonify(error=f'Генерация изображения: {e}'),502

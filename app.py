import os, json, uuid, math, subprocess, threading
from pathlib import Path
from functools import wraps
from flask import Flask, request, jsonify, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3, requests
from dotenv import load_dotenv

load_dotenv()
ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'; DATA.mkdir(exist_ok=True)
WORKSPACE = ROOT / 'workspace'; WORKSPACE.mkdir(exist_ok=True)
DB = DATA / 'inhchat.db'
PORT = int(os.getenv('PORT', '698'))
APP_SECRET = os.getenv('APP_SECRET_KEY', 'change-me-in-.env')
DEEPSEEK_URL = os.getenv('DEEPSEEK_BASE_URL', 'http://127.0.0.1:9655/v1')

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = APP_SECRET
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024


def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c

with db() as c:
    c.executescript('''
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS providers(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, base_url TEXT NOT NULL, api_key TEXT DEFAULT '', model TEXT NOT NULL, kind TEXT DEFAULT 'openai', created_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,name));
    CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL, messages TEXT NOT NULL, provider_id INTEGER, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP);
    ''')


def login_required(fn):
    @wraps(fn)
    def w(*a, **kw):
        if 'user_id' not in session: return jsonify({'error':'auth_required'}), 401
        return fn(*a, **kw)
    return w


def current_user():
    if 'user_id' not in session: return None
    with db() as c: return c.execute('SELECT id,email FROM users WHERE id=?',(session['user_id'],)).fetchone()


def provider_row(pid):
    with db() as c: return c.execute('SELECT * FROM providers WHERE id=? AND user_id=?',(pid,session['user_id'])).fetchone()


def call_llm(p, messages, tools=None, stream=False):
    headers={'Content-Type':'application/json'}
    if p['api_key']: headers['Authorization']='Bearer '+p['api_key']
    body={'model':p['model'],'messages':messages,'temperature':0.7,'stream':False}
    if tools: body['tools']=tools; body['tool_choice']='auto'
    r=requests.post(p['base_url'].rstrip('/')+'/chat/completions',json=body,headers=headers,timeout=180)
    r.raise_for_status(); return r.json()

TOOLS=[
 {'type':'function','function':{'name':'calculator','description':'Evaluate a mathematical expression using safe math operations.','parameters':{'type':'object','properties':{'expression':{'type':'string'}},'required':['expression']}}},
 {'type':'function','function':{'name':'list_workspace','description':'List files in the inhCHAT workspace.','parameters':{'type':'object','properties':{}}}},
 {'type':'function','function':{'name':'read_file','description':'Read a UTF-8 text file inside the inhCHAT workspace.','parameters':{'type':'object','properties':{'path':{'type':'string'}},'required':['path']}}},
 {'type':'function','function':{'name':'write_file','description':'Write a UTF-8 text file inside the inhCHAT workspace.','parameters':{'type':'object','properties':{'path':{'type':'string'},'content':{'type':'string'}},'required':['path','content']}}},
]


def safe_path(rel):
    p=(WORKSPACE / rel).resolve()
    if WORKSPACE not in p.parents and p != WORKSPACE: raise ValueError('Path is outside workspace')
    return p


def run_tool(name,args):
    if name=='calculator':
        allowed={k:getattr(math,k) for k in dir(math) if not k.startswith('_')}; allowed.update({'abs':abs,'round':round,'min':min,'max':max})
        return str(eval(args['expression'],{'__builtins__':{}},allowed))
    if name=='list_workspace':
        return json.dumps([str(p.relative_to(WORKSPACE)) for p in WORKSPACE.rglob('*') if p.is_file()],ensure_ascii=False)
    if name=='read_file':
        return safe_path(args['path']).read_text(encoding='utf-8')[:100000]
    if name=='write_file':
        p=safe_path(args['path']); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(args['content'],encoding='utf-8'); return 'written: '+str(p.relative_to(WORKSPACE))
    raise ValueError('Unknown tool')


def autonomous(p, messages, max_steps=12):
    work=list(messages); trace=[]
    system={'role':'system','content':'You are inhCHAT autonomous Work Mode. Complete the user task independently. Plan internally, use tools when useful, inspect results, iterate, and finish with a concise report. You may create artifacts only inside the provided workspace. Never claim a tool action happened unless its result confirms it.'}
    if not work or work[0].get('role')!='system': work.insert(0,system)
    for step in range(max_steps):
        data=call_llm(p,work,TOOLS)
        msg=data['choices'][0]['message']; work.append(msg)
        calls=msg.get('tool_calls') or []
        if not calls: return msg.get('content',''), trace
        for tc in calls:
            try:
                args=json.loads(tc['function'].get('arguments') or '{}'); result=run_tool(tc['function']['name'],args)
            except Exception as e: result='ERROR: '+str(e)
            trace.append({'step':step+1,'tool':tc['function']['name'],'result':result[:4000]})
            work.append({'role':'tool','tool_call_id':tc['id'],'content':result})
    return 'Автономный режим остановлен после достижения лимита шагов.', trace


@app.get('/')
def index(): return send_from_directory('static','index.html')

@app.get('/api/me')
def me():
    u=current_user(); return jsonify({'user':dict(u) if u else None})

@app.post('/api/auth/register')
def register():
    d=request.json or {}; email=d.get('email','').strip().lower(); pw=d.get('password','')
    if len(email)<3 or len(pw)<8: return jsonify({'error':'Нужен корректный email и пароль минимум 8 символов'}),400
    try:
        with db() as c: cur=c.execute('INSERT INTO users(email,password_hash) VALUES(?,?)',(email,generate_password_hash(pw))); uid=cur.lastrowid
    except sqlite3.IntegrityError: return jsonify({'error':'Аккаунт уже существует'}),409
    session['user_id']=uid; return jsonify({'ok':True,'user':{'id':uid,'email':email}})

@app.post('/api/auth/login')
def login():
    d=request.json or {}
    with db() as c: u=c.execute('SELECT * FROM users WHERE email=?',(d.get('email','').strip().lower(),)).fetchone()
    if not u or not check_password_hash(u['password_hash'],d.get('password','')): return jsonify({'error':'Неверный email или пароль'}),401
    session['user_id']=u['id']; return jsonify({'ok':True,'user':{'id':u['id'],'email':u['email']}})

@app.post('/api/auth/logout')
def logout(): session.clear(); return jsonify({'ok':True})

@app.get('/api/providers')
@login_required
def providers():
    with db() as c: rows=c.execute('SELECT id,name,base_url,model,kind FROM providers WHERE user_id=? ORDER BY id',(session['user_id'],)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.post('/api/providers')
@login_required
def add_provider():
    d=request.json or {}; name=d.get('name','').strip(); base=d.get('base_url','').strip(); model=d.get('model','').strip(); key=d.get('api_key','')
    if not name or not base or not model: return jsonify({'error':'name, base_url и model обязательны'}),400
    with db() as c:
        try: cur=c.execute('INSERT INTO providers(user_id,name,base_url,api_key,model,kind) VALUES(?,?,?,?,?,?)',(session['user_id'],name,base,key,model,d.get('kind','openai'))); pid=cur.lastrowid
        except sqlite3.IntegrityError: return jsonify({'error':'Провайдер с таким именем уже существует'}),409
    return jsonify({'id':pid,'name':name})

@app.delete('/api/providers/<int:pid>')
@login_required
def delete_provider(pid):
    with db() as c: c.execute('DELETE FROM providers WHERE id=? AND user_id=?',(pid,session['user_id']))
    return jsonify({'ok':True})

@app.post('/api/deepseek/auth')
def deepseek_auth():
    vendor=ROOT/'.vendor'/'FreeDeepseekAPI'
    if not vendor.exists(): return jsonify({'error':'FreeDeepseekAPI не установлен. Запустите ./setup.sh'}),503
    log=open(DATA/'deepseek-auth.log','a')
    subprocess.Popen(['npm','run','auth'],cwd=vendor,stdout=log,stderr=log,start_new_session=True)
    return jsonify({'ok':True,'message':'Открывается окно Chrome для входа в DeepSeek. Завершите вход там, затем вернитесь в inhCHAT.'})

@app.get('/api/deepseek/status')
def deepseek_status():
    try:
        r=requests.get(DEEPSEEK_URL.rstrip('/')+'/models',timeout=3); return jsonify({'ok':r.ok,'models':r.json().get('data',[])})
    except Exception as e: return jsonify({'ok':False,'error':str(e)})

@app.get('/api/chats')
@login_required
def chats():
    with db() as c: rows=c.execute('SELECT id,title,provider_id,updated_at FROM chats WHERE user_id=? ORDER BY updated_at DESC',(session['user_id'],)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.post('/api/chats')
@login_required
def new_chat():
    cid=str(uuid.uuid4()); d=request.json or {}; title=d.get('title','Новый чат')
    with db() as c: c.execute('INSERT INTO chats(id,user_id,title,messages,provider_id) VALUES(?,?,?,?,?)',(cid,session['user_id'],title,'[]',d.get('provider_id')))
    return jsonify({'id':cid,'title':title})

@app.get('/api/chats/<cid>')
@login_required
def get_chat(cid):
    with db() as c: r=c.execute('SELECT * FROM chats WHERE id=? AND user_id=?',(cid,session['user_id'])).fetchone()
    if not r:return jsonify({'error':'not_found'}),404
    x=dict(r); x['messages']=json.loads(x['messages']); return jsonify(x)

@app.post('/api/chats/<cid>/message')
@login_required
def message(cid):
    d=request.json or {}; text=d.get('content','').strip(); pid=d.get('provider_id'); mode=d.get('mode','chat')
    if not text:return jsonify({'error':'Пустое сообщение'}),400
    with db() as c: row=c.execute('SELECT * FROM chats WHERE id=? AND user_id=?',(cid,session['user_id'])).fetchone()
    if not row:return jsonify({'error':'not_found'}),404
    pid=pid or row['provider_id']
    p=provider_row(pid) if pid else None
    if not p: return jsonify({'error':'Выберите провайдера в настройках'}),400
    msgs=json.loads(row['messages']); msgs.append({'role':'user','content':text})
    try:
        if mode=='work': answer,trace=autonomous(p,msgs)
        else:
            data=call_llm(p,msgs); answer=data['choices'][0]['message'].get('content',''); trace=[]
    except requests.HTTPError as e: return jsonify({'error':f'LLM API error: {e.response.text[:1000]}'}),502
    except Exception as e: return jsonify({'error':str(e)}),500
    msgs.append({'role':'assistant','content':answer})
    title=row['title']
    if title=='Новый чат': title=text[:48]
    with db() as c: c.execute('UPDATE chats SET title=?,messages=?,provider_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?',(title,json.dumps(msgs,ensure_ascii=False),p['id'],cid,session['user_id']))
    return jsonify({'answer':answer,'trace':trace,'title':title})

@app.get('/api/workspace')
@login_required
def workspace(): return jsonify([str(p.relative_to(WORKSPACE)) for p in WORKSPACE.rglob('*') if p.is_file()])

if __name__=='__main__': app.run(host='127.0.0.1',port=PORT,debug=False)

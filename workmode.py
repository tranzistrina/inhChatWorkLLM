import json, re, shutil, zipfile
from pathlib import Path

TEXT_EXT={'.txt','.md','.markdown','.rst','.py','.js','.ts','.tsx','.jsx','.json','.yaml','.yml','.toml','.ini','.cfg','.conf','.env','.log','.csv','.tsv','.html','.htm','.css','.scss','.xml','.sql','.sh','.bash','.zsh','.bat','.ps1','.java','.kt','.kts','.c','.h','.cpp','.hpp','.cs','.go','.rs','.rb','.php','.swift','.vue','.svelte','.tex'}
MAX_UPLOAD=24*1024*1024
MAX_FILES=200
MAX_FILE_TEXT=150000
MAX_CONTEXT=2000000

def safe_child(root, rel):
    p=(root/rel).resolve()
    if p!=root and root not in p.parents: raise ValueError('Небезопасный путь')
    return p

def text_from(path):
    if path.suffix.lower() not in TEXT_EXT:return None
    raw=path.read_bytes()[:MAX_FILE_TEXT*4]
    if b'\0' in raw[:4096]:return None
    return raw.decode('utf-8',errors='replace')[:MAX_FILE_TEXT]

def ingest_uploads(files, dest):
    dest.mkdir(parents=True,exist_ok=True); extracted=[]; uploaded=0; total=0
    for f in files:
        if not f or not f.filename: continue
        name=Path(f.filename.replace('\\','/')).name
        if not name: continue
        data=f.read(MAX_UPLOAD+1)
        if len(data)>MAX_UPLOAD: raise ValueError('Файл больше лимита 24 MB')
        out=safe_child(dest,name);out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(data);uploaded+=1;total+=len(data)
        if out.suffix.lower()=='.zip':
            target=dest/(out.stem+'_extracted');target.mkdir(exist_ok=True)
            with zipfile.ZipFile(out) as z:
                infos=[i for i in z.infolist() if not i.is_dir()]
                if len(infos)>MAX_FILES: raise ValueError(f'В архиве больше {MAX_FILES} файлов')
                for info in infos:
                    q=safe_child(target,info.filename)
                    if info.file_size>MAX_FILE_TEXT*2: continue
                    q.parent.mkdir(parents=True,exist_ok=True)
                    with z.open(info) as src,q.open('wb') as dst: shutil.copyfileobj(src,dst,65536)
                    txt=text_from(q)
                    if txt is not None: extracted.append({'path':str(q.relative_to(dest)),'text':txt})
        else:
            txt=text_from(out)
            if txt is not None: extracted.append({'path':name,'text':txt})
    return extracted,uploaded,total

def source_context(source):
    return '\n\n'.join(f'FILE: {x["path"]}\n{x["text"]}' for x in source)[:MAX_CONTEXT]

def parse_plan(text):
    m=re.search(r'\[[\s\S]*\]',text or '')
    if m:
        try:
            arr=json.loads(m.group(0))
            if isinstance(arr,list):
                return [{'title':str(x.get('title',x.get('task','Задача'))),'description':str(x.get('description','')),'status':'pending'} for x in arr if isinstance(x,dict)][:12]
        except Exception: pass
    return [{'title':line.strip(),'description':'Выполнить задачу и зафиксировать результат','status':'pending'} for line in (text or '').splitlines() if line.strip()][:12] or [{'title':'Выполнить запрос пользователя','description':'Подготовить результат','status':'pending'}]

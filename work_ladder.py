import json

TIERS=("router","multimodal","smart","medium","weak")
TEXT_TIERS=("smart","medium","weak")
MAX_FALLBACKS=5

def init_db(db):
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS work_ladder_settings (user_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, router_provider_id INTEGER, multimodal_provider_id INTEGER, smart_provider_id INTEGER, medium_provider_id INTEGER, weak_provider_id INTEGER, fallback_provider_ids TEXT NOT NULL DEFAULT '[]', updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)")

def _provider_row(db,user_id,pid):
    if pid in (None,"",0,"0"): return None
    try: pid=int(pid)
    except (TypeError,ValueError): raise ValueError("Некорректный ID модели")
    with db() as c: row=c.execute("SELECT * FROM providers WHERE id=? AND user_id=?",(pid,user_id)).fetchone()
    if not row: raise ValueError("Выбранная модель не найдена среди подготовленных провайдеров")
    return row

def _validate_role(db,user_id,pid,role):
    row=_provider_row(db,user_id,pid)
    if not row: return None
    kind=str(row["kind"] or "text").lower()
    if role=="multimodal" and kind!="multimodal": raise ValueError("Для мультимодальной роли нужна мультимодальная модель")
    if role!="multimodal" and kind=="image": raise ValueError("Image-модель нельзя использовать как текстовую")
    return row

def get_settings(db,user_id):
    init_db(db)
    with db() as c: row=c.execute("SELECT * FROM work_ladder_settings WHERE user_id=?",(user_id,)).fetchone()
    if not row: return {"enabled":False,"router_provider_id":None,"multimodal_provider_id":None,"smart_provider_id":None,"medium_provider_id":None,"weak_provider_id":None,"fallback_provider_ids":[]}
    try: fallback=json.loads(row["fallback_provider_ids"] or "[]")
    except (TypeError,ValueError): fallback=[]
    return {"enabled":bool(row["enabled"]),"router_provider_id":row["router_provider_id"],"multimodal_provider_id":row["multimodal_provider_id"],"smart_provider_id":row["smart_provider_id"],"medium_provider_id":row["medium_provider_id"],"weak_provider_id":row["weak_provider_id"],"fallback_provider_ids":[int(x) for x in fallback if str(x).isdigit()][:MAX_FALLBACKS]}

def save_settings(db,user_id,payload):
    init_db(db)
    values={role+"_provider_id":payload.get(role+"_provider_id") for role in TIERS}
    for role in ("router","smart","medium","weak"): _validate_role(db,user_id,values[role+"_provider_id"],role)
    _validate_role(db,user_id,values["multimodal_provider_id"],"multimodal")
    raw=payload.get("fallback_provider_ids") or []
    if isinstance(raw,str): raw=[x for x in raw.split(",") if x.strip()]
    fallbacks=[]
    for pid in raw:
        row=_provider_row(db,user_id,pid)
        if row and str(row["kind"] or "text")!="image" and row["id"] not in fallbacks: fallbacks.append(int(row["id"]))
        if len(fallbacks)>=MAX_FALLBACKS: break
    enabled=bool(payload.get("enabled"))
    for role,label in (("router","роутер"),("smart","умная"),("medium","средняя"),("weak","слабая")):
        if enabled and not values[role+"_provider_id"]: raise ValueError("Для лестницы нужна "+label+" модель")
    with db() as c:
        c.execute("INSERT INTO work_ladder_settings (user_id,enabled,router_provider_id,multimodal_provider_id,smart_provider_id,medium_provider_id,weak_provider_id,fallback_provider_ids) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled,router_provider_id=excluded.router_provider_id,multimodal_provider_id=excluded.multimodal_provider_id,smart_provider_id=excluded.smart_provider_id,medium_provider_id=excluded.medium_provider_id,weak_provider_id=excluded.weak_provider_id,fallback_provider_ids=excluded.fallback_provider_ids,updated_at=CURRENT_TIMESTAMP",(user_id,int(enabled),values["router_provider_id"],values["multimodal_provider_id"],values["smart_provider_id"],values["medium_provider_id"],values["weak_provider_id"],json.dumps(fallbacks)))
    return get_settings(db,user_id)

def provider_for_role(db,user_id,settings,role):
    pid=settings.get(role+"_provider_id")
    return _provider_row(db,user_id,pid) if pid else None

def provider_label(row):
    return {"id":row["id"],"name":row["name"],"model":row["model"],"kind":row["kind"]} if row else None

def parse_router_response(raw,tasks):
    text=str(raw or "").strip().replace(chr(96)*3+"json","").replace(chr(96)*3,"").strip()
    try: data=json.loads(text)
    except (TypeError,ValueError): data={}
    assignments=data.get("assignments",data.get("tasks",data)) if isinstance(data,dict) else data
    if isinstance(assignments,dict): assignments=[{"task":k,"tier":v} for k,v in assignments.items()]
    if not isinstance(assignments,list): assignments=[]
    result={}
    for item in assignments:
        if not isinstance(item,dict): continue
        try: idx=int(item.get("task",item.get("index",item.get("task_id"))))
        except (TypeError,ValueError): continue
        if 1<=idx<=len(tasks): idx-=1
        if not 0<=idx<len(tasks): continue
        tier=str(item.get("tier","")).lower().strip()
        if tier not in TEXT_TIERS and tier!="multimodal": tier="medium"
        result[idx]={"tier":tier,"reason":str(item.get("reason","")).strip()[:500]}
    return result

def route_tasks(router_call,tasks,request_text,has_images=False):
    lines=["TASK %d: %s"%(i,json.dumps(task,ensure_ascii=False)) for i,task in enumerate(tasks,1)]
    prompt=("Ты модель-роутер Work Mode. Не выполняй задачи. Для каждой задачи выбери минимально достаточный tier: weak, medium, smart или multimodal. multimodal нужен только если задаче нужно читать изображения. Сложное рассуждение, код и строгий синтез требуют smart; обычная работа medium; простая механика weak. Верни только JSON вида {"assignments":[{"task":1,"tier":"medium","reason":"..."}]}.\nЗапрос:\n"+str(request_text)+"\n"+("\nЕсть изображения. Назначай multimodal только задачам, которым они нужны.\n" if has_images else "")+"\n".join(lines))
    raw=router_call([{"role":"system","content":prompt},{"role":"user","content":"Назначь tier всем задачам."}])
    return parse_router_response(raw,tasks),raw

def choose_task_role(task,settings,image_required=False):
    if image_required: return "multimodal" if settings.get("multimodal_provider_id") else "smart"
    tier=str(task.get("tier") or "medium").lower()
    return tier if tier in TEXT_TIERS else "medium"

def execute_with_fallback(db,user_id,primary,fallback_ids,call):
    candidates=[];seen=set();errors=[]
    for row in [primary]+[_provider_row(db,user_id,x) for x in (fallback_ids or [])]:
        if not row or int(row["id"]) in seen or str(row["kind"] or "text")=="image": continue
        seen.add(int(row["id"]));candidates.append(row)
    for row in candidates:
        try: return call(row),{"provider":provider_label(row),"attempts":len(errors)+1,"fallback_used":bool(errors),"errors":errors}
        except Exception as exc: errors.append({"provider":provider_label(row),"error":str(exc)[:1000]})
    raise RuntimeError("Все выбранные модели недоступны: "+" | ".join(x["provider"]["name"]+": "+x["error"] for x in errors))

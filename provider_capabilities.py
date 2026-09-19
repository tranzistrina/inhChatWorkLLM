"""Provider capability profiles used by Work Mode routing."""
import json

DEFAULTS={
    "text":{"vision":False,"reasoning":False,"coding":False,"tool_calling":False,"max_context":0,"max_output":0},
    "multimodal":{"vision":True,"reasoning":False,"coding":False,"tool_calling":False,"max_context":0,"max_output":0},
    "image":{"vision":False,"reasoning":False,"coding":False,"tool_calling":False,"max_context":0,"max_output":0},
}

def infer(kind,model=""):
    value=dict(DEFAULTS.get(str(kind or "text").lower(),DEFAULTS["text"]))
    name=str(model or "").lower()
    if any(x in name for x in ("reason","thinking","r1","o1","o3","qwen3")): value["reasoning"]=True
    if any(x in name for x in ("code","coder","codestral","deepseek-coder")): value["coding"]=True
    return value

def load(raw,kind,model):
    try:
        data=json.loads(raw or "{}") if isinstance(raw,str) else dict(raw or {})
    except (TypeError,ValueError):
        data={}
    base=infer(kind,model);base.update({k:v for k,v in data.items() if k in base})
    return base

def requirements(task,images=False):
    text=json.dumps(task,ensure_ascii=False).lower()
    return {
        "vision":bool(images) or any(x in text for x in ("изображен","скриншот","фото","image","screenshot")),
        "coding":any(x in text for x in ("код","программ","python","javascript","refactor","рефактор")),
        "reasoning":any(x in text for x in ("проанализ","сравни","докажи","архитект","исследован","сложн")),
    }

def compatible(cap,req):
    return all(not needed or bool(cap.get(key)) for key,needed in req.items())

def capability_score(cap,req):
    return sum(bool(cap.get(k)) == bool(v) for k,v in req.items())

"""Validation boundary between model text directives and OS side effects."""
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlparse

@dataclass(frozen=True)
class Action:
    kind:str
    payload:dict

MAX_ACTIONS=32
MAX_FILE_CHARS=2_000_000
MAX_ARCHIVE_FILES=100

def parse(raw):
    text=str(raw or "")
    actions=[]
    for m in re.finditer(r"FILE:\s*([^\n]+)\n([\s\S]*?)(?=\nFILE:|\nCOPY_FROM:|\nARCHIVE:|\nGITHUB:|\nGENERATE_IMAGE:|\Z)",text,re.I):
        actions.append(Action("file",{"path":m.group(1).strip(),"content":m.group(2)}))
    for m in re.finditer(r"COPY_FROM:\s*([^\n]+?)\s*=>\s*([^\n]+)",text,re.I):
        actions.append(Action("copy",{"source":m.group(1).strip(),"destination":m.group(2).strip()}))
    for m in re.finditer(r"ARCHIVE:\s*([^\n]+)\nFILES:\s*([^\n]+)",text,re.I):
        actions.append(Action("archive",{"name":m.group(1).strip(),"files":[x.strip() for x in re.split(r"[,;]",m.group(2)) if x.strip()]}))
    for m in re.finditer(r"GITHUB:\s*(https?://github\.com/[^\s]+?)(?:\s*=>\s*([^\n]+))?\n",text,re.I):
        actions.append(Action("github",{"url":m.group(1).rstrip("/").rstrip("."),"destination":(m.group(2) or "").strip()}))
    for m in re.finditer(r"GENERATE_IMAGE:\s*([^\n]+)",text,re.I):
        actions.append(Action("image",{"prompt":m.group(1).strip()}))
    if len(actions)>MAX_ACTIONS:
        raise ValueError("Превышен лимит действий Work Mode")
    return [validate(a) for a in actions]

def _safe_rel(value):
    p=Path(str(value).replace("\\","/"))
    if p.is_absolute() or ".." in p.parts:
        raise ValueError("Небезопасный путь в действии")
    return str(p)

def validate(action):
    p=action.payload
    if action.kind=="file":
        p["path"]=_safe_rel(p["path"])
        if len(p["content"])>MAX_FILE_CHARS: raise ValueError("FILE превышает лимит размера")
    elif action.kind=="copy":
        p["source"]=_safe_rel(p["source"]);p["destination"]=_safe_rel(p["destination"])
    elif action.kind=="archive":
        p["name"]=_safe_rel(p["name"])
        p["files"]=[_safe_rel(x) for x in p["files"][:MAX_ARCHIVE_FILES]]
    elif action.kind=="github":
        u=urlparse(p["url"])
        if u.scheme!="https" or u.netloc.lower()!="github.com": raise ValueError("Разрешены только HTTPS GitHub URL")
        parts=[x for x in u.path.strip("/").split("/") if x]
        if len(parts)<2 or any(x in (".","..") for x in parts[:2]): raise ValueError("Некорректный GitHub repository URL")
        p["destination"]=_safe_rel(p["destination"] or parts[1].removesuffix(".git"))
    elif action.kind=="image":
        if not p["prompt"] or len(p["prompt"])>10000: raise ValueError("Некорректный image prompt")
    else:
        raise ValueError("Неизвестное действие")
    return action

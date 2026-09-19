from server import app, llm as base_llm
import server
import work_routes
import work_recovery
import os
from llm_execution import execute

def resilient_llm(provider, messages):
    result,_meta=execute(base_llm,provider,messages,max_attempts=2)
    return result

server.llm = resilient_llm
work_routes.llm = resilient_llm

app.run(host='127.0.0.1',port=int(os.getenv('PORT','6767')),debug=False)

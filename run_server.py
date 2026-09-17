from server import app, llm as base_llm
import work_routes
import work_recovery
import os
from llm_retry import with_retries

def resilient_llm(provider, messages):
    return with_retries(base_llm, provider, messages, attempts=3)

# Work Mode and ordinary chat routes use the resilient wrapper without changing their API.
work_routes.llm = resilient_llm
app.run(host='127.0.0.1',port=int(os.getenv('PORT','6767')),debug=False)

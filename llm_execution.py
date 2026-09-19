"""Unified provider execution policy for Work Mode and normal LLM calls."""
import time

ERROR_AUTH="AUTH"
ERROR_RATE_LIMIT="RATE_LIMIT"
ERROR_TIMEOUT="TIMEOUT"
ERROR_CONTEXT="CONTEXT_TOO_LONG"
ERROR_INVALID="INVALID_REQUEST"
ERROR_SERVER="SERVER_ERROR"
ERROR_EMPTY="EMPTY_RESPONSE"
ERROR_UNKNOWN="UNKNOWN"

def classify_error(exc):
    text=str(exc or "").lower()
    status=getattr(getattr(exc,"response",None),"status_code",None)
    if status in (401,403) or any(x in text for x in ("401","403","unauthorized","forbidden","invalid api key")): return ERROR_AUTH
    if status==429 or "rate limit" in text or "too many requests" in text: return ERROR_RATE_LIMIT
    if isinstance(exc,TimeoutError) or "timeout" in text or "timed out" in text: return ERROR_TIMEOUT
    if status in (400,422) and any(x in text for x in ("context","token","too long","maximum context")): return ERROR_CONTEXT
    if status in (400,422): return ERROR_INVALID
    if status and status>=500: return ERROR_SERVER
    if "empty" in text or "пуст" in text: return ERROR_EMPTY
    return ERROR_UNKNOWN

def execute(base_call, provider, messages, *, max_attempts=2, sleep_seconds=1.0):
    errors=[]
    for attempt in range(1,max_attempts+1):
        try:
            result=base_call(provider,messages)
            if result is None or (isinstance(result,str) and not result.strip()):
                raise RuntimeError("API вернул пустой ответ")
            return result,{"attempts":attempt,"errors":errors,"error_class":None}
        except Exception as exc:
            kind=classify_error(exc)
            item={"class":kind,"error":str(exc)[:1000],"attempt":attempt}
            errors.append(item)
            if kind in (ERROR_AUTH,ERROR_INVALID,ERROR_CONTEXT) or attempt>=max_attempts:
                break
            time.sleep(min(sleep_seconds*(2**(attempt-1)),4))
    raise RuntimeError("LLM execution failed: "+ " | ".join(x["class"]+": "+x["error"] for x in errors))

def should_fallback(error_class):
    return error_class not in (ERROR_AUTH,)

def should_escalate(error_class):
    return error_class==ERROR_CONTEXT

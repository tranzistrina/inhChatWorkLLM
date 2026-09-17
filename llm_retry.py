import time

def with_retries(base_llm, provider, messages, attempts=3):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            result = base_llm(provider, messages)
            if result:
                return result
            raise RuntimeError('API вернул пустой ответ')
        except Exception as exc:
            last = exc
            if attempt < attempts:
                time.sleep(min(2 ** (attempt - 1), 4))
    raise RuntimeError(f'API не ответил после {attempts} попыток: {last}')

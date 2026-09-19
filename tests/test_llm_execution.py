import unittest
from llm_execution import classify_error, ERROR_CONTEXT, ERROR_AUTH, execute

class ExecutionTests(unittest.TestCase):
    def test_context_classification(self):
        class E(Exception):
            response=type("R",(),{"status_code":400})()
        self.assertEqual(classify_error(E("maximum context length exceeded")),ERROR_CONTEXT)
    def test_auth_classification(self):
        class E(Exception):
            response=type("R",(),{"status_code":401})()
        self.assertEqual(classify_error(E("bad key")),ERROR_AUTH)
    def test_retry_stops_on_auth(self):
        calls=[]
        def f(provider,messages):
            calls.append(1); raise RuntimeError("401 unauthorized")
        with self.assertRaises(RuntimeError): execute(f,{},[],max_attempts=3)
        self.assertEqual(len(calls),1)

if __name__=="__main__": unittest.main()

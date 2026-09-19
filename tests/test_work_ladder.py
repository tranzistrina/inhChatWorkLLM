import json
import sqlite3
import unittest
from work_ladder import execute_with_fallback, parse_router_response, choose_task_role, save_settings, route_tasks

class WorkLadderTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(":memory:")
        self.conn.row_factory=sqlite3.Row
        def db(): return self.conn
        self.db=db
        self.conn.execute("CREATE TABLE providers(id INTEGER PRIMARY KEY,user_id INTEGER,name TEXT,base_url TEXT,api_key TEXT,model TEXT,kind TEXT)")
        rows=[
            (1,1,"Router","http://r","","router","text"),
            (2,1,"Vision","http://v","","vision","multimodal"),
            (3,1,"Smart","http://s","","smart","text"),
            (4,1,"Medium","http://m","","medium","text"),
            (5,1,"Weak","http://w","","weak","text"),
            (6,1,"Backup","http://b","","backup","text"),
            (7,1,"Image","http://i","","image","image")]
        self.conn.executemany("INSERT INTO providers VALUES(?,?,?,?,?,?,?)",rows)

    def test_router_json(self):
        raw=json.dumps({"assignments":[{"task":1,"tier":"weak","reason":"simple"},{"task":2,"tier":"smart","reason":"complex"}]})
        parsed=parse_router_response(raw,[{"title":"a"},{"title":"b"}])
        self.assertEqual(parsed[0]["tier"],"weak")
        self.assertEqual(parsed[1]["tier"],"smart")

    def test_router_call_assigns_tasks(self):
        def router(messages):
            return json.dumps({"assignments":[{"task":1,"tier":"weak"},{"task":2,"tier":"smart"}]})
        assignments,_=route_tasks(router,[{"title":"a"},{"title":"b"}],"request")
        self.assertEqual(assignments[0]["tier"],"weak")
        self.assertEqual(assignments[1]["tier"],"smart")

    def test_multimodal_selection(self):
        settings={"multimodal_provider_id":2}
        self.assertEqual(choose_task_role({"tier":"weak"},settings,True),"multimodal")
        self.assertEqual(choose_task_role({"tier":"smart"},settings,False),"smart")

    def test_save_settings_rejects_image_provider(self):
        with self.assertRaises(ValueError):
            save_settings(self.db,1,{"enabled":True,"router_provider_id":7,"multimodal_provider_id":2,"smart_provider_id":3,"medium_provider_id":4,"weak_provider_id":5})

    def test_fallback_on_failure(self):
        calls=[]
        def call(p):
            calls.append(p["id"])
            if p["id"]==3: raise RuntimeError("down")
            return "ok"
        result,meta=execute_with_fallback(self.db,1,{"id":3,"name":"Smart","model":"smart","kind":"text"},[6],call)
        self.assertEqual(result,"ok")
        self.assertEqual(calls,[3,6])
        self.assertTrue(meta["fallback_used"])

    def test_image_provider_never_used_as_text_fallback(self):
        result,meta=execute_with_fallback(self.db,1,{"id":7,"name":"Image","model":"image","kind":"image"},[7,6],lambda p:"ok")
        self.assertEqual(result,"ok")
        self.assertEqual(meta["provider"]["id"],6)

if __name__=="__main__":
    unittest.main()

import sqlite3
import unittest
from work_state import transition

class StateTests(unittest.TestCase):
    def test_transitions(self):
        conn=sqlite3.connect(":memory:")
        conn.row_factory=sqlite3.Row
        conn.execute("CREATE TABLE work_runs(id TEXT PRIMARY KEY,user_id INTEGER,status TEXT,active_task_index INTEGER)")
        conn.execute("INSERT INTO work_runs VALUES('r',1,'CREATED',NULL)")
        db=lambda: conn
        transition(db,"r",1,"PLANNING",expected="CREATED")
        transition(db,"r",1,"PLANNED",expected="PLANNING")
        self.assertEqual(conn.execute("SELECT status FROM work_runs WHERE id='r'").fetchone()[0],"PLANNED")
        with self.assertRaises(ValueError): transition(db,"r",1,"COMPLETED")
if __name__=="__main__": unittest.main()

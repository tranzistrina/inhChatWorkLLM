"""Persistent Work Run state machine and atomic transition helpers."""
from datetime import datetime

STATES=("CREATED","PLANNING","PLANNED","RUNNING_TASK","FINALIZING","COMPLETED","FAILED","CANCELLED")
TERMINAL={"COMPLETED","FAILED","CANCELLED"}
ALLOWED={
 "CREATED":{"PLANNING","FAILED","CANCELLED"},
 "PLANNING":{"PLANNED","FAILED","CANCELLED"},
 "PLANNED":{"RUNNING_TASK","FINALIZING","FAILED","CANCELLED"},
 "RUNNING_TASK":{"RUNNING_TASK","FINALIZING","FAILED","CANCELLED"},
 "FINALIZING":{"COMPLETED","FAILED","CANCELLED"},
 "COMPLETED":set(),
 "FAILED":{"PLANNING","RUNNING_TASK","FINALIZING","CANCELLED"},
 "CANCELLED":set(),
}

def transition(db,run_id,user_id,new_state,expected=None):
    if new_state not in STATES: raise ValueError("Недопустимое состояние Work Run")
    with db() as c:
        row=c.execute("SELECT status FROM work_runs WHERE id=? AND user_id=?",(run_id,user_id)).fetchone()
        if not row: raise ValueError("Work Run не найден")
        old=row["status"] or "CREATED"
        if expected and old!=expected: raise ValueError("Состояние Work Run изменилось")
        if new_state not in ALLOWED.get(old,set()): raise ValueError("Переход %s -> %s запрещён"%(old,new_state))
        c.execute("UPDATE work_runs SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",(new_state,run_id,user_id))
    return new_state

def acquire_task(db,run_id,user_id,index):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("SELECT status,active_task_index FROM work_runs WHERE id=? AND user_id=?",(run_id,user_id)).fetchone()
        if not row: raise ValueError("Work Run не найден")
        if row["active_task_index"] is not None and int(row["active_task_index"])!=index:
            raise ValueError("Другая задача этого запуска уже выполняется")
        c.execute("UPDATE work_runs SET status='RUNNING_TASK',active_task_index=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",(index,run_id,user_id))
    return True

def release_task(db,run_id,user_id,index):
    with db() as c:
        c.execute("UPDATE work_runs SET active_task_index=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=? AND active_task_index=?",(run_id,user_id,index))

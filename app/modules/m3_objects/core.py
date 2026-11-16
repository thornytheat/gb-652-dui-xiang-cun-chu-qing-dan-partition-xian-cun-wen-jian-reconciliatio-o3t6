from app.contracts import V1, verify_row
from app.reasons import reason as reason_text

SAMPLE={"manifest":[{"key":"a","size":"120","etag":"abc"},{"key":"b","size":"80","etag":"def-2"}],"objects":[{"key":"a","size":120,"etag":"abc"},{"key":"b","size":70,"etag":"def-2"}]}

def verify(expected, actual):
    return verify_row(expected, actual, V1)["status"]

def verify_all(manifest, objects, contract_version=V1):
    actual={x["key"]:x for x in objects}; out=[]
    for row_no,row in enumerate(manifest):
        result=verify_row(row,actual.get(row["key"]),contract_version)
        out.append({"row_no":row_no,"key":row["key"],"contract_version":contract_version,**result})
    return out

def run(payload):
    payload=payload or {}; d=SAMPLE|payload
    store=d.get("store")
    manifest_rows=d["manifest"]; contract=d.get("contract_version",V1)
    manifest_id=None; source="sample"
    if "manifest" in payload:
        source="payload"  # 显式提交的临时清单，不落核验记录
    elif store:
        receipt=store.get_manifest(d["manifest_id"]) if d.get("manifest_id") else store.latest_manifest()
        if receipt:
            manifest_id=receipt["manifest_id"]; source="store"
            contract=receipt["contract_version"]  # 清单绑定接收时的版本，升级不影响在途清单
            manifest_rows=receipt["payload"]["rows"]
    objects=d["objects"]
    if store and manifest_id:
        if "objects" in payload:
            store.record_objects(manifest_id,objects)  # 留存本次采用的对象证据
        else:
            snapshot=store.get_objects(manifest_id)
            if snapshot is not None: objects=snapshot  # 重试沿用上次证据，不另起口径
    rows=verify_all(manifest_rows,objects,contract)
    for row in rows: row["reason_text"]=reason_text(row["reason"])
    result={"status":"READY","manifest_id":manifest_id,"manifest_source":source,"contract_version":contract,
            "input_count":len(rows),"valid_count":sum(x["status"]=="MATCH" for x in rows),
            "quarantine_count":sum(x["status"]!="MATCH" for x in rows),"checks":rows,
            "reason":"DIFF" if any(x["status"]!="MATCH" for x in rows) else "OK"}
    if store and manifest_id:
        store.record_verifications(manifest_id,rows)  # 重试就地更新，不新增核验记录
        result["records"]=store.verification_count(manifest_id)
    else:
        result["records"]=0
    return result

def migration_outcome(record):
    """历史记录迁移裁决：旧契约证据完整才沿用，缺失则拒绝猜测补齐。"""
    if record["contract_version"]!=V1: return "current","OK"
    if record["status"]=="MATCH" and record["evidence"]=="size+etag": return "carried","OK"
    return "refused","EVIDENCE_MISSING"

def migrate(store, batch_size=None):
    """按游标迁移历史核验记录；每步与游标同事务提交，中断后可续跑。"""
    cursor=store.migration_status()["cursor"]
    limit=batch_size if batch_size and batch_size>0 else 1000
    rows=store.fetch_verifications_after(cursor,limit+1)
    more=len(rows)>limit; batch=rows[:limit]
    counts={"carried":0,"refused":0,"current":0}
    for index,record in enumerate(batch):
        state,note=migration_outcome(record); cursor=record["vid"]
        store.migrate_step(cursor,state,note,done=not more and index==len(batch)-1)
        counts[state]+=1
    if not batch: store.set_migration_done(True)
    return {"cursor":cursor,"done":not more,"processed":len(batch),**counts}

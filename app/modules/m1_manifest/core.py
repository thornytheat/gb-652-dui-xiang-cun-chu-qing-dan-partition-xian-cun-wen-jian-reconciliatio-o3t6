import csv, io, json
from app.manifest import canonical_digest

SAMPLE={"text":"key,size,etag\ndt=2026-11-15/hour=00/a.parquet,120,aaa\ndt=2026-11-15/hour=01/b.parquet,80,bbb\ndt=2026-11-15/hour=01/b.parquet,81,ccc\nbad,1\n","format":"csv"}
REQUIRED={"key","size","etag"}
OPTIONAL={"digest"}  # 新契约增加的整文件摘要列；分片标识 etag 仍保留

def parse(text, fmt="csv"):
    if not text.strip(): return {"state":"empty-file","rows":[],"quarantine":[]}
    rows=[]; quarantine=[]
    if fmt=="jsonl":
        for line_no,line in enumerate(text.splitlines(),1):
            if not line.strip(): continue
            try: rows.append(json.loads(line)|{"source_line":line_no})
            except json.JSONDecodeError: quarantine.append({"source_line":line_no,"reason":"TRUNCATED"})
    else:
        reader=csv.DictReader(io.StringIO(text));
        if not reader.fieldnames: return {"state":"empty-file","rows":[],"quarantine":[]}
        reader.fieldnames=[x.lstrip("\ufeff") for x in reader.fieldnames]
        for line_no,row in enumerate(reader,2):
            if None in row or any(value is None for value in row.values()) or not REQUIRED<=set(row)<=REQUIRED|OPTIONAL: quarantine.append({"source_line":line_no,"reason":"BAD_COLUMNS"})
            else: rows.append(row|{"source_line":line_no})
    return {"state":"only-header" if not rows and not quarantine else "data","rows":rows,"quarantine":quarantine}

def receive(store, text, fmt, parsed):
    """接收清单并绑定接收时的契约版本；同一内容重复接收沿用首次绑定。"""
    manifest_id="mfst-"+canonical_digest({"format":fmt,"text":text})[:12]
    return store.record_manifest(manifest_id,{"format":fmt,"rows":parsed["rows"],"quarantine":parsed["quarantine"]})

def run(payload):
    d=SAMPLE|payload; out=parse(d["text"],d.get("format","csv")); result={"status":"READY","input_count":len(out["rows"])+len(out["quarantine"]),"valid_count":len(out["rows"]),"quarantine_count":len(out["quarantine"]),"state":out["state"],"reason":out["quarantine"][0]["reason"] if out["quarantine"] else "OK"}
    store=d.get("store")
    if store:
        receipt=receive(store,d["text"],d.get("format","csv"),out)
        result|={"manifest_id":receipt["manifest_id"],"contract_version":receipt["contract_version"],"received_at":receipt["received_at"]}
    return result

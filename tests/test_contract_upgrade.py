import threading

import pytest

from app.contracts import V1, V2, verify_row
from app.modules.m1_manifest.core import parse as m1_parse, run as m1_run
from app.modules.m3_objects import core as m3
from app.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "t.db"))


def import_manifest(store, text, fmt="csv"):
    return m1_run({"text": text, "format": fmt, "store": store})


# ---- 摘要契约：区分分片标识与内容摘要 ----

def test_same_size_does_not_mix_proofs():
    row = {"key": "a", "size": 10, "etag": "e1", "digest": "d1"}
    # 新契约：大小相同、分片标识一致，但缺整文件摘要仍被拒
    assert verify_row(row, {"key": "a", "size": 10, "etag": "e1"}, V2)["status"] == "DIGEST_MISSING"
    # 旧契约：内容摘要一致也顶替不了分片标识不符
    assert verify_row({"key": "a", "size": 10, "etag": "e1"},
                      {"key": "a", "size": 10, "etag": "e2", "digest": "d1"}, V1)["status"] == "ETAG_MISMATCH"


def test_each_contract_uses_its_own_proof():
    row = {"key": "a", "size": 10, "etag": "e1", "digest": "d1"}
    v2 = verify_row(row, {"key": "a", "size": 10, "etag": "e9", "digest": "d1"}, V2)
    assert v2["status"] == "MATCH" and v2["evidence"] == "size+digest"
    v1 = verify_row({"key": "a", "size": 10, "etag": "e1"},
                    {"key": "a", "size": 10, "etag": "e1", "digest": "d9"}, V1)
    assert v1["status"] == "MATCH" and v1["evidence"] == "size+etag"


def test_v2_rejects_digest_mismatch():
    row = {"key": "a", "size": 10, "etag": "e1", "digest": "d1"}
    out = verify_row(row, {"key": "a", "size": 10, "etag": "e1", "digest": "d2"}, V2)
    assert out["status"] == "DIGEST_MISMATCH" and out["evidence"] == "size+digest"


def test_manifest_accepts_optional_digest_column():
    out = m1_parse("key,size,etag,digest\na,1,e1,d1\n")
    assert out["rows"][0]["digest"] == "d1" and not out["quarantine"]
    assert m1_parse("key,size,etag,zzz\na,1,e1,d1\n")["quarantine"][0]["reason"] == "BAD_COLUMNS"


# ---- 清单绑定接收时的版本 ----

def test_manifest_binds_contract_at_receipt(store):
    first = import_manifest(store, "key,size,etag\na,10,e1\n")
    assert first["contract_version"] == V1
    store.upgrade_contract(V2)
    second = import_manifest(store, "key,size,etag,digest\nb,20,e2,d2\n")
    assert second["contract_version"] == V2
    again = import_manifest(store, "key,size,etag\na,10,e1\n")
    assert again["manifest_id"] == first["manifest_id"] and again["contract_version"] == V1


def test_inflight_manifest_keeps_bound_version(store):
    bound = import_manifest(store, "key,size,etag\na,10,e1\n")
    store.upgrade_contract(V2)
    out = m3.run({"store": store, "manifest_id": bound["manifest_id"],
                  "objects": [{"key": "a", "size": 10, "etag": "e1"}]})
    assert out["contract_version"] == V1
    assert out["checks"][0]["status"] == "MATCH" and out["checks"][0]["evidence"] == "size+etag"


def test_new_manifest_rejects_object_without_digest(store):
    import_manifest(store, "key,size,etag,digest\nb,20,e2,d2\n")
    store.upgrade_contract(V2)
    bound = import_manifest(store, "key,size,etag,digest\nc,30,e3,d3\n")
    assert bound["contract_version"] == V2
    out = m3.run({"store": store, "manifest_id": bound["manifest_id"],
                  "objects": [{"key": "c", "size": 30, "etag": "e3"}]})
    check = out["checks"][0]
    assert check["status"] == "DIGEST_MISSING" and check["reason_text"] == "缺少整文件摘要"
    assert out["quarantine_count"] == 1


# ---- 重试不新增核验记录 ----

def test_retry_adds_no_new_records(store):
    bound = import_manifest(store, "key,size,etag\na,10,e1\nb,20,e2\n")
    objects = [{"key": "a", "size": 10, "etag": "e1"}, {"key": "b", "size": 20, "etag": "e2"}]
    m3.run({"store": store, "objects": objects})
    before = [(r["vid"], r["status"]) for r in store.verifications_for(bound["manifest_id"])]
    store.upgrade_contract(V2)  # 升级不改变在途清单的重试口径
    out = m3.run({"store": store, "manifest_id": bound["manifest_id"], "objects": objects})
    after = [(r["vid"], r["status"]) for r in store.verifications_for(bound["manifest_id"])]
    assert before == after and len(after) == 2
    assert out["records"] == 2 and out["contract_version"] == V1


# ---- 历史迁移：中断可恢复，缺证据拒绝猜测补齐 ----

def seed_v1_records(store):
    bound = import_manifest(store, "key,size,etag\na,10,e1\nb,20,e2\nc,30,e3\n")
    m3.run({"store": store, "objects": [{"key": "a", "size": 10, "etag": "e1"}]})
    return bound["manifest_id"]


def test_migration_resume_after_interrupt(store, monkeypatch):
    seed_v1_records(store)
    assert store.migration_status()["pending"] == 3
    first = m3.migrate(store, batch_size=1)
    assert first["processed"] == 1 and first["done"] is False
    original = store.migrate_step
    def flaky(*args, **kwargs):
        raise RuntimeError("crash")
    monkeypatch.setattr(store, "migrate_step", flaky)
    with pytest.raises(RuntimeError):
        m3.migrate(store, batch_size=2)
    monkeypatch.setattr(store, "migrate_step", original)
    rest = m3.migrate(store)
    assert rest["done"] is True and rest["processed"] == 2
    status = store.migration_status()
    assert status["pending"] == 0 and status["carried"] == 1 and status["refused"] == 2
    again = m3.migrate(store)
    assert again["processed"] == 0 and store.migration_status()["carried"] == 1


def test_migration_refuses_to_guess_missing_evidence(store):
    manifest_id = seed_v1_records(store)
    m3.migrate(store)
    records = {r["key"]: r for r in store.verifications_for(manifest_id)}
    assert records["a"]["migration_state"] == "carried"
    for key in ("b", "c"):
        assert records[key]["migration_state"] == "refused"
        assert records[key]["migration_note"] == "EVIDENCE_MISSING"
        # 不猜测补齐：原判定与证据保持原样
        assert records[key]["status"] == "MISSING" and records[key]["evidence"] == "manifest"


def test_migration_marks_new_contract_records_current(store):
    import_manifest(store, "key,size,etag\na,10,e1\n")
    m3.run({"store": store, "objects": [{"key": "a", "size": 10, "etag": "e1"}]})
    store.upgrade_contract(V2)
    bound = import_manifest(store, "key,size,etag,digest\nb,20,e2,d2\n")
    m3.run({"store": store, "manifest_id": bound["manifest_id"],
            "objects": [{"key": "b", "size": 20, "etag": "e2", "digest": "d2"}]})
    m3.migrate(store)
    status = store.migration_status()
    assert status["carried"] == 1 and status["current"] == 1 and status["pending"] == 0


# ---- 并发升级：只有一个生效，在途清单沿用原版 ----

def test_concurrent_upgrade_single_winner(store):
    import_manifest(store, "key,size,etag\na,10,e1\n")
    results = []
    threads = [threading.Thread(target=lambda: results.append(store.upgrade_contract(V2)))
               for _ in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert sum(r["upgraded"] for r in results) == 1
    assert all(r["contract_version"] == V2 for r in results)
    assert store.current_contract()["version"] == V2
    assert len(store.contract_events()) == 1
    out = m3.run({"store": store, "objects": [{"key": "a", "size": 10, "etag": "e1"}]})
    assert out["contract_version"] == V1  # 在途清单沿用原版


def test_upgrade_is_idempotent(store):
    assert store.upgrade_contract(V2)["upgraded"] is True
    again = store.upgrade_contract(V2)
    assert again["upgraded"] is False and again["previous"] == V2


# ---- 页面与接口：真实清单、升级、重试、证据与原因 ----

def test_api_flow(client):
    bound = client.post("/api/manifests/import",
                        json={"text": "key,size,etag\na,10,e1\n"}).json()
    assert bound["contract_version"] == V1 and bound["manifest_id"].startswith("mfst-")
    verified = client.post("/api/objects/verify",
                           json={"objects": [{"key": "a", "size": 10, "etag": "e1"}]}).json()
    assert verified["manifest_id"] == bound["manifest_id"]
    assert verified["checks"][0]["evidence"] == "size+etag"
    upgraded = client.post("/api/objects/contract/upgrade").json()
    assert upgraded["upgraded"] is True and upgraded["contract_version"] == V2
    retried = client.post("/api/objects/retry", json={}).json()
    assert retried["retried"] is True and retried["contract_version"] == V1
    assert retried["records"] == verified["records"]
    migrated = client.post("/api/objects/migrate", json={}).json()
    assert migrated["done"] is True and migrated["carried"] == 1
    page = client.get("/objects")
    assert page.status_code == 200 and "采用证据" in page.text and "size+etag" in page.text


def test_retry_without_manifest_rejected(client):
    response = client.post("/api/objects/retry", json={})
    assert response.status_code == 422 and response.json()["error"] == "NO_MANIFEST"


def test_page_uses_real_manifest(client):
    client.post("/api/manifests/import", json={"text": "key,size,etag\nreal/one.parquet,10,e1\n"})
    page = client.get("/objects")
    assert "real/one.parquet" in page.text and "mfst-" in page.text

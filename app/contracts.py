"""摘要契约：清单在接收时绑定版本，分片标识与内容摘要互不替代。

- v1-etag   旧契约：按 大小 + 分片标识(etag) 验收
- v2-digest 新契约：按 大小 + 整文件摘要(digest) 验收
"""
from __future__ import annotations

V1 = "v1-etag"
V2 = "v2-digest"

CONTRACTS = {
    V1: {"label": "分片标识约定", "proof": "size+etag"},
    V2: {"label": "整文件摘要约定", "proof": "size+digest"},
}


def verify_row(expected: dict, actual: dict | None, contract_version: str) -> dict:
    """按清单绑定的契约核验单个对象，返回状态、采用的证据与原因码。

    大小相同也不混用证明：etag 是分片标识，digest 是内容摘要，互不顶替。
    """
    if contract_version not in CONTRACTS:
        raise ValueError(f"未知摘要契约: {contract_version}")
    if actual is None:
        return {"status": "MISSING", "evidence": "manifest", "reason": "MISSING"}
    if int(expected["size"]) != int(actual["size"]):
        return {"status": "SIZE_MISMATCH", "evidence": "size", "reason": "SIZE_MISMATCH"}
    if contract_version == V2:
        want, got = expected.get("digest"), actual.get("digest")
        if not want or not got:
            # 新契约必须见整文件摘要；只拿出旧证明(分片标识)也不达标
            evidence = "size+etag" if expected.get("etag") and expected.get("etag") == actual.get("etag") else "size"
            return {"status": "DIGEST_MISSING", "evidence": evidence, "reason": "DIGEST_MISSING"}
        if want != got:
            return {"status": "DIGEST_MISMATCH", "evidence": "size+digest", "reason": "DIGEST_MISMATCH"}
        return {"status": "MATCH", "evidence": "size+digest", "reason": "OK"}
    # v1 只认分片标识，内容摘要不参与判定
    if expected.get("etag") != actual.get("etag"):
        return {"status": "ETAG_MISMATCH", "evidence": "size", "reason": "ETAG_MISMATCH"}
    return {"status": "MATCH", "evidence": "size+etag", "reason": "OK"}

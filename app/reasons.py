REASONS = {
    "BAD_COLUMNS": "字段数量不符", "EMPTY_VALUE": "必要值为空",
    "DUPLICATE_CONFLICT": "同一业务键取值冲突", "TRUNCATED": "尾记录不完整",
    "INVALID_VALUE": "值不符合定义", "STALE_VERSION": "提交版本已过期",
    "OK": "核验通过", "MISSING": "清单有记录但对象缺失",
    "SIZE_MISMATCH": "大小不符", "ETAG_MISMATCH": "分片标识不符",
    "DIGEST_MISSING": "缺少整文件摘要", "DIGEST_MISMATCH": "整文件摘要与清单不符",
    "EVIDENCE_MISSING": "缺少旧契约证据，拒绝猜测补齐", "NO_MANIFEST": "尚未接收清单",
}

def reason(code: str) -> str:
    return REASONS.get(code, "未登记原因")

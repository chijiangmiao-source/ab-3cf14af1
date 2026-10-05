"""审计结论的封存存储。

每个稳定审计标识对应一份 JSON 封存文件，记录：语义指纹、原始请求、
判定结论与两棵证据树、封存时间。相同标识 + 语义等价输入回放原结论；
相同标识 + 不同输入报告冲突且 *保留原证据*。写入进程内加锁并原子
落盘，重启后结论仍可回放。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional


class AuditStore:
    def __init__(self, directory: str):
        self.directory = directory
        os.makedirs(self.directory, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, audit_id: str) -> str:
        safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in audit_id)
        return os.path.join(self.directory, f"{safe}.json")

    def get(self, audit_id: str) -> Optional[dict[str, Any]]:
        path = self._path(audit_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def get_or_seal(
        self,
        audit_id: str,
        fingerprint: str,
        request_payload: dict[str, Any],
        conclusion: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """原子地“回放 / 冲突 / 封存”。

        返回 (outcome, record)，outcome ∈ {"replayed", "conflict",
        "sealed"}；冲突时 record 是已存在的原封存（原证据保留）。
        """
        with self._lock:
            existing = self.get(audit_id)
            if existing is not None:
                if existing["fingerprint"] == fingerprint:
                    return "replayed", existing
                return "conflict", existing
            record = {
                "audit_id": audit_id,
                "fingerprint": fingerprint,
                "sealed_at": datetime.now(timezone.utc).isoformat(),
                "request": request_payload,
                "conclusion": conclusion,
            }
            path = self._path(audit_id)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(record, fh, ensure_ascii=False, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, path)
            return "sealed", record

    def seal(
        self,
        audit_id: str,
        fingerprint: str,
        request_payload: dict[str, Any],
        conclusion: dict[str, Any],
    ) -> dict[str, Any]:
        """封存新结论；已存在时调用方应先 get 判定回放或冲突。"""
        path = self._path(audit_id)
        record = {
            "audit_id": audit_id,
            "fingerprint": fingerprint,
            "sealed_at": datetime.now(timezone.utc).isoformat(),
            "request": request_payload,
            "conclusion": conclusion,
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
        return record

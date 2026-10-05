"""HTTP 场景冒烟：健康检查 + 唯一 / 歧义 / 无消费环三类判定。

可对任意 BASE_URL 运行；审计标识每次唯一，避免与已封存结论混淆。
任何断言失败以非零状态码退出。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8080").rstrip("/")


def request(method: str, path: str, body=None) -> tuple[int, dict]:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def wait_for_health(deadline_s: float = 30.0) -> None:
    deadline = time.time() + deadline_s
    last_err = None
    while time.time() < deadline:
        try:
            status, body = request("GET", "/health")
            if status == 200 and body.get("status") == "ok":
                print(f"[health] OK via {BASE_URL}")
                return
        except Exception as exc:  # noqa: BLE001 - 启动期重试
            last_err = exc
        time.sleep(0.5)
    raise SystemExit(f"health check failed: {last_err}")


def submit(audit_id: str, prods, tokens):
    return request(
        "POST",
        "/v1/verdicts",
        {
            "audit_id": audit_id,
            "start": "S",
            "productions": prods,
            "tokens": tokens,
        },
    )


def main() -> None:
    wait_for_health()
    tag = uuid.uuid4().hex[:12]

    # 1) 唯一接受
    status, body = submit(
        f"SMOKE-UNIQUE-{tag}",
        [
            {"id": 1, "lhs": "S", "rhs": ["a", "S", "b"]},
            {"id": 2, "lhs": "S", "rhs": ["a", "b"]},
        ],
        ["a", "a", "b", "b"],
    )
    assert status == 200, (status, body)
    verdict = body["verdict"]
    assert verdict["status"] == "unique", verdict
    assert verdict["trees"][0]["production_sequence"] == [1, 2]
    print("[unique] accepted with exactly one tree")

    # 2) 歧义接受，两棵稳定排序的证据树
    status, body = submit(
        f"SMOKE-AMBIGUOUS-{tag}",
        [
            {"id": 1, "lhs": "S", "rhs": ["S", "PLUS", "S"]},
            {"id": 2, "lhs": "S", "rhs": ["S", "STAR", "S"]},
            {"id": 3, "lhs": "S", "rhs": ["a"]},
        ],
        ["a", "PLUS", "a", "STAR", "a"],
    )
    assert status == 200, (status, body)
    verdict = body["verdict"]
    assert verdict["status"] == "ambiguous", verdict
    seqs = [tuple(t["production_sequence"]) for t in verdict["trees"]]
    assert len(seqs) == 2 and seqs[0] < seqs[1], seqs
    print(f"[ambiguous] two distinct stable evidence trees: {[list(s) for s in seqs]}")

    # 3) 可达的无消费环：明确拒绝
    status, body = submit(
        f"SMOKE-LOOP-{tag}",
        [
            {"id": 1, "lhs": "S", "rhs": ["a", "A", "b"]},
            {"id": 2, "lhs": "A", "rhs": ["A"]},
            {"id": 3, "lhs": "A", "rhs": []},
        ],
        ["a", "b"],
    )
    assert status == 200, (status, body)
    verdict = body["verdict"]
    assert verdict["status"] == "reject", verdict
    assert verdict["reason_code"] == "nonproductive_loop", verdict
    cycle = verdict["loop"]["cycle"]
    assert cycle[0] == cycle[-1] and cycle[0][0] == "A", cycle
    print(f"[non-consuming-loop] explicitly rejected: {cycle}")

    # 4) 附带：同一标识语义等价重传应回放
    status, body2 = request(
        "POST",
        "/v1/verdicts",
        {
            "audit_id": f"SMOKE-UNIQUE-{tag}",
            "start": "S",
            "productions": [
                {"id": 2, "lhs": "S", "rhs": ["a", "b"]},
                {"id": 1, "lhs": "S", "rhs": ["a", "S", "b"]},
            ],
            "tokens": ["a", "a", "b", "b"],
        },
    )
    assert status == 200 and body2["replayed"] is True, body2
    print("[replay] semantically equivalent retransmission replayed")

    print("ALL HTTP SMOKE SCENARIOS PASSED")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        sys.exit(1)

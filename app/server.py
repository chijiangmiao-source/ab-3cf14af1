"""深空着陆器文法判定 HTTP 服务（仅标准库）。

路由：
  GET  /health                      健康响应
  POST /v1/verdicts                 提交判定（带稳定审计标识）
  GET  /v1/verdicts/<audit_id>      回放已封存结论

判定三态 unique / ambiguous / reject 均以 200 返回（reject 是合法
结论而非传输错误）；请求结构/文法非法返回 400 并给出首个可操作原因；
审计标识复用语义不同的输入返回 409 并随响应保留原证据。
"""

from __future__ import annotations

import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine  # noqa: E402
from storage import AuditStore  # noqa: E402

MAX_AUDIT_ID_LEN = 128
MAX_BODY_BYTES = 1_000_000


class Service:
    def __init__(self, store: AuditStore):
        self.store = store

    def submit(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        audit_id = payload.get("audit_id")
        if not isinstance(audit_id, str) or not audit_id:
            raise engine.GrammarError(
                "malformed_audit_id",
                "audit_id must be a non-empty ASCII string",
            )
        if len(audit_id) > MAX_AUDIT_ID_LEN or any(
            ord(c) >= 128 for c in audit_id
        ):
            raise engine.GrammarError(
                "malformed_audit_id",
                f"audit_id must be ASCII and at most {MAX_AUDIT_ID_LEN} chars",
            )

        grammar_body = {
            "start": payload.get("start"),
            "nonterminals": payload.get("nonterminals"),
            "productions": payload.get("productions"),
            "tokens": payload.get("tokens", []),
        }
        # 先完整校验 + 判定，语义指纹只对合法输入计算。
        conclusion = engine.analyze(grammar_body)
        fingerprint = engine.canonical_fingerprint(grammar_body)

        # 原子检查并封存，避免并发重传时重复写入或竞态。
        outcome, record = self.store.get_or_seal(
            audit_id, fingerprint, grammar_body, conclusion
        )
        if outcome == "conflict":
            return HTTPStatus.CONFLICT, {
                "error_code": "audit_id_conflict",
                "message": (
                    f"audit_id '{audit_id}' is already sealed to semantically "
                    "different input; the original verdict and evidence are "
                    "preserved"
                ),
                "audit_id": audit_id,
                "original_fingerprint": record["fingerprint"],
                "original_sealed_at": record["sealed_at"],
                "original_verdict": record["conclusion"],
            }
        return HTTPStatus.OK, {
            "audit_id": audit_id,
            "replayed": outcome == "replayed",
            "sealed_at": record["sealed_at"],
            "verdict": record["conclusion"],
        }

    def fetch(self, audit_id: str) -> tuple[int, dict[str, Any]]:
        record = self.store.get(audit_id)
        if record is None:
            return HTTPStatus.NOT_FOUND, {
                "error_code": "unknown_audit_id",
                "message": f"no sealed verdict for audit_id '{audit_id}'",
            }
        return HTTPStatus.OK, {
            "audit_id": audit_id,
            "replayed": True,
            "sealed_at": record["sealed_at"],
            "verdict": record["conclusion"],
        }


def make_handler(service: Service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LanderGrammar/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write(
                "[%s] %s\n" % (self.log_date_time_string(), fmt % args)
            )

        def _send_json(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/health":
                self._send_json(HTTPStatus.OK, {"status": "ok"})
                return
            prefix = "/v1/verdicts/"
            if path.startswith(prefix) and len(path) > len(prefix):
                audit_id = unquote(path[len(prefix):])
                status, body = service.fetch(audit_id)
                self._send_json(status, body)
                return
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"error_code": "not_found", "message": f"unknown path {path}"},
            )

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path != "/v1/verdicts":
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"error_code": "not_found", "message": f"unknown path {path}"},
                )
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error_code": "malformed_body",
                        "message": "request must carry a JSON body",
                    },
                )
                return
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error_code": "malformed_body",
                        "message": "request body must be valid UTF-8 JSON",
                    },
                )
                return
            if not isinstance(payload, dict):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error_code": "malformed_request",
                        "message": "request body must be a JSON object",
                    },
                )
                return
            try:
                status, body = service.submit(payload)
            except engine.GrammarError as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": exc.to_detail()},
                )
                return
            except RecursionError:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": {
                            "error_code": "grammar_too_complex",
                            "message": (
                                "grammar expansion exceeded the safety bound; "
                                "simplify long recursive right-hand sides"
                            ),
                        }
                    },
                )
                return
            self._send_json(status, body)

    return Handler


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    seal_dir = os.environ.get("SEAL_DIR", "/data/seals")
    service = Service(AuditStore(seal_dir))
    httpd = ThreadingHTTPServer((host, port), make_handler(service))
    print(f"lander grammar service listening on {host}:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()

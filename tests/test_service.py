"""服务层测试：HTTP 路由、封存回放、标识冲突、健康检查。"""

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "app"))

import server as server_mod  # noqa: E402
from storage import AuditStore  # noqa: E402


def req(method, url, body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


class ServiceHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.service = server_mod.Service(AuditStore(cls.tmp.name))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.make_handler(cls.service))
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def body(self, audit_id, prods, tokens):
        return {
            "audit_id": audit_id,
            "start": "S",
            "productions": prods,
            "tokens": tokens,
        }

    def test_health(self):
        status, body = req("GET", f"{self.base}/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok"})

    def test_unique_then_replay_then_conflict(self):
        prods = [
            {"id": 1, "lhs": "S", "rhs": ["a", "S", "b"]},
            {"id": 2, "lhs": "S", "rhs": ["a", "b"]},
        ]
        status, body = req(
            "POST", f"{self.base}/v1/verdicts", self.body("X1", prods, ["a", "a", "b", "b"])
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["replayed"])
        self.assertEqual(body["verdict"]["status"], "unique")
        sealed_at = body["sealed_at"]

        # 产生式数组重排 = 语义等价，应回放。
        status, body = req(
            "POST",
            f"{self.base}/v1/verdicts",
            self.body("X1", list(reversed(prods)), ["a", "a", "b", "b"]),
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["replayed"])
        self.assertEqual(body["sealed_at"], sealed_at)

        # 不同词元 = 冲突，且保留原证据。
        status, body = req(
            "POST", f"{self.base}/v1/verdicts", self.body("X1", prods, ["a", "b"])
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error_code"], "audit_id_conflict")
        self.assertEqual(body["original_verdict"]["status"], "unique")

    def test_ambiguous_returns_two_trees(self):
        status, body = req(
            "POST",
            f"{self.base}/v1/verdicts",
            self.body(
                "X2",
                [
                    {"id": 1, "lhs": "S", "rhs": ["S", "PLUS", "S"]},
                    {"id": 2, "lhs": "S", "rhs": ["S", "STAR", "S"]},
                    {"id": 3, "lhs": "S", "rhs": ["a"]},
                ],
                ["a", "PLUS", "a", "STAR", "a"],
            ),
        )
        self.assertEqual(status, 200)
        verdict = body["verdict"]
        self.assertEqual(verdict["status"], "ambiguous")
        self.assertEqual(len(verdict["trees"]), 2)

    def test_non_consuming_loop_rejected(self):
        status, body = req(
            "POST",
            f"{self.base}/v1/verdicts",
            self.body(
                "X3",
                [
                    {"id": 1, "lhs": "S", "rhs": ["a", "A", "b"]},
                    {"id": 2, "lhs": "A", "rhs": ["A"]},
                    {"id": 3, "lhs": "A", "rhs": []},
                ],
                ["a", "b"],
            ),
        )
        self.assertEqual(status, 200)
        verdict = body["verdict"]
        self.assertEqual(verdict["status"], "reject")
        self.assertEqual(verdict["reason_code"], "nonproductive_loop")

    def test_illegal_grammar_400_first_reason(self):
        status, body = req(
            "POST",
            f"{self.base}/v1/verdicts",
            {
                "audit_id": "X4",
                "start": "S",
                "nonterminals": ["S", "A"],
                "productions": [{"id": 1, "lhs": "S", "rhs": ["A"]}],
                "tokens": [],
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["error_code"], "dangling_reference")

    def test_missing_audit_id_400(self):
        status, body = req(
            "POST",
            f"{self.base}/v1/verdicts",
            {"start": "S", "productions": [{"id": 1, "lhs": "S", "rhs": []}], "tokens": []},
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["error_code"], "malformed_audit_id")

    def test_malformed_json_400(self):
        request = urllib.request.Request(
            f"{self.base}/v1/verdicts",
            data=b"{not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=5)
            self.fail("expected 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
            self.assertEqual(json.loads(exc.read())["error_code"], "malformed_body")

    def test_get_unknown_and_replay_endpoint(self):
        status, _ = req("GET", f"{self.base}/v1/verdicts/NOPE")
        self.assertEqual(status, 404)

        status, body = req(
            "POST",
            f"{self.base}/v1/verdicts",
            self.body("X5", [{"id": 1, "lhs": "S", "rhs": []}], []),
        )
        self.assertEqual(status, 200)
        status, body = req("GET", f"{self.base}/v1/verdicts/X5")
        self.assertEqual(status, 200)
        self.assertTrue(body["replayed"])
        self.assertEqual(body["verdict"]["status"], "unique")

    def test_concurrent_first_submission_seals_once(self):
        prods = [{"id": 1, "lhs": "S", "rhs": []}]
        results = []

        def submit():
            results.append(
                req(
                    "POST",
                    f"{self.base}/v1/verdicts",
                    self.body("X6", prods, []),
                )
            )

        threads = [threading.Thread(target=submit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        statuses = sorted((s, b.get("replayed")) for s, b in results)
        self.assertTrue(all(s == 200 for s, _ in statuses))
        self.assertEqual(sum(1 for _, replayed in statuses if not replayed), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

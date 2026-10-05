"""文法引擎单元测试（标准库 unittest）。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

import engine  # noqa: E402


def payload(prods, tokens, start="S", nts=None):
    return {
        "start": start,
        "nonterminals": nts,
        "productions": prods,
        "tokens": tokens,
    }


def p(pid, lhs, rhs):
    return {"id": pid, "lhs": lhs, "rhs": rhs}


def leaves(tree):
    out = []
    for child in tree["children"]:
        if "terminal" in child:
            out.append(child["terminal"])
        else:
            out.extend(leaves(child))
    return out


class UniqueAcceptanceTests(unittest.TestCase):
    def test_nested_unique(self):
        r = engine.analyze(
            payload(
                [p(1, "S", ["a", "S", "b"]), p(2, "S", ["a", "b"])],
                ["a", "a", "b", "b"],
            )
        )
        self.assertEqual(r["status"], "unique")
        self.assertEqual(r["trees"][0]["production_sequence"], [1, 2])
        self.assertEqual(r["trees"][0]["span"], [0, 4])

    def test_direct_left_recursion_unique(self):
        r = engine.analyze(
            payload(
                [p(1, "S", ["S", "a"]), p(2, "S", ["a"])],
                ["a", "a", "a"],
            )
        )
        self.assertEqual(r["status"], "unique")
        self.assertEqual(r["trees"][0]["production_sequence"], [1, 1, 2])

    def test_indirect_left_recursion_unique(self):
        r = engine.analyze(
            payload(
                [
                    p(1, "S", ["A", "c"]),
                    p(2, "A", ["A", "a"]),
                    p(3, "A", ["B"]),
                    p(4, "B", ["b"]),
                ],
                ["b", "a", "c"],
            )
        )
        self.assertEqual(r["status"], "unique")
        self.assertEqual(r["trees"][0]["production_sequence"], [1, 2, 3, 4])

    def test_right_recursion_unique(self):
        r = engine.analyze(
            payload([p(1, "S", ["a", "S"]), p(2, "S", ["a"])], ["a", "a"])
        )
        self.assertEqual(r["status"], "unique")

    def test_epsilon_production_unique(self):
        r = engine.analyze(
            payload([p(1, "S", ["A", "b"]), p(2, "A", [])], ["b"])
        )
        self.assertEqual(r["status"], "unique")
        self.assertEqual(r["trees"][0]["production_sequence"], [1, 2])
        # ε 孩子的跨距为零宽。
        a_node = r["trees"][0]["children"][0]
        self.assertEqual(a_node["span"], [0, 0])
        self.assertEqual(a_node["children"], [])

    def test_empty_input_epsilon_unique(self):
        r = engine.analyze(payload([p(1, "S", [])], []))
        self.assertEqual(r["status"], "unique")
        self.assertEqual(r["trees"][0]["production_sequence"], [1])

    def test_unique_tree_spans_partition_input(self):
        # 顶层强制加法、加法分量可乘：a*a+a 只有一种解析。
        r = engine.analyze(
            payload(
                [
                    p(1, "S", ["A", "PLUS", "A"]),
                    p(2, "A", ["a", "STAR", "a"]),
                    p(3, "A", ["a"]),
                ],
                ["a", "STAR", "a", "PLUS", "a"],
            )
        )
        self.assertEqual(r["status"], "unique")
        tree = r["trees"][0]
        self._assert_well_formed(tree)

    def _assert_well_formed(self, tree, lo=0, hi=None, tokens=None):
        if hi is None:
            hi = tree["span"][1]
        i, j = tree["span"]
        self.assertEqual((i, j), (lo, hi))
        cur = i
        for child in tree["children"]:
            if "terminal" in child:
                self.assertEqual(child["span"], [cur, cur + 1])
                cur += 1
            else:
                ci, cj = child["span"]
                self.assertEqual(ci, cur)
                self._assert_well_formed(child, ci, cj, tokens)
                cur = cj
        self.assertEqual(cur, j)


class AmbiguityTests(unittest.TestCase):
    def test_classic_precedence_ambiguity_returns_two(self):
        r = engine.analyze(
            payload(
                [
                    p(1, "S", ["S", "PLUS", "S"]),
                    p(2, "S", ["S", "STAR", "S"]),
                    p(3, "S", ["a"]),
                ],
                ["a", "PLUS", "a", "STAR", "a"],
            )
        )
        self.assertEqual(r["status"], "ambiguous")
        self.assertEqual(len(r["trees"]), 2)
        for tree in r["trees"]:
            self.assertEqual(tree["span"], [0, 5])
            self.assertEqual(
                leaves(tree), ["a", "PLUS", "a", "STAR", "a"]
            )
        seqs = [tuple(t["production_sequence"]) for t in r["trees"]]
        self.assertEqual(list(seqs), sorted(seqs))
        self.assertNotEqual(seqs[0], seqs[1])

    def test_ss_ambiguity_huge_forest_not_enumerated(self):
        # Catalan(48) ≈ 10^26 棵解析，只取按编号序列稳定的前两棵。
        r = engine.analyze(
            payload([p(1, "S", ["S", "S"]), p(2, "S", ["a"])], ["a"] * 48)
        )
        self.assertEqual(r["status"], "ambiguous")
        self.assertEqual(len(r["trees"]), 2)
        seqs = [tuple(t["production_sequence"]) for t in r["trees"]]
        self.assertEqual(list(seqs), sorted(seqs))

    def test_epsilon_finite_ambiguity(self):
        # A -> eps 与 A -> A  B, B -> eps 是两棵不同的有限零宽树，但
        # 不存在环（B 不再依赖 A），应判歧义而非拒绝。
        r = engine.analyze(
            payload(
                [
                    p(1, "S", ["A"]),
                    p(2, "A", []),
                    p(3, "A", ["B"]),
                    p(4, "B", []),
                ],
                [],
            )
        )
        self.assertEqual(r["status"], "ambiguous")
        seqs = [tuple(t["production_sequence"]) for t in r["trees"]]
        self.assertEqual(list(seqs), sorted(seqs))
        self.assertEqual([list(s) for s in seqs], [[1, 2], [1, 3, 4]])

    def test_stability_order_independent_of_array_order(self):
        prods = [
            p(3, "S", ["a"]),
            p(1, "S", ["S", "PLUS", "S"]),
            p(2, "S", ["S", "STAR", "S"]),
        ]
        r = engine.analyze(payload(prods, ["a", "PLUS", "a", "STAR", "a"]))
        self.assertEqual(r["status"], "ambiguous")
        seqs = [tuple(t["production_sequence"]) for t in r["trees"]]
        self.assertEqual(list(seqs), sorted(seqs))

    def test_repeated_calls_are_deterministic(self):
        body = payload(
            [
                p(1, "S", ["S", "OR", "S"]),
                p(2, "S", ["S", "AND", "S"]),
                p(3, "S", ["a"]),
            ],
            ["a", "OR", "a", "AND", "a"],
        )
        first = engine.analyze(body)
        for _ in range(5):
            again = engine.analyze(body)
            self.assertEqual(
                [t["production_sequence"] for t in again["trees"]],
                [t["production_sequence"] for t in first["trees"]],
            )


class NonConsumingCycleTests(unittest.TestCase):
    def test_zero_self_loop_rejected(self):
        r = engine.analyze(
            payload(
                [p(1, "S", ["A"]), p(2, "A", ["A"]), p(3, "A", ["a"])],
                ["a"],
            )
        )
        self.assertEqual(r["status"], "reject")
        self.assertEqual(r["reason_code"], "nonproductive_loop")
        self.assertEqual(r["trees"], [])
        cycle = r["loop"]["cycle"]
        self.assertEqual(cycle[0], cycle[-1])

    def test_indirect_zero_cycle_rejected(self):
        r = engine.analyze(
            payload(
                [
                    p(1, "S", ["a", "B", "b"]),
                    p(2, "B", ["C"]),
                    p(3, "C", ["B"]),
                    p(4, "B", ["x"]),
                ],
                ["a", "x", "b"],
            )
        )
        self.assertEqual(r["status"], "reject")
        self.assertEqual(r["reason_code"], "nonproductive_loop")
        names = [step[0] for step in r["loop"]["cycle"]]
        self.assertEqual(names[0], names[-1])
        self.assertIn("B", names)
        self.assertIn("C", names)

    def test_epsilon_pump_amidst_terminals_rejected(self):
        r = engine.analyze(
            payload(
                [
                    p(1, "S", ["a", "A", "b"]),
                    p(2, "A", ["A"]),
                    p(3, "A", []),
                ],
                ["a", "b"],
            )
        )
        self.assertEqual(r["status"], "reject")
        self.assertEqual(r["reason_code"], "nonproductive_loop")

    def test_unreachable_zero_cycle_does_not_poison_parse(self):
        # A -> A | a；对 'a' 存在一元自包裹泵（A(0,1)->A(0,1)），
        # 必须拒绝。
        r = engine.analyze(
            payload(
                [p(1, "S", ["A"]), p(2, "A", ["A"]), p(3, "A", ["a"])],
                ["a"],
            )
        )
        self.assertEqual(r["status"], "reject")
        self.assertEqual(r["reason_code"], "nonproductive_loop")

        # 环存在于未被根引用的非终结符 X 中，不得毒化正常解析：
        # S -> a b 唯一，X -> X 不可达。
        r2 = engine.analyze(
            payload(
                [
                    p(1, "S", ["a", "b"]),
                    p(2, "X", ["X"]),
                    p(3, "X", ["x"]),
                ],
                ["a", "b"],
                nts=["S", "X"],
            )
        )
        self.assertEqual(r2["status"], "unique")

    def test_no_false_positive_on_dag_recursion(self):
        # S -> S S | a 是 DAG（每个孩子跨距严格变小），树数有限，
        # n=3 恰有两棵，判歧义而非环。
        r = engine.analyze(
            payload([p(1, "S", ["S", "S"]), p(2, "S", ["a"])], ["a", "a", "a"])
        )
        self.assertEqual(r["status"], "ambiguous")


class RejectInputTests(unittest.TestCase):
    def test_no_parse_reports_first_position(self):
        r = engine.analyze(payload([p(1, "S", ["a", "b"])], ["a", "c"]))
        self.assertEqual(r["status"], "reject")
        self.assertEqual(r["reason_code"], "no_parse")
        self.assertEqual(r["parse"]["position"], 1)
        self.assertIn("b", r["parse"]["expected_terminals"])

    def test_input_consumed_but_incomplete(self):
        r = engine.analyze(payload([p(1, "S", ["a", "b"])], ["a"]))
        self.assertEqual(r["status"], "reject")
        self.assertEqual(r["reason_code"], "no_parse")

    def test_empty_input_no_epsilon(self):
        r = engine.analyze(payload([p(1, "S", ["a"])], []))
        self.assertEqual(r["status"], "reject")
        self.assertEqual(r["parse"]["position"], 0)

    def test_terminal_not_in_grammar(self):
        r = engine.analyze(
            payload([p(1, "S", ["S", "a"]), p(2, "S", ["a"])], ["a", "z"])
        )
        self.assertEqual(r["status"], "reject")


class ValidationTests(unittest.TestCase):
    def _expect_code(self, body, code):
        with self.assertRaises(engine.GrammarError) as ctx:
            engine.analyze(body)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_illegal_symbol(self):
        self._expect_code(
            payload([p(1, "S", ["a", ""])], []), "malformed_symbol"
        )

    def test_non_ascii_symbol(self):
        self._expect_code(
            payload([p(1, "S", ["着陆"])], []), "malformed_symbol"
        )

    def test_dangling_reference(self):
        self._expect_code(
            payload([p(1, "S", ["A"])], [], nts=["S", "A"]),
            "dangling_reference",
        )

    def test_dangling_start(self):
        self._expect_code(
            payload([p(1, "S", ["a"])], [], start="Z", nts=["S", "Z"]),
            "start_has_no_production",
        )

    def test_start_not_declared(self):
        self._expect_code(
            payload([p(1, "S", ["a"])], [], start="Z", nts=["S"]),
            "dangling_start_symbol",
        )

    def test_duplicate_production_id(self):
        self._expect_code(
            payload([p(1, "S", ["a"]), p(1, "S", ["b"])], []),
            "duplicate_production_id",
        )

    def test_limits_enforced(self):
        self._expect_code(
            payload(
                [p(i + 1, "S", ["a"]) for i in range(engine.MAX_PRODUCTIONS + 1)],
                [],
            ),
            "too_many_productions",
        )
        self._expect_code(
            payload(
                [p(1, "S", ["a"])],
                ["a"] * (engine.MAX_TOKENS + 1),
            ),
            "too_many_tokens",
        )

    def test_first_actionable_reason_is_stable(self):
        # 同时存在多个问题时，返回结构校验阶段遇到的第一个。
        exc = self._expect_code(
            payload([p(1, "S", ["a"]), p(1, "S", ["b"])], ["a", 123]),
            "duplicate_production_id",
        )
        self.assertIsNotNone(exc.message)


class FingerprintTests(unittest.TestCase):
    def test_order_equivalence(self):
        a = payload(
            [p(1, "S", ["a"]), p(2, "S", ["b"])],
            ["a"],
        )
        b = payload(
            [p(2, "S", ["b"]), p(1, "S", ["a"])],
            ["a"],
        )
        self.assertEqual(
            engine.canonical_fingerprint(a), engine.canonical_fingerprint(b)
        )

    def test_token_difference_conflicts(self):
        a = payload([p(1, "S", ["a"])], ["a"])
        b = payload([p(1, "S", ["a"])], ["b"])
        self.assertNotEqual(
            engine.canonical_fingerprint(a), engine.canonical_fingerprint(b)
        )

    def test_rhs_difference_conflicts(self):
        a = payload([p(1, "S", ["a"])], ["a"])
        b = payload([p(1, "S", ["b"])], ["a"])
        self.assertNotEqual(
            engine.canonical_fingerprint(a), engine.canonical_fingerprint(b)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

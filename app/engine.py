"""深空着陆器文法判定引擎。

构造 Earley 共享解析森林（完成的产生式实例构成共享与/或图），在不
枚举全部推导的前提下判定：

* reject    —— 没有任何覆盖全部词元的起始符号有限派生，或存在可达的
  不消费词元循环（零宽度环可被泵出任意多次，不得伪装成证据）；
* unique    —— 恰有一棵有限派生树；
* ambiguous —— 至少两棵不同有限派生树，返回按产生式编号序列（深度
  优先先序）字典序稳定选出的前两棵。

关键事实：一棵派生树的形状与各节点跨距完全由“先序产生式编号序列”
决定（每个产生式的孩子数固定，终结符产量固定），因此两棵树不同当
且仅当其编号序列不同，整数元组上的字典序即为稳定总序。只取前两名
时，每个森林节点只需保留各自的前两名（k 最佳解析的局部性），无需
枚举推导。

只用标准库。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Optional

MAX_NONTERMINALS = 16
MAX_PRODUCTIONS = 32
MAX_TOKENS = 48

# 实例 DAG 上界约为 MAX_PRODUCTIONS * (MAX_TOKENS+1)^2，递归留出余量。
MAX_DEPTH = 200_000
sys.setrecursionlimit(max(10_000, MAX_DEPTH + 5_000))


class GrammarError(ValueError):
    """文法/请求非法；code 供调用方映射到首个可操作原因。"""

    def __init__(self, code: str, message: str, position: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.position = position

    def to_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"error_code": self.code, "message": self.message}
        if self.position is not None:
            detail["position"] = self.position
        return detail


@dataclass(frozen=True)
class Production:
    id: int
    lhs: str
    rhs: tuple[str, ...]


@dataclass(frozen=True)
class Grammar:
    start: str
    nonterminals: frozenset[str]
    by_id: dict[int, Production]
    # lhs -> 按编号升序的产生式列表
    prods: dict[str, list[Production]]


def _is_ascii_name(s: Any) -> bool:
    return isinstance(s, str) and len(s) > 0 and all(ord(c) < 128 for c in s)


def build_grammar(
    start: Any,
    nonterminals: Any,
    productions: Any,
) -> Grammar:
    """校验并构造文法。按固定顺序检查，抛出 *第一个* 可操作原因。"""
    if not _is_ascii_name(start):
        raise GrammarError(
            "malformed_start_symbol",
            "start must be a non-empty ASCII string",
        )

    declared_set: set[str] = set()
    if nonterminals is None:
        pass
    elif not isinstance(nonterminals, list):
        raise GrammarError(
            "malformed_nonterminals",
            "nonterminals must be a list of ASCII strings",
        )
    else:
        if len(nonterminals) > MAX_NONTERMINALS:
            raise GrammarError(
                "too_many_nonterminals",
                f"at most {MAX_NONTERMINALS} nonterminals are allowed",
            )
        for idx, name in enumerate(nonterminals):
            if not _is_ascii_name(name):
                raise GrammarError(
                    "malformed_nonterminal",
                    f"nonterminal at index {idx} must be a non-empty ASCII string",
                    position=idx,
                )
            declared_set.add(name)

    if not isinstance(productions, list):
        raise GrammarError(
            "malformed_productions",
            "productions must be a list of production objects",
        )
    if len(productions) == 0:
        raise GrammarError(
            "empty_production_set",
            "at least one production is required",
        )
    if len(productions) > MAX_PRODUCTIONS:
        raise GrammarError(
            "too_many_productions",
            f"at most {MAX_PRODUCTIONS} productions are allowed",
        )

    by_id: dict[int, Production] = {}
    prods: dict[str, list[Production]] = {}
    for idx, raw in enumerate(productions):
        if not isinstance(raw, dict):
            raise GrammarError(
                "malformed_production",
                f"production at index {idx} must be an object",
                position=idx,
            )
        pid = raw.get("id")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
            raise GrammarError(
                "malformed_production_id",
                f"production at index {idx} needs a positive integer id",
                position=idx,
            )
        if pid in by_id:
            raise GrammarError(
                "duplicate_production_id",
                f"production id {pid} is not unique",
                position=idx,
            )
        lhs = raw.get("lhs")
        if not _is_ascii_name(lhs):
            raise GrammarError(
                "malformed_lhs",
                f"production {pid} lhs must be a non-empty ASCII string",
                position=idx,
            )
        if declared_set and lhs not in declared_set:
            raise GrammarError(
                "dangling_lhs",
                f"production {pid} lhs '{lhs}' is not a declared nonterminal",
                position=idx,
            )
        rhs_raw = raw.get("rhs", [])
        if rhs_raw is None:
            rhs_raw = []
        if not isinstance(rhs_raw, list):
            raise GrammarError(
                "malformed_rhs",
                f"production {pid} rhs must be a list",
                position=idx,
            )
        rhs_syms: list[str] = []
        for sym in rhs_raw:
            if not _is_ascii_name(sym):
                raise GrammarError(
                    "malformed_symbol",
                    f"production {pid} contains an illegal symbol "
                    "(must be a non-empty ASCII string)",
                    position=idx,
                )
            rhs_syms.append(sym)

        by_id[pid] = Production(id=pid, lhs=lhs, rhs=tuple(rhs_syms))
        prods.setdefault(lhs, []).append(by_id[pid])

    if len(prods) > MAX_NONTERMINALS:
        raise GrammarError(
            "too_many_nonterminals",
            f"at most {MAX_NONTERMINALS} distinct nonterminals are allowed",
        )

    # 声明显式集合时以其为准；未声明时由左部推出。
    nt_set = declared_set if declared_set else set(prods.keys())
    if start not in nt_set:
        raise GrammarError(
            "dangling_start_symbol",
            f"start symbol '{start}' is not a declared nonterminal",
        )
    if start not in prods:
        raise GrammarError(
            "start_has_no_production",
            f"start symbol '{start}' has no production and cannot derive anything",
        )

    # 右部引用了没有任何产生式的非终结符 —— 悬空引用。
    referenced: dict[str, int] = {}
    for prod in by_id.values():
        for sym in prod.rhs:
            if sym in nt_set and sym not in prods and sym not in referenced:
                referenced[sym] = prod.id
    for sym, pid in referenced.items():
        raise GrammarError(
            "dangling_reference",
            f"nonterminal '{sym}' is used by production {pid} "
            "but has no production of its own",
        )

    for lhs in prods:
        prods[lhs].sort(key=lambda p: p.id)

    return Grammar(
        start=start,
        nonterminals=frozenset(nt_set),
        by_id=by_id,
        prods=prods,
    )


def earley(g: Grammar, tokens: tuple[str, ...]):
    """返回 (sets, finishes)。

    sets[k]     —— S_k 中状态 (pid, dot, start)，按加入顺序；
    finishes[j] —— 在 S_j 中完成的实例 (pid, start) 集合。

    每个 Earley 位置对每个非终结符只预测一次，从而驯服直接/间接左
    递归；完成步对结果集做索引遍历，ε 闭环在单趟内也能收敛。
    """
    n = len(tokens)
    states: list[list[tuple[int, int, int]]] = [[] for _ in range(n + 1)]
    seen: list[set[tuple[int, int, int]]] = [set() for _ in range(n + 1)]
    finishes: list[set[tuple[int, int]]] = [set() for _ in range(n + 1)]
    predicted: list[set[str]] = [set() for _ in range(n + 1)]

    def add(k: int, pid: int, dot: int, start: int) -> None:
        key = (pid, dot, start)
        if key not in seen[k]:
            seen[k].add(key)
            states[k].append(key)
            if dot == len(g.by_id[pid].rhs):
                finishes[k].add((pid, start))

    for prod in g.prods[g.start]:
        add(0, prod.id, 0, 0)

    for k in range(n + 1):
        idx = 0
        while idx < len(states[k]):
            pid, dot, start = states[k][idx]
            idx += 1
            prod = g.by_id[pid]
            if dot < len(prod.rhs):
                sym = prod.rhs[dot]
                if sym in g.prods:
                    if sym not in predicted[k]:
                        predicted[k].add(sym)
                        for q in g.prods[sym]:
                            add(k, q.id, 0, k)
                elif k < n and tokens[k] == sym:
                    add(k + 1, pid, dot + 1, start)
            else:
                lhs = prod.lhs
                target = states[start]
                q = 0
                while q < len(target):
                    qpid, qdot, qstart = target[q]
                    q += 1
                    qprod = g.by_id[qpid]
                    if qdot < len(qprod.rhs) and qprod.rhs[qdot] == lhs:
                        add(k, qpid, qdot + 1, qstart)

    return states, finishes


class Forest:
    """共享解析森林：门控边、环检测与 k=2 取树共用同一份位向量表。

    Earley 的完成实例 (pid, i, j) 必有一棵有限派生树（每个非终结符
    在每个位置只预测一次，纯环无法自举完成）。本类再做三件事：

    1. 预算位向量表
         can[B][v]   —— B 能覆盖到的终点 b 的集合；
         cov[(p,j)]  —— 产生式 p 右部后缀 [pos:] 覆盖 tokens[v:j]
                        的可行 v 集合（按 pos 的位向量数组）；
         pre[(p,i)]  —— 产生式 p 右部前缀 [:pos] 覆盖 tokens[i:v]
                        的可行 v 集合。
       于是“孩子 (B,v,b) 能出现在实例 (p,i,j) 的某棵完成树中”当且
       仅当 v ∈ pre_p[pos]∩cov_p[pos] 且 b ∈ can[B][v]∩cov_p[pos+1]。

    2. 在被门控的或节点图 (A,i,j) 上找环。图上的环回到同一跨距，
       泵出一次不消费任何新词元 —— 即可无限展开，必须明确拒绝。

    3. 在共享森林上取按先序产生式编号字典序的前两棵不同树
       （k 最佳局部性：每个节点只留前两名），不枚举全部推导。
    """

    def __init__(self, g: Grammar, finishes, tokens: tuple[str, ...]):
        self.g = g
        self.finishes = finishes
        self.tokens = tokens
        n = self.n = len(tokens)

        # can[nt][v] : int bitset of end positions b.
        self.can: dict[str, list[int]] = {
            nt: [0] * (n + 1) for nt in g.nonterminals
        }
        for b in range(n + 1):
            for pid, start in finishes[b]:
                self.can[g.by_id[pid].lhs][start] |= 1 << b

        self._cov_cache: dict[tuple[int, int], list[int]] = {}
        self._pre_cache: dict[tuple[int, int], list[int]] = {}

        self.root_instances: list[tuple[int, int, int]] = []
        for prod in g.prods[g.start]:
            if (prod.id, 0) in finishes[n]:
                self.root_instances.append((prod.id, 0, n))

        # 或节点图边：(A,i,j) -> 被门控的孩子或节点集合。
        self.or_edges: dict[
            tuple[str, int, int], set[tuple[str, int, int]]
        ] = {}
        seen_inst: set[tuple[int, int, int]] = set()
        stack = list(self.root_instances)
        while stack:
            pid, i, j = stack.pop()
            if (pid, i, j) in seen_inst:
                continue
            seen_inst.add((pid, i, j))
            prod = g.by_id[pid]
            node = (prod.lhs, i, j)
            edge_set = self.or_edges.setdefault(node, set())
            pre = self._prefix_bits(pid, i)
            cov = self._cover_bits(pid, j)
            for pos, sym in enumerate(prod.rhs):
                if sym not in g.prods:
                    continue
                starts = pre[pos] & cov[pos]
                tail = cov[pos + 1]
                while starts:
                    bit = starts & -starts
                    v = bit.bit_length() - 1
                    starts -= bit
                    ends = self.can[sym][v] & tail
                    while ends:
                        ebit = ends & -ends
                        b = ebit.bit_length() - 1
                        ends -= ebit
                        child = (sym, v, b)
                        edge_set.add(child)
                        for q in g.prods[sym]:
                            if (q.id, v) in finishes[b]:
                                cinst = (q.id, v, b)
                                if cinst not in seen_inst:
                                    stack.append(cinst)

    # ------------------------------------------------------------------
    # 位向量 DP
    # ------------------------------------------------------------------
    def _cover_bits(self, pid: int, j: int) -> list[int]:
        """cov[pos]：第 pos 个 bit=1 当且仅当右部 [pos:] 覆盖 tokens[v:j]。"""
        key = (pid, j)
        cached = self._cov_cache.get(key)
        if cached is not None:
            return cached
        rhs = self.g.by_id[pid].rhs
        n = self.n
        bits = [0] * (len(rhs) + 1)
        bits[len(rhs)] = 1 << j
        for pos in range(len(rhs) - 1, -1, -1):
            sym = rhs[pos]
            tail = bits[pos + 1]
            acc = 0
            if sym in self.g.prods:
                canrow = self.can[sym]
                for v in range(0, j + 1):
                    if canrow[v] & tail:
                        acc |= 1 << v
            else:
                for v in range(0, j):
                    if self.tokens[v] == sym and ((tail >> (v + 1)) & 1):
                        acc |= 1 << v
            bits[pos] = acc
        self._cov_cache[key] = bits
        return bits

    def _prefix_bits(self, pid: int, i: int) -> list[int]:
        """pre[pos]：第 v 个 bit=1 当且仅当右部 [:pos] 覆盖 tokens[i:v]。"""
        key = (pid, i)
        cached = self._pre_cache.get(key)
        if cached is not None:
            return cached
        rhs = self.g.by_id[pid].rhs
        n = self.n
        bits = [0] * (len(rhs) + 1)
        bits[0] = 1 << i
        for pos, sym in enumerate(rhs):
            cur = bits[pos]
            nxt = 0
            if sym in self.g.prods:
                canrow = self.can[sym]
                while cur:
                    bit = cur & -cur
                    v = bit.bit_length() - 1
                    cur -= bit
                    nxt |= canrow[v]
            else:
                while cur:
                    bit = cur & -cur
                    v = bit.bit_length() - 1
                    cur -= bit
                    if v < n and self.tokens[v] == sym:
                        nxt |= 1 << (v + 1)
            bits[pos + 1] = nxt
        self._pre_cache[key] = bits
        return bits

    # ------------------------------------------------------------------
    # 环检测
    # ------------------------------------------------------------------
    def find_cycle(self) -> Optional[list[list[Any]]]:
        """稳定序下返回第一条环 [[符号,起点,终点], ...]（首尾节点相同）。"""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {node: WHITE for node in self.or_edges}
        parent: dict[
            tuple[str, int, int], Optional[tuple[str, int, int]]
        ] = {}

        def dfs(start: tuple[str, int, int]):
            color[start] = GRAY
            parent[start] = None
            dfs_stack: list[tuple[tuple[str, int, int], int]] = [(start, 0)]
            while dfs_stack:
                node, edge_idx = dfs_stack[-1]
                neighbors = sorted(self.or_edges.get(node, ()))
                if edge_idx < len(neighbors):
                    dfs_stack[-1] = (node, edge_idx + 1)
                    nxt = neighbors[edge_idx]
                    if color[nxt] == WHITE:
                        color[nxt] = GRAY
                        parent[nxt] = node
                        dfs_stack.append((nxt, 0))
                    elif color[nxt] == GRAY:
                        path = [nxt]
                        cur: Optional[tuple[str, int, int]] = node
                        while cur is not None and cur != nxt:
                            path.append(cur)
                            cur = parent.get(cur)
                        path.append(nxt)
                        path.reverse()
                        return [[nt, a, b] for nt, a, b in path]
                else:
                    color[node] = BLACK
                    dfs_stack.pop()
            return None

        for node in sorted(self.or_edges):
            if color[node] == WHITE:
                cycle = dfs(node)
                if cycle is not None:
                    return cycle
        return None

    # ------------------------------------------------------------------
    # k=2 取树
    # ------------------------------------------------------------------
    def top2_trees(self) -> list[dict[str, Any]]:
        g = self.g
        finishes = self.finishes
        tokens = self.tokens

        @dataclass(frozen=True)
        class KTree:
            pids: tuple[int, ...]
            children: tuple[Any, ...]

        memo: dict[tuple[Any, ...], tuple[KTree, ...]] = {}
        visiting: set[tuple[Any, ...]] = set()

        def take2(candidates: list[KTree]) -> tuple[KTree, ...]:
            candidates.sort(key=lambda t: t.pids)
            result: list[KTree] = []
            seen_pids: set[tuple[int, ...]] = set()
            for cand in candidates:
                if cand.pids not in seen_pids:
                    seen_pids.add(cand.pids)
                    result.append(cand)
                    if len(result) == 2:
                        break
            return tuple(result)

        def suffix_trees(
            pid: int, j: int, pos: int, v: int
        ) -> tuple[KTree, ...]:
            """产生式 pid 右部 [pos:] 覆盖 tokens[v:j] 的前两名。"""
            key = (True, pid, j, pos, v)
            if key in memo:
                return memo[key]
            if key in visiting:  # 环已在枚举前拒绝；防御性兜底
                raise GrammarError(
                    "nonproductive_loop",
                    "reachable non-consuming derivation cycle detected",
                )
            visiting.add(key)
            rhs = g.by_id[pid].rhs
            candidates: list[KTree] = []

            if pos == len(rhs):
                if v == j:
                    candidates.append(KTree((), ()))
            else:
                sym = rhs[pos]
                if sym in g.prods:
                    # 用共享森林的位向量门控：b 必须既是 sym 真实
                    # 可覆盖的终点，又允许后缀补到 j。这样 S->S S
                    # 中空孩子之类的死边不会递归进入。
                    allowed = self.can[sym][v] & self._cover_bits(pid, j)[pos + 1]
                    b = v
                    while allowed:
                        ebit = allowed & -allowed
                        b = ebit.bit_length() - 1
                        allowed -= ebit
                        heads = or_trees(sym, v, b)
                        if not heads:
                            continue
                        tails = suffix_trees(pid, j, pos + 1, b)
                        for head in heads:
                            for tail in tails:
                                candidates.append(
                                    KTree(
                                        head.pids + tail.pids,
                                        head.children + tail.children,
                                    )
                                )
                elif v < j and tokens[v] == sym:
                    for tail in suffix_trees(pid, j, pos + 1, v + 1):
                        leaf = {"terminal": sym, "span": [v, v + 1]}
                        candidates.append(
                            KTree(tail.pids, (leaf,) + tail.children)
                        )

            visiting.discard(key)
            memo[key] = take2(candidates)
            return memo[key]

        def or_trees(nt: str, i: int, j: int) -> tuple[KTree, ...]:
            key = (False, nt, i, j)
            if key in memo:
                return memo[key]
            if key in visiting:
                raise GrammarError(
                    "nonproductive_loop",
                    "reachable non-consuming derivation cycle detected",
                )
            visiting.add(key)
            candidates: list[KTree] = []
            for q in g.prods[nt]:  # 产生式编号升序：并列时稳定
                if (q.id, i) not in finishes[j]:
                    continue
                for suffix in suffix_trees(q.id, j, 0, i):
                    node = {
                        "prod_id": q.id,
                        "lhs": nt,
                        "span": [i, j],
                        "children": list(suffix.children),
                    }
                    candidates.append(
                        KTree((q.id,) + suffix.pids, (node,))
                    )
            visiting.discard(key)
            memo[key] = take2(candidates)
            return memo[key]

        if not self.root_instances:
            return []
        roots = or_trees(g.start, 0, self.n)
        return [cand.children[0] for cand in roots]


def _stall_reason(
    g: Grammar,
    states: list[list[tuple[int, int, int]]],
    tokens: tuple[str, ...],
) -> dict[str, Any]:
    """给出首个可操作的拒绝位置与期望终结符。"""
    n = len(tokens)
    last = n
    while last > 0 and not states[last]:
        last -= 1
    expected: set[str] = set()
    for pid, dot, _start in states[last]:
        prod = g.by_id[pid]
        if dot < len(prod.rhs):
            sym = prod.rhs[dot]
            if sym not in g.prods:
                expected.add(sym)
    detail: dict[str, Any] = {"position": last}
    if expected:
        detail["expected_terminals"] = sorted(expected)
        detail["message"] = (
            f"no derivation covers the input; stalled at token position {last}, "
            f"expected one of {sorted(expected)}"
        )
    elif last == n:
        detail["message"] = "input consumed but the start symbol is not complete"
    else:
        detail["message"] = (
            f"no derivation covers the input; no rule can continue "
            f"at token position {last}"
        )
    return detail


def _validate_tokens(tokens: Any) -> tuple[str, ...]:
    if tokens is None:
        return ()
    if not isinstance(tokens, list):
        raise GrammarError(
            "malformed_tokens", "tokens must be a list of ASCII strings"
        )
    if len(tokens) > MAX_TOKENS:
        raise GrammarError(
            "too_many_tokens", f"at most {MAX_TOKENS} tokens are allowed"
        )
    for idx, tok in enumerate(tokens):
        if not _is_ascii_name(tok):
            raise GrammarError(
                "malformed_token",
                f"token at index {idx} must be a non-empty ASCII string",
                position=idx,
            )
    return tuple(tokens)


def _preorder_pids(tree: dict[str, Any]) -> list[int]:
    result = [tree["prod_id"]]
    for child in tree["children"]:
        if "prod_id" in child:
            result.extend(_preorder_pids(child))
    return result


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    """判定入口。合法输入返回结论 dict；非法输入抛 GrammarError。"""
    if not isinstance(payload, dict):
        raise GrammarError("malformed_request", "request body must be an object")

    g = build_grammar(
        payload.get("start"),
        payload.get("nonterminals"),
        payload.get("productions"),
    )
    token_tuple = _validate_tokens(payload.get("tokens", []))

    states, finishes = earley(g, token_tuple)
    n = len(token_tuple)

    if not any((p.id, 0) in finishes[n] for p in g.prods[g.start]):
        detail = _stall_reason(g, states, token_tuple)
        return {
            "status": "reject",
            "reason_code": "no_parse",
            "reason": detail.pop("message"),
            "parse": detail,
            "trees": [],
        }

    forest = Forest(g, finishes, token_tuple)
    cycle = forest.find_cycle()
    if cycle is not None:
        return {
            "status": "reject",
            "reason_code": "nonproductive_loop",
            "reason": (
                "reachable non-consuming derivation cycle detected; the cycle "
                "returns to the same span, pumps infinitely many expansions, "
                "and no finite tree is evidence"
            ),
            "loop": {"cycle": cycle},
            "trees": [],
        }

    trees = forest.top2_trees()
    for tree in trees:
        tree["production_sequence"] = _preorder_pids(tree)
    if len(trees) >= 2:
        return {
            "status": "ambiguous",
            "reason_code": None,
            "reason": "two or more distinct finite derivations exist",
            "trees": trees,
        }
    return {
        "status": "unique",
        "reason_code": None,
        "reason": "exactly one finite derivation exists",
        "trees": trees,
    }


def canonical_fingerprint(payload: dict[str, Any]) -> str:
    """语义指纹：只取决于 (起始符号, 产生式, 词元)。

    产生式数组顺序无关（按编号与右部规范化）；非终结符声明列表仅用于
    校验，未使用的额外声明不改变可派生语言，故不计入指纹。
    """
    prods = payload.get("productions") or []
    normalized = sorted(
        (p.get("id"), p.get("lhs"), tuple(p.get("rhs") or []))
        for p in prods
    )
    return repr(
        (
            payload.get("start"),
            tuple((pid, lhs, rhs) for pid, lhs, rhs in normalized),
            tuple(payload.get("tokens") or []),
        )
    )

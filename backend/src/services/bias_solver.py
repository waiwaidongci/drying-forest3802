"""计时器偏差极值的精确求解。

口径
----
计时器读数 = 真实时长 + 偏差 b，即真实时长 t = 读数 m - b。
样本合格当且仅当 lower <= m - b <= upper（含两端），等价于
    m - upper <= b <= m - lower。

约束
----
1. 每只计时器的偏差落在各自校准区间 [bias_min, bias_max]；
2. 同一事件两表同测，差值 b_a - b_b 落在 [diff_min, diff_max]。

约束全部是一维差分约束 x_v - x_u <= w，构成差分约束系统。
Bellman-Ford 检测负环：出现负环即校准矛盾（无解），
否则源点最短路距离给出一组可行偏差。

极值
----
每个试件只属于一只计时器，合格总数可分解为 Σ f_t(b_t)，
其中 f_t 只取决于本计时器偏差。把每只计时器的校准盒按本批次
样本产生的端点切成单元（端点单点 + 相邻端点间开区间），
单元内 f_t 恒定。分支定界两次 DFS：
- 最小化：用各单元贡献的下包络做下界剪枝；
- 最大化：用上包络做上界剪枝。
叶节点分别评估边界 witness 与单元内部 witness（含两端口径）。
"""
import math

EPS = 1e-9


class CalibrationConflict(Exception):
    """差分约束系统存在负环，校准自相矛盾，不报件数。"""


class SolverBudgetError(Exception):
    """可行域组合规模超过分支定界预算，无法在限额内证明精确极值。"""


def _base_edges(box, pairs, timer_ids):
    """构造基础差分约束边 (u, v, w)：b_v - b_u <= w。节点 0 为虚拟源。"""
    idx = {t: i + 1 for i, t in enumerate(timer_ids)}
    edges = []
    for t in timer_ids:
        i = idx[t]
        lo, hi = box[t]
        edges.append((0, i, float(hi)))    # b_t <= hi
        edges.append((i, 0, -float(lo)))   # -b_t <= -lo
    for a, b, dmin, dmax in pairs:
        ia, ib = idx[a], idx[b]
        edges.append((ib, ia, float(dmax)))    # b_a - b_b <= dmax
        edges.append((ia, ib, -float(dmin)))   # b_b - b_a <= -dmin
    return edges, idx


def _bellman_ford(edges, n, extra=None):
    """单源 Bellman-Ford（全 0 初值等价于引入超级源）。

    返回各节点最短路距离；存在负环返回 None。
    extra: {timer_node: (lo, hi)} 追加的单元盒约束。
    """
    all_edges = list(edges)
    if extra:
        for node, (lo, hi) in extra.items():
            all_edges.append((0, node, float(hi)))
            all_edges.append((node, 0, -float(lo)))
    dist = [math.inf] * n
    dist[0] = 0.0
    # 以虚拟节点 0 为显式源跑满 n-1 轮：收敛后 dist[t] 即可行偏差 b_t
    for _ in range(n - 1):
        for u, v, w in all_edges:
            if dist[u] != math.inf:
                nd = dist[u] + w
                if nd < dist[v]:
                    dist[v] = nd
    if any(dist[u] != math.inf and dist[u] + w < dist[v] - 1e-10 for u, v, w in all_edges):
        return None
    return dist


def _witness(dist, idx, effective_box, timer_ids):
    """源点最短路距离取可行偏差，再夹回有效盒（吸收浮点累积误差）。"""
    bias = {}
    for t in timer_ids:
        lo, hi = effective_box[t]
        bias[t] = min(max(float(dist[idx[t]]), lo), hi)
    return bias


def _verify(bias, box, pairs):
    """显式复验全部校准盒与两表差值约束。"""
    for t, (lo, hi) in box.items():
        if not lo - EPS <= bias[t] <= hi + EPS:
            return False
    for a, b, dmin, dmax in pairs:
        d = bias[a] - bias[b]
        if not dmin - EPS <= d <= dmax + EPS:
            return False
    return True


def _cells(specimen_for_timer, lo, hi):
    """切分单元：[(lo, hi, is_point, contribution_at)]。

    贡献在开区间内部恒定；端点单点处可能有更多试件合格（含两端）。
    """
    cuts = sorted({round(x, 10) for sp in specimen_for_timer
                   for x in (sp["blo"], sp["bhi"]) if lo - EPS <= x <= hi + EPS})
    points = sorted({round(float(lo), 10), round(float(hi), 10), *cuts})

    def contribution(b):
        return sum(1 for sp in specimen_for_timer
                   if sp["blo"] - EPS <= b <= sp["bhi"] + EPS)

    cells = []
    for i, p in enumerate(points):
        c_point = contribution(p)
        cells.append((p, p, True, c_point, c_point))
        if i + 1 < len(points) and points[i + 1] - p > EPS:
            q = points[i + 1]
            c_open = contribution((p + q) / 2.0)
            # 单元闭盒上的贡献包络：边界点只会比内部多、不会少
            c_hi = max(c_point, contribution(q), c_open)
            c_lo = min(c_point, contribution(q), c_open)
            cells.append((p, q, False, c_lo, c_hi))
    return cells


def solve(samples, timers, pair_rules):
    """求解一批试件的合格件数最小/最大值。

    samples: [{"reference","timer_id","reading","lower","upper"}]
    timers:  {timer_id: {"name","bias_min","bias_max"}}
    pair_rules: [{"timer_a","timer_b","diff_min","diff_max"}]

    返回 dict；校准矛盾时抛 CalibrationConflict。
    """
    timer_ids = sorted(timers)
    if len(timer_ids) > 6:
        raise SolverBudgetError("单次分析最多支持 6 只计时器；当前 %d 只，请拆分批次" % len(timer_ids))
    box = {t: (float(timers[t]["bias_min"]), float(timers[t]["bias_max"])) for t in timer_ids}
    for t, (lo, hi) in box.items():
        if hi < lo - EPS:
            raise CalibrationConflict("计时器 %s 的校准范围上下限颠倒" % t)
    pairs = [(p["timer_a"], p["timer_b"], float(p["diff_min"]), float(p["diff_max"]))
             for p in pair_rules]
    for p in pair_rules:
        if float(p["diff_max"]) < float(p["diff_min"]) - EPS:
            raise CalibrationConflict("两表差值范围上下限颠倒：%s / %s"
                                      % (p["timer_a"], p["timer_b"]))

    edges, idx = _base_edges(box, pairs, timer_ids)
    n_nodes = len(timer_ids) + 1
    if _bellman_ford(edges, n_nodes) is None:
        raise CalibrationConflict("校准约束自相矛盾：差分约束系统存在负环，不存在可行偏差")

    specimen = []
    by_timer = {t: [] for t in timer_ids}
    for s in samples:
        t = s["timer_id"]
        if t not in box:
            raise CalibrationConflict("样本 %s 引用了未登记的计时器 %s" % (s["reference"], t))
        lo = float(s["reading"]) - float(s["upper"])
        hi = float(s["reading"]) - float(s["lower"])
        if hi < lo - EPS:
            raise CalibrationConflict("样本 %s 的合格区间上下限颠倒" % s["reference"])
        sp = {"reference": s["reference"], "timer_id": t,
              "reading": float(s["reading"]),
              "lower": float(s["lower"]), "upper": float(s["upper"]),
              "blo": lo, "bhi": hi}
        specimen.append(sp)
        by_timer[t].append(sp)
    total = len(specimen)

    cells = {t: _cells(by_timer[t], *box[t]) for t in timer_ids}
    # 单元少的计时器先分支，剪枝更早生效
    order = sorted(timer_ids, key=lambda t: len(cells[t]))
    per_timer_min = {t: min(c[3] for c in cells[t]) for t in timer_ids}
    per_timer_max = {t: max(c[4] for c in cells[t]) for t in timer_ids}

    feasible_cache = {}

    def feasible(assigned, shrink):
        """assigned: {timer: (lo,hi,is_point)}；shrink 收紧开区间单元探内部。"""
        extra = {}
        widths = []
        for t, (rlo, rhi, is_point) in assigned.items():
            if shrink and not is_point:
                widths.append(rhi - rlo)
                extra[idx[t]] = (rlo, rhi)  # 占位，下面统一收紧
            else:
                extra[idx[t]] = (rlo, rhi)
        if shrink and widths:
            delta = min(widths) * 1e-6
            extra = {}
            for t, (rlo, rhi, is_point) in assigned.items():
                extra[idx[t]] = (rlo + delta, rhi - delta) if not is_point else (rlo, rhi)
        key = (tuple(sorted(extra.items())), shrink)
        if key not in feasible_cache:
            feasible_cache[key] = _bellman_ford(edges, n_nodes, extra) is not None
        return feasible_cache[key]

    def evaluate(bias):
        count = 0
        outside = []
        for sp in specimen:
            true_minutes = sp["reading"] - bias[sp["timer_id"]]
            if sp["lower"] - EPS <= true_minutes <= sp["upper"] + EPS:
                count += 1
            else:
                outside.append({"reference": sp["reference"],
                                "timer_id": sp["timer_id"],
                                "reading": sp["reading"],
                                "true_minutes": true_minutes,
                                "lower": sp["lower"], "upper": sp["upper"]})
        return count, outside

    def search(seek):
        """seek='min' 求最小合格件数，seek='max' 求最大。"""
        best = total + 1 if seek == "min" else -1
        leaf = None
        budget = [120000]  # BF 可行性检查预算，防止病态规模下组合失控

        def bound(assigned_cells):
            """部分单元选择下合格总数的紧包络。"""
            lo_sum = hi_sum = 0
            for t, cell in assigned_cells.items():
                lo_sum += cell[3]
                hi_sum += cell[4]
            for t in timer_ids:
                if t not in assigned_cells:
                    lo_sum += per_timer_min[t]
                    hi_sum += per_timer_max[t]
            return lo_sum, hi_sum

        def greedy_seed():
            """沿每计时器最有利单元贪心走一条可行路径，先拿到一个完整可行解收紧界。"""
            chosen = {}
            flat = {}
            for depth in range(len(order)):
                t = order[depth]
                ranked = sorted(cells[t], key=lambda c: c[3] if seek == "min" else -c[4])
                picked = None
                for cell in ranked:
                    trial = dict(chosen)
                    trial[t] = cell
                    flat_trial = {x: (c[0], c[1], c[2]) for x, c in trial.items()}
                    if feasible(flat_trial, False):
                        picked = cell
                        break
                if picked is None:
                    return None
                chosen[t] = picked
                flat[t] = (picked[0], picked[1], picked[2])
            dist = _bellman_ford(edges, n_nodes,
                                 {idx[t]: (c[0], c[1]) for t, c in chosen.items()})
            if dist is None:
                return None
            bias = _witness(dist, idx, {t: (c[0], c[1]) for t, c in chosen.items()}, timer_ids)
            if not _verify(bias, box, pairs):
                return None
            count, outside = evaluate(bias)
            return {"bias": bias, "outside": outside, "count": count}

        seed_leaf = greedy_seed()
        if seed_leaf is not None:
            best = seed_leaf["count"]
            leaf = seed_leaf

        def dfs(depth, assigned_cells):
            nonlocal best, leaf
            if budget[0] <= 0:
                return
            if depth:
                if not feasible({t: (c[0], c[1], c[2]) for t, c in assigned_cells.items()}, False):
                    return
                budget[0] -= 1
            lb, ub = bound(assigned_cells)
            if seek == "min" and lb >= best:
                return
            if seek == "max" and ub <= best:
                return
            if depth == len(order):
                flat = {t: (c[0], c[1], c[2]) for t, c in assigned_cells.items()}
                cell_box = {t: (c[0], c[1]) for t, c in assigned_cells.items()}
                # 边界 witness：闭盒上的可行偏差
                dist = _bellman_ford(edges, n_nodes,
                                     {idx[t]: v for t, v in cell_box.items()})
                if dist is not None:
                    bias = _witness(dist, idx, cell_box, timer_ids)
                    if _verify(bias, box, pairs):
                        count, outside = evaluate(bias)
                        improved = (seek == "min" and count < best) or (seek == "max" and count > best)
                        if improved:
                            best, leaf = count, {"bias": bias, "outside": outside, "count": count}
                # 内部 witness：开区间单元收紧后仍可行，内部计数恒定
                if feasible(flat, True):
                    shrink_box = dict(box)
                    extra = {}
                    widths = [c[1] - c[0] for c in assigned_cells.values() if not c[2]]
                    delta = (min(widths) if widths else 1.0) * 1e-6
                    for t, c in assigned_cells.items():
                        if not c[2]:
                            shrink_box[t] = (c[0] + delta, c[1] - delta)
                            extra[idx[t]] = shrink_box[t]
                        else:
                            extra[idx[t]] = (c[0], c[1])
                    dist_inner = _bellman_ford(edges, n_nodes, extra)
                    if dist_inner is not None:
                        bias = _witness(dist_inner, idx, shrink_box, timer_ids)
                        if _verify(bias, box, pairs):
                            count, outside = evaluate(bias)
                            improved = (seek == "min" and count < best) or (seek == "max" and count > best)
                            if improved:
                                best, leaf = count, {"bias": bias, "outside": outside, "count": count}
                return
            t = order[depth]
            ranked = sorted(cells[t], key=lambda c: (c[3] if seek == "min" else -c[4], c[0]))
            for cell in ranked:
                if budget[0] <= 0:
                    break
                assigned_cells[t] = cell
                dfs(depth + 1, assigned_cells)
            assigned_cells.pop(t, None)

        dfs(0, {})
        if leaf is None:
            raise CalibrationConflict("分支定界未找到可行单元组合")
        if budget[0] <= 0:
            raise SolverBudgetError("可行域规模过大，分支定界预算内未取得精确极值；请减少计时器数量或合并样本后重试")
        return leaf

    leaf_min = search("min")
    leaf_max = search("max")

    def payload(leaf):
        return {
            "count": leaf["count"],
            "bias": [{"timer_id": t, "bias": leaf["bias"][t]} for t in timer_ids],
            "outside_window": leaf["outside"],
        }

    return {
        "feasible": True,
        "sample_total": total,
        "timer_count": len(timer_ids),
        "minimum": payload(leaf_min),
        "maximum": payload(leaf_max),
    }

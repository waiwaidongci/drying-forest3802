"""bias_solver 扩展 oracle：更大实例、随机矛盾实例与确定性校验。"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

from src.services.bias_solver import solve, CalibrationConflict, EPS


def random_feasible(rng, n_timers, n_samples):
    true_bias = {t: rng.uniform(-4, 4) for t in range(1, n_timers + 1)}
    timers = {}
    for t, b in true_bias.items():
        w = rng.uniform(1.0, 5.0)
        timers[t] = {"name": f"T{t}",
                     "bias_min": b - w, "bias_max": b + w}
    pairs = []
    for a in range(1, n_timers + 1):
        for b in range(a + 1, n_timers + 1):
            if rng.random() < 0.75:
                d = true_bias[a] - true_bias[b]
                w = rng.uniform(0.5, 4.0)
                pairs.append({"timer_a": a, "timer_b": b,
                              "diff_min": d - w, "diff_max": d + w})
    samples = []
    for i in range(n_samples):
        t = rng.randint(1, n_timers)
        true_t = rng.uniform(40, 120)
        reading = true_t + true_bias[t]
        center = true_t + rng.gauss(0, 4)
        half = rng.uniform(2, 16)
        samples.append({"reference": f"S{i:04d}", "timer_id": t,
                        "reading": reading,
                        "lower": center - half, "upper": center + half})
    return timers, pairs, samples, true_bias


def count_for_bias(samples, bias):
    return sum(1 for s in samples
               if s["lower"] - EPS <= s["reading"] - bias[s["timer_id"]] <= s["upper"] + EPS)


def feasible_bias(timers, pairs, bias):
    for t, c in timers.items():
        if not c["bias_min"] - EPS <= bias[t] <= c["bias_max"] + EPS:
            return False
    for p in pairs:
        d = bias[p["timer_a"]] - bias[p["timer_b"]]
        if not p["diff_min"] - EPS <= d <= p["diff_max"] + EPS:
            return False
    return True


def grid_oracle(timers, pairs, samples, rng):
    tids = sorted(timers)
    candidates = {}
    for t in tids:
        pts = {timers[t]["bias_min"], timers[t]["bias_max"]}
        for s in samples:
            if s["timer_id"] == t:
                pts.add(s["reading"] - s["lower"])
                pts.add(s["reading"] - s["upper"])
        candidates[t] = sorted(x for x in pts
                               if timers[t]["bias_min"] - EPS <= x <= timers[t]["bias_max"] + EPS)
    lo_n, hi_n = None, None
    budget = {"n": 0}
    limit = 200000

    def walk(i, bias):
        nonlocal lo_n, hi_n
        if budget["n"] >= limit:
            return
        if i == len(tids):
            budget["n"] += 1
            if feasible_bias(timers, pairs, bias):
                c = count_for_bias(samples, bias)
                lo_n = c if lo_n is None else min(lo_n, c)
                hi_n = c if hi_n is None else max(hi_n, c)
            return
        t = tids[i]
        for x in candidates[t]:
            bias[t] = x
            walk(i + 1, bias)

    walk(0, {})
    # 端点网格可能漏掉只存在于开区间内部的可行点（或预算耗尽），用拒绝采样兜底
    tids = sorted(timers)
    for _ in range(60000):
        bias = {t: rng.uniform(timers[t]["bias_min"], timers[t]["bias_max"]) for t in tids}
        if feasible_bias(timers, pairs, bias):
            c = count_for_bias(samples, bias)
            lo_n = c if lo_n is None else min(lo_n, c)
            hi_n = c if hi_n is None else max(hi_n, c)
    return lo_n, hi_n


def main():
    rng = random.Random(9917)
    failures = 0
    for case in range(40):
        n_timers = rng.randint(2, 5)
        n_samples = rng.randint(20, 90)
        timers, pairs, samples, true_bias = random_feasible(rng, n_timers, n_samples)
        # 真实偏差必然可行，求解器不得报矛盾
        res = solve(samples, timers, pairs)
        lo_n, hi_n = grid_oracle(timers, pairs, samples, rng)
        # 真实偏差计数必须落在极值区间内
        true_count = count_for_bias(samples, true_bias)
        for key in ("minimum", "maximum"):
            bias = {x["timer_id"]: x["bias"] for x in res[key]["bias"]}
            assert feasible_bias(timers, pairs, bias), f"case {case} infeasible witness"
            assert count_for_bias(samples, bias) == res[key]["count"]
            assert len(res[key]["outside_window"]) == n_samples - res[key]["count"]
        ok = (res["minimum"]["count"] <= lo_n <= hi_n <= res["maximum"]["count"]
              and res["minimum"]["count"] <= true_count <= res["maximum"]["count"])
        if not ok:
            print(f"case {case} FAIL solver=[{res['minimum']['count']},{res['maximum']['count']}] "
                  f"grid=[{lo_n},{hi_n}] true={true_count}")
            failures += 1
        # 确定性：同输入重复求解结果一致
        res2 = solve(samples, timers, pairs)
        assert res == res2, f"case {case} nondeterministic"
    print("feasible large cases:", 40 - failures, "/ 40")

    # 随机矛盾实例：盒不相交且差值强制相等/反向
    conflicts_detected = 0
    for k in range(20):
        t = rng.randint(2, 4)
        timers = {}
        bases = sorted(rng.sample(range(-10, 10, 2), t))
        for i, b in enumerate(bases, start=1):
            timers[i] = {"name": f"T{i}", "bias_min": float(b), "bias_max": float(b + 0.5)}
        # 强制全部相邻表差值为 0，但校准盒互不相交 -> 必矛盾
        pairs = [{"timer_a": i, "timer_b": i + 1, "diff_min": 0.0, "diff_max": 0.0}
                 for i in range(1, t)]
        samples = [{"reference": "X", "timer_id": 1, "reading": 60.0,
                    "lower": 50.0, "upper": 70.0}]
        try:
            solve(samples, timers, pairs)
        except CalibrationConflict:
            conflicts_detected += 1
    print("conflict cases detected:", conflicts_detected, "/ 20")

    # 矛盾时不报件数：返回结构中不得出现 count 字段
    bad = {1: {"name": "A", "bias_min": 0.0, "bias_max": 1.0},
           2: {"name": "B", "bias_min": 5.0, "bias_max": 6.0}}
    try:
        solve([{"reference": "X", "timer_id": 1, "reading": 50.0,
                "lower": 40.0, "upper": 60.0}], bad,
              [{"timer_a": 1, "timer_b": 2, "diff_min": 0.0, "diff_max": 0.0}])
        print("explicit conflict: FAIL")
        failures += 1
    except CalibrationConflict as e:
        print("explicit conflict raised:", str(e)[:40])

    print("FAILURES:", failures)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

"""bias_solver 独立 oracle：随机小实例，用密集随机采样与网格枚举核对极值。"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

from src.services.bias_solver import solve, CalibrationConflict, EPS


def random_instance(rng, n_timers, n_samples, force_feasible=True):
    timers = {}
    for t in range(1, n_timers + 1):
        c = rng.uniform(-3, 3)
        w = rng.uniform(0.5, 4.0)
        timers[t] = {"name": f"T{t}", "bias_min": c - w, "bias_max": c + w}
    pairs = []
    for a in range(1, n_timers + 1):
        for b in range(a + 1, n_timers + 1):
            if rng.random() < 0.7:
                if force_feasible:
                    lo = timers[a]["bias_min"] - timers[b]["bias_max"] - rng.uniform(0, 2)
                    hi = timers[a]["bias_max"] - timers[b]["bias_min"] + rng.uniform(0, 2)
                else:
                    lo, hi = sorted((rng.uniform(-6, 6), rng.uniform(-6, 6)))
                pairs.append({"timer_a": a, "timer_b": b, "diff_min": lo, "diff_max": hi})
    samples = []
    for i in range(n_samples):
        t = rng.randint(1, n_timers)
        reading = rng.uniform(30, 120)
        lo = reading - rng.uniform(2, 25)
        hi = lo + rng.uniform(2, 20)
        samples.append({"reference": f"S{i:03d}", "timer_id": t,
                        "reading": reading, "lower": lo, "upper": hi})
    return timers, pairs, samples


def count_for_bias(samples, bias):
    n = 0
    for s in samples:
        true_minutes = s["reading"] - bias[s["timer_id"]]
        if s["lower"] - EPS <= true_minutes <= s["upper"] + EPS:
            n += 1
    return n


def box_feasible(timers, pairs, bias):
    for t, cfg in timers.items():
        if not cfg["bias_min"] - EPS <= bias[t] <= cfg["bias_max"] + EPS:
            return False
    for p in pairs:
        d = bias[p["timer_a"]] - bias[p["timer_b"]]
        if not p["diff_min"] - EPS <= d <= p["diff_max"] + EPS:
            return False
    return True


def oracle_enumerate(timers, pairs, samples, rng, trials=40000):
    """拒绝采样 + 盒内网格，返回观测到的 (min,max) 与对应偏差。"""
    tids = list(timers)
    obs_min, obs_max = None, None
    for _ in range(trials):
        bias = {t: rng.uniform(timers[t]["bias_min"], timers[t]["bias_max"]) for t in tids}
        if not box_feasible(timers, pairs, bias):
            continue
        c = count_for_bias(samples, bias)
        obs_min = c if obs_min is None else min(obs_min, c)
        obs_max = c if obs_max is None else max(obs_max, c)
    # 网格补充：沿每个样本端点的候选点取值
    candidates = {}
    for t in tids:
        pts = {timers[t]["bias_min"], timers[t]["bias_max"]}
        for s in samples:
            if s["timer_id"] == t:
                pts.add(s["reading"] - s["lower"])
                pts.add(s["reading"] - s["upper"])
        candidates[t] = sorted(x for x in pts if timers[t]["bias_min"] - EPS <= x <= timers[t]["bias_max"] + EPS)

    def grid(i, bias):
        nonlocal obs_min, obs_max
        if i == len(tids):
            if box_feasible(timers, pairs, bias):
                c = count_for_bias(samples, bias)
                obs_min = c if obs_min is None else min(obs_min, c)
                obs_max = c if obs_max is None else max(obs_max, c)
            return
        t = tids[i]
        for x in candidates[t]:
            bias[t] = x
            grid(i + 1, bias)

    grid(0, {})
    return obs_min, obs_max


def main():
    rng = random.Random(20261119)
    failures = 0
    for case in range(60):
        n_timers = rng.randint(2, 3)
        n_samples = rng.randint(4, 14)
        timers, pairs, samples = random_instance(rng, n_timers, n_samples)
        try:
            res = solve(samples, timers, pairs)
        except CalibrationConflict:
            # 强制可行实例不应冲突
            print(f"case {case}: unexpected conflict")
            failures += 1
            continue
        omin, omax = oracle_enumerate(timers, pairs, samples, rng)
        # 校验返回的证据偏差本身可行且计数自洽
        for key in ("minimum", "maximum"):
            ext = res[key]
            bias = {item["timer_id"]: item["bias"] for item in ext["bias"]}
            assert box_feasible(timers, pairs, bias), f"case {case} {key} witness infeasible"
            assert count_for_bias(samples, bias) == ext["count"], f"case {case} {key} count mismatch"
            assert len(ext["outside_window"]) == res["sample_total"] - ext["count"]
        assert res["minimum"]["count"] <= res["maximum"]["count"]
        # oracle 观测值必须落在求解器给出的区间内
        if not (res["minimum"]["count"] <= omin <= omax <= res["maximum"]["count"]):
            print(f"case {case}: solver [{res['minimum']['count']},{res['maximum']['count']}] "
                  f"oracle [{omin},{omax}] timers={n_timers} samples={n_samples}")
            failures += 1
        else:
            print(f"case {case}: ok solver=[{res['minimum']['count']},{res['maximum']['count']}] "
                  f"oracle=[{omin},{omax}] T={n_timers} N={n_samples}")

    # 显式构造一个矛盾实例：两个计时器校准盒不相交，却要求差值为 0
    bad_timers = {1: {"name": "A", "bias_min": 0, "bias_max": 1},
                  2: {"name": "B", "bias_min": 5, "bias_max": 6}}
    bad_pairs = [{"timer_a": 1, "timer_b": 2, "diff_min": 0, "diff_max": 0}]
    bad_samples = [{"reference": "X", "timer_id": 1, "reading": 50, "lower": 40, "upper": 60}]
    try:
        solve(bad_samples, bad_timers, bad_pairs)
        print("conflict case: FAIL (no exception)")
        failures += 1
    except CalibrationConflict:
        print("conflict case: ok (CalibrationConflict raised)")

    # 边界含两端：单计时器，单样本，真实时长恰好在边界上必须算合格
    edge_timers = {1: {"name": "A", "bias_min": -5, "bias_max": 5}}
    edge_samples = [{"reference": "E1", "timer_id": 1, "reading": 55, "lower": 50, "upper": 60},
                    {"reference": "E2", "timer_id": 1, "reading": 65, "lower": 50, "upper": 60}]
    res = solve(edge_samples, edge_timers, [])
    # b=5 -> E1 真实50（下边界，合格）；b=-5 -> E2 真实70 不合格、E1 真实60（上边界，合格）
    assert res["minimum"]["count"] == 1 and res["maximum"]["count"] == 2, res
    print(f"edge case: ok min={res['minimum']['count']} max={res['maximum']['count']}")

    print("FAILURES:", failures)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

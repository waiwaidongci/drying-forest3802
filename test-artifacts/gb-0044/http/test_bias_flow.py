"""计时器偏差模块 HTTP 全流程集成测试（SQLite，无需 Redis/PG）。"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

_db = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_URL"] = "sqlite:///" + _db
os.environ["SEED_DEMO"] = "false"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"
os.environ["JWT_SECRET"] = "test-secret"

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402
from src.config.database import Base, engine, Session  # noqa: E402
from src.models.user import User  # noqa: E402
from src.models.bias import BiasAnalysis, Timer  # noqa: E402
from src.utils.security import hash_password  # noqa: E402

Base.metadata.create_all(engine)
client = TestClient(app)

with Session() as db:
    for role in ("admin", "analyst", "viewer"):
        db.add(User(email=role + "@x.demo", password_hash=hash_password("pw123456"), role=role))
    db.commit()


def token(role):
    r = client.post("/auth/login", json={"email": role + "@x.demo", "password": "pw123456"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


admin = token("admin")
analyst = token("analyst")
viewer = token("viewer")
H = {"Authorization": "Bearer " + analyst}
HV = {"Authorization": "Bearer " + viewer}
HA = {"Authorization": "Bearer " + admin}
failures = []


def check(name, cond, extra=""):
    print(("ok  " if cond else "FAIL") + " " + name + ("" if cond else " " + str(extra)))
    if not cond:
        failures.append(name)


# 1. 匿名 401、viewer 写 403
r = client.get("/bias/timers")
check("anonymous 401", r.status_code == 401, r.status_code)
r = client.post("/bias/timers", headers=HV, json={"code": "X", "name": "x", "bias_min": 0, "bias_max": 1})
check("viewer write 403", r.status_code == 403, r.status_code)

# 2. 建两只计时器 + 差值规则
r = client.post("/bias/timers", headers=H, json={"code": "TM-A", "name": "甲表", "bias_min": -3, "bias_max": 3})
check("create timer A", r.status_code == 200, r.text)
id_a = r.json()["id"]
r = client.post("/bias/timers", headers=H, json={"code": "TM-B", "name": "乙表", "bias_min": -2, "bias_max": 2})
id_b = r.json()["id"]
r = client.post("/bias/timers", headers=H, json={"code": "TM-A", "name": "重复", "bias_min": 0, "bias_max": 1})
check("duplicate timer 409", r.status_code == 409, r.status_code)
r = client.post("/bias/pair-rules", headers=H,
                json={"timer_a_id": id_a, "timer_b_id": id_b, "diff_min": -1.5, "diff_max": 1.5})
check("pair rule 200", r.status_code == 200, r.text)
r = client.post("/bias/pair-rules", headers=H,
                json={"timer_a_id": id_a, "timer_b_id": id_a, "diff_min": 0, "diff_max": 0})
check("pair same timer 422", r.status_code == 422, r.status_code)

# 3. 非法校准 422
r = client.post("/bias/timers", headers=H, json={"code": "BAD", "name": "bad", "bias_min": 5, "bias_max": 1})
check("inverted box 422", r.status_code == 422, r.status_code)
r = client.post("/bias/timers", headers=H, json={"code": "BAD2", "name": "bad", "bias_min": "x", "bias_max": 1})
check("non-numeric 422", r.status_code == 422, r.status_code)

# 4. 补录试件
specs = [
    {"reference": "SP-001", "timer_id": id_a, "reading": 55, "lower": 50, "upper": 60},
    {"reference": "SP-002", "timer_id": id_a, "reading": 70, "lower": 50, "upper": 60},
    {"reference": "SP-003", "timer_id": id_b, "reading": 58, "lower": 50, "upper": 60},
    {"reference": "SP-004", "timer_id": id_b, "reading": 45, "lower": 50, "upper": 60},
]
r = client.post("/bias/specimens", headers=H, json={"batch_key": "B1", "specimens": specs})
check("add specimens", r.status_code == 200 and r.json()["added"] == 4, r.text)
r = client.post("/bias/specimens", headers=H,
                json={"batch_key": "B1", "specimens": [specs[0]]})
check("duplicate specimen 409", r.status_code == 409, r.status_code)
r = client.post("/bias/specimens", headers=H,
                json={"batch_key": "B1", "specimens": [{**specs[0], "reference": "SP-X", "upper": 1}]})
check("inverted window 422", r.status_code == 422, r.status_code)
r = client.post("/bias/specimens", headers=H,
                json={"batch_key": "B1", "specimens": [{**specs[0], "reference": "SP-Y", "timer_id": 9999}]})
check("unknown timer 404", r.status_code == 404, r.status_code)

# 5. 创建分析：极值 + 可行偏差证据 + 超窗明细
r = client.post("/bias/analyses", headers=H,
                json={"name": "首批偏差极值", "batch_key": "B1", "window_label": "50-60min"})
check("create analysis 200", r.status_code == 200, r.text)
a1 = r.json()
check("analysis completed", a1["state"] == "completed", a1["state"])
res = a1["result"]
check("has min/max", res and "minimum" in res and "maximum" in res)
check("min<=max", res["minimum"]["count"] <= res["maximum"]["count"])
for key in ("minimum", "maximum"):
    ext = res[key]
    check(key + " witness size", len(ext["bias"]) == 2)
    check(key + " outside count", len(ext["outside_window"]) == 4 - ext["count"])
    # 证据偏差可行且计数自洽
    bias_map = {int(b["timer_id"]): b["bias"] for b in ext["bias"]}
    for t_id, b in bias_map.items():
        t = next(x for x in a1["snapshot"]["timers"] if x["id"] == t_id)
        check(key + " bias in box", t["bias_min"] - 1e-9 <= b <= t["bias_max"] + 1e-9)
    d = bias_map[id_a] - bias_map[id_b]
    check(key + " pair diff", -1.5 - 1e-9 <= d <= 1.5 + 1e-9, d)
    # 用证据偏差重算合格数，必须与返回 count 一致
    recomputed = 0
    for sp in a1["snapshot"]["specimens"]:
        true_minutes = sp["reading"] - bias_map[sp["timer_id"]]
        if sp["lower"] - 1e-9 <= true_minutes <= sp["upper"] + 1e-9:
            recomputed += 1
    check(key + " count self-consistent", recomputed == ext["count"], (recomputed, ext["count"]))
a1_id = a1["id"]
snap_before = a1["snapshot"]

# 6. 冻结：补录 + 改校准后，旧分析快照不变
client.post("/bias/specimens", headers=H, json={"batch_key": "B1", "specimens": [
    {"reference": "SP-005", "timer_id": id_a, "reading": 100, "lower": 50, "upper": 60}]})
client.put("/bias/timers/%d" % id_a, headers=H,
           json={"code": "TM-A", "name": "甲表-改", "bias_min": -1, "bias_max": 1})
r = client.get("/bias/analyses/%d" % a1_id, headers=H)
a1_re = r.json()
check("snapshot frozen", a1_re["snapshot"] == snap_before)
check("result immutable", a1_re["result"] == res)
with Session() as db:
    t = db.get(Timer, id_a)
    check("timer version bumped", t.version == 2 and t.bias_min == -1, t.version)

# 7. 新分析使用新校准
r = client.post("/bias/analyses", headers=H, json={"name": "改校准后", "batch_key": "B1"})
check("new analysis after recalibration", r.status_code == 200 and r.json()["id"] != a1_id)
check("new snapshot has v2", any(x["id"] == id_a and x["version"] == 2
                                 for x in r.json()["snapshot"]["timers"]))

# 8. 校准矛盾：新建两只永不相交的表并强制差值 0
r = client.post("/bias/timers", headers=H, json={"code": "C1", "name": "c1", "bias_min": 0, "bias_max": 1})
c1 = r.json()["id"]
r = client.post("/bias/timers", headers=H, json={"code": "C2", "name": "c2", "bias_min": 5, "bias_max": 6})
c2 = r.json()["id"]
client.post("/bias/pair-rules", headers=H, json={"timer_a_id": c1, "timer_b_id": c2, "diff_min": 0, "diff_max": 0})
client.post("/bias/specimens", headers=H, json={"batch_key": "BC", "specimens": [
    {"reference": "C-X", "timer_id": c1, "reading": 55, "lower": 50, "upper": 60}]})
r = client.post("/bias/analyses", headers=H, json={"name": "矛盾批次", "batch_key": "BC"})
ac = r.json()
check("conflict state", ac["state"] == "conflict", ac["state"])
check("conflict no counts", ac["result"] is None and bool(ac["conflict_reason"]))
r = client.post("/bias/analyses/%d/publish" % ac["id"], headers=H, json={"publish": True})
check("conflict not publishable 409", r.status_code == 409, r.status_code)

# 9. 发布 / 幂等 / 撤销 / 中断恢复
r = client.post("/bias/analyses/%d/publish" % a1_id, headers=H, json={"publish": True})
check("publish 200", r.status_code == 200 and r.json()["publish_state"] == "published", r.text)
r = client.post("/bias/analyses/%d/publish" % a1_id, headers=H, json={"publish": True})
check("republish idempotent", r.status_code == 200 and r.json()["publish_state"] == "published")
n_analyses = len(client.get("/bias/analyses", headers=H).json())
client.post("/bias/analyses/%d/publish" % a1_id, headers=H, json={"publish": True})
check("republish no new analysis", len(client.get("/bias/analyses", headers=H).json()) == n_analyses)
r = client.post("/bias/analyses/%d/publish" % a1_id, headers=H, json={"publish": False})
check("unpublish", r.json()["publish_state"] == "unpublished")

# 模拟发布中断：直接把状态置为 interrupted
with Session() as db:
    obj = db.get(BiasAnalysis, a1_id)
    obj.publish_state = "interrupted"
    db.commit()
r = client.post("/bias/analyses/%d/recover" % a1_id, headers=H)
check("recover interrupted", r.status_code == 200 and r.json()["publish_state"] == "unpublished", r.text)
# 结论仍在
r = client.get("/bias/analyses/%d" % a1_id, headers=H)
check("conclusion preserved after recover", r.json()["result"] == res)
r = client.post("/bias/analyses/%d/recover" % a1_id, headers=H)
check("recover non-interrupted 409", r.status_code == 409)

# 10. recompute 不新增分析、结论一致
r = client.post("/bias/analyses/%d/recompute" % a1_id, headers=HA)
check("recompute ok", r.status_code == 200 and r.json()["id"] == a1_id and r.json()["result"] == res)
check("recompute no new analysis", len(client.get("/bias/analyses", headers=H).json()) == n_analyses)

# 11. 审计留痕
r = client.get("/audit", headers=H)
actions = {e["action"] for e in r.json()}
expected = {"timer.created", "pair_rule.created", "bias_specimens.added",
            "bias_analysis.queued", "bias_analysis.completed", "bias_analysis.conflict",
            "bias_analysis.published", "bias_analysis.recovered", "bias_analysis.recompute"}
check("audit trail", expected.issubset(actions), expected - actions)

print("\nFAILURES:", len(failures), failures)
sys.exit(1 if failures else 0)

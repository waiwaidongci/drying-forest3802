"""并发补录 × 冻结分析：验证行锁下快照一致、无脏读、无异常。"""
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

_db = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_URL"] = "sqlite:///" + _db
os.environ["SEED_DEMO"] = "false"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"
os.environ["JWT_SECRET"] = "test-secret"

from src.config.database import Base, engine, Session  # noqa: E402
from src.main import app  # noqa: E402
from src.models.user import User  # noqa: E402
from src.models.bias import BiasAnalysis, BiasSpecimen  # noqa: E402
from src.utils.security import hash_password  # noqa: E402
from src.services import bias as bias_service  # noqa: E402

Base.metadata.create_all(engine)


class U:
    email = "analyst@x.demo"


with Session() as db:
    db.add(User(email=U.email, password_hash=hash_password("pw"), role="analyst"))
    db.commit()

# 两只计时器 + 差值规则
with Session() as db:
    from src.models.bias import Timer, TimerPairRule
    t1 = Timer(code="K1", name="k1", bias_min=-3, bias_max=3, version=1, updated_at="t")
    t2 = Timer(code="K2", name="k2", bias_min=-3, bias_max=3, version=1, updated_at="t")
    db.add_all([t1, t2])
    db.flush()
    db.add(TimerPairRule(timer_a_id=t1.id, timer_b_id=t2.id,
                         diff_min=-2, diff_max=2, version=1, updated_at="t"))
    db.commit()
    id1, id2 = t1.id, t2.id

errors = []
lock = threading.Lock()


def add_batch(i):
    try:
        with Session() as db:
            from src.schemas.bias import SpecimenBatch, SpecimenInput
            data = SpecimenBatch(batch_key="CB", specimens=[
                SpecimenInput(reference=f"C-{i:03d}-A", timer_id=id1,
                              reading=50 + (i % 20), lower=45, upper=70),
                SpecimenInput(reference=f"C-{i:03d}-B", timer_id=id2,
                              reading=52 + (i % 18), lower=45, upper=70),
            ])
            bias_service.add_specimens(db, U, data)
    except Exception as e:
        with lock:
            errors.append(("add", i, str(e)))


def freeze(i):
    try:
        with Session() as db:
            from src.schemas.bias import AnalysisInput
            data = AnalysisInput(name=f"并发分析{i}", batch_key="CB")
            a = bias_service.create_analysis(db, U, data)
            # 快照内部一致：试件数与快照一致，且快照含计时器版本
            n_spec = len(a["snapshot"]["specimens"])
            assert n_spec >= 2
            assert len(a["snapshot"]["timers"]) >= 2
            assert a["state"] in ("completed", "conflict", "failed")
            if a["state"] == "completed":
                r = a["result"]
                assert 0 <= r["minimum"]["count"] <= r["maximum"]["count"] <= n_spec
    except Exception as e:
        # 批次尚未有任何已提交试件时，冻结被明确拒绝（422）属预期行为
        if "还没有试件" in str(e):
            with lock:
                errors.append(("expected-empty", i, str(e)))
        else:
            with lock:
                errors.append(("freeze", i, str(e)))


with ThreadPoolExecutor(max_workers=8) as pool:
    jobs = []
    for i in range(20):
        jobs.append(pool.submit(add_batch, i))
        if i % 2 == 0:
            jobs.append(pool.submit(freeze, i))
    for j in jobs:
        j.result()

hard_errors = [e for e in errors if e[0] != "expected-empty"]
print("hard errors:", hard_errors, "| expected empty-batch rejects:",
      sum(1 for e in errors if e[0] == "expected-empty"))
assert not hard_errors, hard_errors

with Session() as db:
    total_spec = len(bias_service.list_specimens(db, "CB"))
    analyses = db.query(BiasAnalysis).order_by(BiasAnalysis.id).all()
    print("specimens:", total_spec, "analyses:", len(analyses))
    # 每个分析的快照都是某个已提交时刻的完整一致切片：
    # 计数不超过最终总数、编号不重复、且都是已落库试件的子集
    counts = [len(a.snapshot["specimens"]) for a in analyses]
    print("frozen specimen counts:", counts)
    final_refs = {s.reference for s in db.query(BiasSpecimen).filter_by(batch_key="CB").all()}
    for a in analyses:
        refs = [s["reference"] for s in a.snapshot["specimens"]]
        assert len(refs) == len(set(refs)), "快照内编号重复"
        assert set(refs).issubset(final_refs), "快照引用了未提交试件（脏读）"
        assert len(refs) <= total_spec
print("CONCURRENCY OK")

"""计时器偏差分析业务服务。

冻结口径
--------
分析提交时把试件与当时的计时器校准（含版本）整体快照保存；
之后的补录或改校准只影响新分析，历史分析按快照重算，结论不漂移。

并发口径
--------
补录试件与改校准共用 PostgreSQL 行级事务锁：
写操作先对相关计时器行 SELECT ... FOR UPDATE 串行化，
分析创建在同一事务内冻结，保证“冻结的就是已提交的最新版”。

发布口径
--------
极值随分析持久化；发布是独立阶段。发布中断（进程异常/请求失败）
只停在 unpublished/interrupted，已保存结论可一键恢复原研究，
重试发布不新增分析、不新增结论。
"""
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from ..config.database import Session, engine
from ..models.bias import Timer, TimerPairRule, BiasSpecimen, BiasAnalysis
from ..utils.serialization import row, now
from .bias_solver import solve, CalibrationConflict, SolverBudgetError
from .audit import log


def _lock_timers(db, timer_ids):
    """对计时器行加事务锁串行化写操作。

    PostgreSQL 用 SELECT ... FOR UPDATE 行锁；SQLite 为库级写锁，
    FOR UPDATE 无对应语法，直接普通查询（单写者语义已足够测试）。
    """
    if not timer_ids:
        return []
    stmt = select(Timer).where(Timer.id.in_(timer_ids))
    if engine.dialect.name == 'postgresql':
        stmt = stmt.with_for_update()
    return db.scalars(stmt).all()


# ---------- 计时器台账 ----------

def list_timers(db):
    timers = [row(t) for t in db.scalars(select(Timer).order_by(Timer.id))]
    rules = [row(r) for r in db.scalars(select(TimerPairRule).order_by(TimerPairRule.id))]
    return {'timers': timers, 'pair_rules': rules}


def _timer_or_404(db, timer_id):
    t = db.get(Timer, timer_id)
    if not t:
        raise HTTPException(404, '未找到计时器')
    return t


def save_timer(db, user, data, timer_id=None):
    """新增或修改校准。修改即递增版本，旧分析快照不受影响。"""
    if timer_id is None:
        t = Timer(code=data.code.strip(), name=data.name.strip(),
                  bias_min=data.bias_min, bias_max=data.bias_max,
                  version=1, updated_at=now())
        db.add(t)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, '计时器编号已存在：' + data.code)
        log(db, user, 'timer.created', t.id, t.code)
        db.commit()
        return row(t)

    t = _timer_or_404(db, timer_id)
    # 行锁串行化并发改校准
    if engine.dialect.name == 'postgresql':
        db.scalar(select(Timer).where(Timer.id == timer_id).with_for_update())
    changed = (abs(t.bias_min - data.bias_min) > 0 or abs(t.bias_max - data.bias_max) > 0
               or t.name != data.name.strip())
    t.name = data.name.strip()
    t.bias_min = data.bias_min
    t.bias_max = data.bias_max
    if changed:
        t.version += 1
    t.updated_at = now()
    log(db, user, 'timer.updated', t.id, 'v%s' % t.version)
    db.commit()
    return row(t)


def save_pair_rule(db, user, data):
    if data.timer_a_id == data.timer_b_id:
        raise HTTPException(422, '两表同测规则必须选择两只不同计时器')
    a = _timer_or_404(db, data.timer_a_id)
    b = _timer_or_404(db, data.timer_b_id)
    # 锁两只计时器，与改校准/补录互斥
    _lock_timers(db, (a.id, b.id))
    existing = db.scalar(select(TimerPairRule).where(
        TimerPairRule.timer_a_id == a.id, TimerPairRule.timer_b_id == b.id))
    if existing:
        existing.diff_min = data.diff_min
        existing.diff_max = data.diff_max
        existing.version += 1
        existing.updated_at = now()
        target = existing.id
        action = 'pair_rule.updated'
    else:
        existing = TimerPairRule(timer_a_id=a.id, timer_b_id=b.id,
                                 diff_min=data.diff_min, diff_max=data.diff_max,
                                 version=1, updated_at=now())
        db.add(existing)
        db.flush()
        target = existing.id
        action = 'pair_rule.created'
    log(db, user, action, target, '%s/%s' % (a.code, b.code))
    db.commit()
    return row(existing)


# ---------- 试件补录 ----------

def add_specimens(db, user, data):
    refs = set()
    for s in data.specimens:
        if s.reference in refs:
            raise HTTPException(422, '批内试件编号重复：' + s.reference)
        refs.add(s.reference)
    # 锁相关计时器行：与改校准互斥，保证冻结看到的是已提交校准
    timer_ids = sorted({s.timer_id for s in data.specimens})
    _lock_timers(db, timer_ids)
    for tid in timer_ids:
        _timer_or_404(db, tid)
    existing = set(db.scalars(select(BiasSpecimen.reference).where(
        BiasSpecimen.batch_key == data.batch_key,
        BiasSpecimen.reference.in_(refs))))
    if existing:
        raise HTTPException(409, '试件编号已存在：' + next(iter(existing)))
    rows = [BiasSpecimen(batch_key=data.batch_key, reference=s.reference,
                         timer_id=s.timer_id, reading=s.reading,
                         lower=s.lower, upper=s.upper) for s in data.specimens]
    db.add_all(rows)
    log(db, user, 'bias_specimens.added', data.batch_key, str(len(rows)))
    db.commit()
    return {'added': len(rows)}


def list_specimens(db, batch_key):
    return [row(x) for x in db.scalars(
        select(BiasSpecimen).where(BiasSpecimen.batch_key == batch_key).order_by(BiasSpecimen.id))]


def list_batches(db):
    rows = db.execute(select(BiasSpecimen.batch_key,
                             func.count(BiasSpecimen.id)).group_by(BiasSpecimen.batch_key)).all()
    return [{'batch_key': k, 'specimen_count': c} for k, c in sorted(rows)]


# ---------- 分析：冻结 + 求解 ----------

def _build_snapshot(db, batch_key):
    specimens = list_specimens(db, batch_key)
    if not specimens:
        raise HTTPException(422, '该批次还没有试件，无法冻结分析')
    involved = {s['timer_id'] for s in specimens}
    # 纳入所有“触及”本批计时器的两表规则（含另一端计时器），避免漏掉跨批约束
    rules = [row(r) for r in db.scalars(select(TimerPairRule).where(
        (TimerPairRule.timer_a_id.in_(involved)) | (TimerPairRule.timer_b_id.in_(involved))))]
    timer_ids = set(involved)
    for r in rules:
        timer_ids.add(r['timer_a_id'])
        timer_ids.add(r['timer_b_id'])
    timers = [row(t) for t in db.scalars(select(Timer).where(Timer.id.in_(timer_ids)))]
    snapshot = {
        'batch_key': batch_key,
        'specimens': specimens,
        'timers': timers,
        'pair_rules': rules,
        'frozen_at': now(),
    }
    snapshot['checksum'] = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return snapshot


def create_analysis(db, user, data):
    # 锁本批涉及的全部计时器，与补录/改校准互斥后再冻结
    specimen_count = db.scalar(select(func.count()).select_from(BiasSpecimen).where(
        BiasSpecimen.batch_key == data.batch_key))
    if not specimen_count:
        raise HTTPException(422, '该批次还没有试件，无法冻结分析')
    timer_ids = [x for (x,) in db.execute(select(BiasSpecimen.timer_id).where(
        BiasSpecimen.batch_key == data.batch_key).distinct()).all()]
    _lock_timers(db, timer_ids)

    snapshot = _build_snapshot(db, data.batch_key)
    analysis = BiasAnalysis(name=data.name, batch_key=data.batch_key,
                            window_label=data.window_label, state='queued',
                            snapshot=snapshot, created_by=user.email,
                            created_at=now(), publish_state='unpublished')
    db.add(analysis)
    db.flush()
    log(db, user, 'bias_analysis.queued', analysis.id, data.name)
    db.commit()
    return _compute(analysis.id)


def _compute(analysis_id):
    """独立可重入：按冻结快照求解。矛盾时持久化矛盾原因，不报件数。"""
    with Session() as db:
        analysis = db.get(BiasAnalysis, analysis_id)
        if not analysis:
            raise HTTPException(404, '未找到偏差分析')
        analysis.state = 'running'
        db.commit()
        snap = analysis.snapshot
        timers = {str(t['id']): {'name': t['name'],
                                 'bias_min': t['bias_min'], 'bias_max': t['bias_max']}
                  for t in snap['timers']}
        samples = [{'reference': s['reference'], 'timer_id': str(s['timer_id']),
                    'reading': s['reading'], 'lower': s['lower'], 'upper': s['upper']}
                   for s in snap['specimens']]
        pair_rules = [{'timer_a': str(r['timer_a_id']), 'timer_b': str(r['timer_b_id']),
                       'diff_min': r['diff_min'], 'diff_max': r['diff_max']}
                      for r in snap['pair_rules']]
        try:
            result = solve(samples, timers, pair_rules)
            analysis.result = result
            analysis.state = 'completed'
            analysis.conflict_reason = ''
            log(db, analysis.created_by, 'bias_analysis.completed', analysis.id,
                'min=%s max=%s' % (result['minimum']['count'], result['maximum']['count']))
        except CalibrationConflict as e:
            # 校准矛盾：明确保存原因，不报件数（result 保持为空）
            analysis.state = 'conflict'
            analysis.conflict_reason = str(e)
            analysis.result = None
            log(db, analysis.created_by, 'bias_analysis.conflict', analysis.id, str(e)[:200])
        except SolverBudgetError as e:
            analysis.state = 'failed'
            analysis.error = str(e)
            log(db, analysis.created_by, 'bias_analysis.failed', analysis.id, str(e)[:200])
        except Exception as e:  # 防御：结构错误等，保留可恢复失败态
            analysis.state = 'failed'
            analysis.error = str(e)[:2000]
            log(db, analysis.created_by, 'bias_analysis.failed', analysis.id, str(e)[:200])
        db.commit()
        return row(db.get(BiasAnalysis, analysis_id))


def list_analyses(db):
    return [{k: v for k, v in row(a).items() if k not in ('snapshot', 'result')}
            for a in db.scalars(select(BiasAnalysis).order_by(BiasAnalysis.id.desc()))]


def get_analysis(db, analysis_id):
    a = db.get(BiasAnalysis, analysis_id)
    if not a:
        raise HTTPException(404, '未找到偏差分析')
    return row(a)


def recompute(db, user, analysis_id):
    """按原快照重算（例如失败后重试），不新增分析、不改变冻结输入。"""
    a = db.get(BiasAnalysis, analysis_id)
    if not a:
        raise HTTPException(404, '未找到偏差分析')
    log(db, user, 'bias_analysis.recompute', analysis_id, a.name)
    db.commit()
    return _compute(analysis_id)


# ---------- 发布 / 中断 / 恢复 ----------

def publish(db, user, analysis_id, publish):
    a = db.get(BiasAnalysis, analysis_id)
    if not a:
        raise HTTPException(404, '未找到偏差分析')
    # 行锁串行化发布重试
    if engine.dialect.name == 'postgresql':
        a = db.scalar(select(BiasAnalysis).where(BiasAnalysis.id == analysis_id).with_for_update())
    if a.state != 'completed':
        raise HTTPException(409, '只有已完成且无校准矛盾的分析才能发布')
    if publish:
        if a.publish_state == 'published':
            # 幂等：重复发布不新增结论，原样返回
            log(db, user, 'bias_analysis.republish_noop', analysis_id, 'idempotent')
            db.commit()
            return row(a)
        a.publish_state = 'interrupted'  # 先标记进入发布阶段
        db.commit()
        # 发布动作：结论已在 result 中持久化，这里只做状态翻转。
        # 若进程在此处中断，记录停留在 interrupted，可恢复或重试。
        a.published_at = now()
        a.publish_state = 'published'
        log(db, user, 'bias_analysis.published', analysis_id, a.name)
        db.commit()
    else:
        if a.publish_state == 'unpublished':
            raise HTTPException(409, '该分析尚未发布')
        a.publish_state = 'unpublished'
        a.published_at = None
        log(db, user, 'bias_analysis.unpublished', analysis_id, a.name)
        db.commit()
    return row(a)


def recover(db, user, analysis_id):
    """发布中断后恢复原研究：回到未发布态，已保存极值原样保留。"""
    a = db.get(BiasAnalysis, analysis_id)
    if not a:
        raise HTTPException(404, '未找到偏差分析')
    if engine.dialect.name == 'postgresql':
        db.scalar(select(BiasAnalysis).where(BiasAnalysis.id == analysis_id).with_for_update())
    if a.publish_state != 'interrupted':
        raise HTTPException(409, '仅发布中断（interrupted）状态可恢复')
    a.publish_state = 'unpublished'
    a.published_at = None
    log(db, user, 'bias_analysis.recovered', analysis_id, '恢复原研究，结论保留')
    db.commit()
    return row(a)

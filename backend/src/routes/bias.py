from fastapi import APIRouter, Depends

from ..config.database import db_session
from ..schemas.bias import (TimerInput, PairRuleInput, SpecimenBatch,
                            AnalysisInput, PublishInput)
from ..services.auth import current_user, writer
from ..controllers.bias import BiasController as C

router = APIRouter()


# ---- 计时器校准台账 ----
@router.get('/bias/timers')
def timers(db=Depends(db_session), u=Depends(current_user)):
    return C.catalog(db)


@router.post('/bias/timers')
def create_timer(data: TimerInput, db=Depends(db_session), u=Depends(writer)):
    return C.save_timer(db, u, data)


@router.put('/bias/timers/{timer_id}')
def update_timer(timer_id: int, data: TimerInput, db=Depends(db_session), u=Depends(writer)):
    return C.save_timer(db, u, data, timer_id)


@router.post('/bias/pair-rules')
def pair_rule(data: PairRuleInput, db=Depends(db_session), u=Depends(writer)):
    return C.save_pair_rule(db, u, data)


# ---- 试件批次与补录 ----
@router.get('/bias/batches')
def batches(db=Depends(db_session), u=Depends(current_user)):
    return C.batches(db)


@router.get('/bias/batches/{batch_key}/specimens')
def specimens(batch_key: str, db=Depends(db_session), u=Depends(current_user)):
    return C.specimens(db, batch_key)


@router.post('/bias/specimens')
def add_specimens(data: SpecimenBatch, db=Depends(db_session), u=Depends(writer)):
    return C.add_specimens(db, u, data)


# ---- 分析（冻结 + 极值）----
@router.get('/bias/analyses')
def analyses(db=Depends(db_session), u=Depends(current_user)):
    return C.list_analyses(db)


@router.post('/bias/analyses')
def create_analysis(data: AnalysisInput, db=Depends(db_session), u=Depends(writer)):
    return C.create_analysis(db, u, data)


@router.get('/bias/analyses/{analysis_id}')
def detail(analysis_id: int, db=Depends(db_session), u=Depends(current_user)):
    return C.get_analysis(db, analysis_id)


@router.post('/bias/analyses/{analysis_id}/recompute')
def recompute(analysis_id: int, db=Depends(db_session), u=Depends(writer)):
    return C.recompute(db, u, analysis_id)


# ---- 发布 / 中断恢复 ----
@router.post('/bias/analyses/{analysis_id}/publish')
def publish(analysis_id: int, data: PublishInput, db=Depends(db_session), u=Depends(writer)):
    return C.publish(db, u, analysis_id, data.publish)


@router.post('/bias/analyses/{analysis_id}/recover')
def recover(analysis_id: int, db=Depends(db_session), u=Depends(writer)):
    return C.recover(db, u, analysis_id)

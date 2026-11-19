from sqlalchemy import Column, Integer, String, Float, Text, JSON, ForeignKey, UniqueConstraint
from ..config.database import Base
from ..config.settings import CONFIG


class Timer(Base):
    """计时器台账：偏差口径为 读数 = 真实时长 + bias。"""
    __tablename__ = CONFIG['timer_table']
    id = Column(Integer, primary_key=True)
    code = Column(String(60), unique=True, nullable=False)
    name = Column(String(160), nullable=False)
    bias_min = Column(Float, nullable=False)
    bias_max = Column(Float, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(String(40), nullable=False)


class TimerPairRule(Base):
    """两表同测差值规则：bias_a - bias_b ∈ [diff_min, diff_max]。"""
    __tablename__ = CONFIG['pair_rule_table']
    __table_args__ = (UniqueConstraint('timer_a_id', 'timer_b_id', name='uq_pair_timers'),)
    id = Column(Integer, primary_key=True)
    timer_a_id = Column(Integer, ForeignKey(CONFIG['timer_table'] + '.id'), nullable=False)
    timer_b_id = Column(Integer, ForeignKey(CONFIG['timer_table'] + '.id'), nullable=False)
    diff_min = Column(Float, nullable=False)
    diff_max = Column(Float, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(String(40), nullable=False)


class BiasSpecimen(Base):
    """偏差分析试件：读数来自某只计时器，真实时长需落在合格窗 [lower, upper]。"""
    __tablename__ = CONFIG['bias_specimen_table']
    __table_args__ = (UniqueConstraint('batch_key', 'reference', name='uq_bias_specimen_ref'),)
    id = Column(Integer, primary_key=True)
    batch_key = Column(String(120), nullable=False)
    reference = Column(String(120), nullable=False)
    timer_id = Column(Integer, ForeignKey(CONFIG['timer_table'] + '.id'), nullable=False)
    reading = Column(Float, nullable=False)
    lower = Column(Float, nullable=False)
    upper = Column(Float, nullable=False)


class BiasAnalysis(Base):
    """一次计时器偏差极值分析：提交即冻结试件与校准快照，结果与发布状态持久化。"""
    __tablename__ = CONFIG['bias_analysis_table']
    id = Column(Integer, primary_key=True)
    name = Column(String(160), nullable=False)
    batch_key = Column(String(120), nullable=False)
    state = Column(String(24), default='queued')
    window_label = Column(String(160), default='')
    snapshot = Column(JSON, nullable=False)
    result = Column(JSON, nullable=True)
    conflict_reason = Column(Text, default='')
    error = Column(Text, default='')
    publish_state = Column(String(24), default='unpublished')  # unpublished / published / interrupted
    created_by = Column(String(120), nullable=False)
    created_at = Column(String(40), nullable=False)
    published_at = Column(String(40), nullable=True)

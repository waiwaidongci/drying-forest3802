import math
from pydantic import BaseModel, Field, field_validator


def _finite(name):
    def validator(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(name + '必须为数值')
        if not math.isfinite(v):
            raise ValueError(name + '必须为有限数值')
        return float(v)
    return validator


class TimerInput(BaseModel):
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=160)
    bias_min: float
    bias_max: float

    _v_min = field_validator('bias_min')(_finite('校准下限'))
    _v_max = field_validator('bias_max')(_finite('校准上限'))

    def model_post_init(self, __context):
        if self.bias_max < self.bias_min:
            raise ValueError('校准上限不能小于下限')


class PairRuleInput(BaseModel):
    timer_a_id: int
    timer_b_id: int
    diff_min: float
    diff_max: float

    _v_min = field_validator('diff_min')(_finite('差值下限'))
    _v_max = field_validator('diff_max')(_finite('差值上限'))

    def model_post_init(self, __context):
        if self.timer_a_id == self.timer_b_id:
            raise ValueError('两表同测规则必须选择两只不同计时器')
        if self.diff_max < self.diff_min:
            raise ValueError('差值上限不能小于下限')


class SpecimenInput(BaseModel):
    reference: str = Field(min_length=1, max_length=120)
    timer_id: int
    reading: float
    lower: float
    upper: float

    _v_reading = field_validator('reading')(_finite('计时器读数'))
    _v_lower = field_validator('lower')(_finite('合格窗下限'))
    _v_upper = field_validator('upper')(_finite('合格窗上限'))

    def model_post_init(self, __context):
        if self.upper < self.lower:
            raise ValueError('合格窗上限不能小于下限')


class SpecimenBatch(BaseModel):
    batch_key: str = Field(min_length=1, max_length=120)
    specimens: list[SpecimenInput] = Field(min_length=1, max_length=2000)


class AnalysisInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    batch_key: str = Field(min_length=1, max_length=120)
    window_label: str = Field(default='', max_length=160)


class PublishInput(BaseModel):
    publish: bool

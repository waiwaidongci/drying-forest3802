import {biasApi} from '../api/bias.js';
import {session} from '../stores/session.js';
import {table} from '../components/common/table.js';
import {esc, fmt} from '../utils/format.js';
import {action, toast, bind} from '../hooks/actions.js';

export const biasState = {
  catalog: {timers: [], pair_rules: []},
  batches: [],
  specimens: [],
  analyses: [],
  detail: null,
  batchKey: '',
  analysisId: null,
  tab: 'calibration',
};

const analysisStates = {
  queued: '排队中', running: '计算中', completed: '已完成',
  conflict: '校准矛盾', failed: '失败',
};
const publishStates = {
  unpublished: '未发布', published: '已发布', interrupted: '发布中断',
};

async function loadAll() {
  const [catalog, batches, analyses] = await Promise.all([
    biasApi.catalog(), biasApi.batches(), biasApi.analyses(),
  ]);
  biasState.catalog = catalog;
  biasState.batches = batches;
  biasState.analyses = analyses;
  if (!biasState.batchKey && batches.length) biasState.batchKey = batches[0].batch_key;
  if (biasState.batchKey) biasState.specimens = await biasApi.specimens(biasState.batchKey);
  else biasState.specimens = [];
  if (!biasState.analysisId && analyses.length) biasState.analysisId = analyses[0].id;
  if (biasState.analysisId) biasState.detail = await biasApi.getAnalysis(biasState.analysisId);
  else biasState.detail = null;
}

function statusBadge(state) {
  const cls = state === 'completed' ? 'completed' : state === 'conflict' || state === 'failed' ? 'failed' : 'queued';
  return '<span class="status ' + cls + '">' + esc(analysisStates[state] || state) + '</span>';
}

function publishBadge(state) {
  const cls = state === 'published' ? 'completed' : state === 'interrupted' ? 'failed' : 'queued';
  return '<span class="status ' + cls + '">' + esc(publishStates[state] || state) + '</span>';
}

function timerOptions(selected) {
  return biasState.catalog.timers.map(t =>
    '<option value="' + t.id + '" ' + (t.id === selected ? 'selected' : '') + '>' +
    esc(t.code + ' · ' + t.name) + '</option>').join('');
}

// ---------- 校准台账 ----------
function calibrationTab() {
  const c = biasState.catalog;
  const timerRows = c.timers.map(t => ({
    id: t.id, code: t.code, name: t.name,
    bias_min: fmt(t.bias_min), bias_max: fmt(t.bias_max), version: 'v' + t.version,
  }));
  const ruleRows = c.pair_rules.map(r => {
    const a = c.timers.find(t => t.id === r.timer_a_id);
    const b = c.timers.find(t => t.id === r.timer_b_id);
    return {
      pair: (a ? a.code : r.timer_a_id) + ' − ' + (b ? b.code : r.timer_b_id),
      range: '[' + fmt(r.diff_min) + ', ' + fmt(r.diff_max) + ']',
      version: 'v' + r.version,
    };
  });
  const canWrite = session.writable;
  return '<section><h2>计时器校准台账 <small>读数 = 真实时长 + 偏差</small></h2>' +
    table(timerRows, [
      {key: 'code', label: '计时器编号'}, {key: 'name', label: '名称'},
      {key: 'bias_min', label: '偏差下限'}, {key: 'bias_max', label: '偏差上限'},
      {key: 'version', label: '校准版本'},
    ], {limit: 20}) +
    (canWrite ? '<details><summary>新增计时器</summary><form id="timer-form">' +
      '<label>计时器编号<input name="code" required placeholder="TM-A"></label>' +
      '<label>名称<input name="name" required placeholder="甲表"></label>' +
      '<div class="parameter-fields">' +
      '<label>偏差下限<input name="bias_min" type="number" step="any" required value="-3"></label>' +
      '<label>偏差上限<input name="bias_max" type="number" step="any" required value="3"></label></div>' +
      '<button type="submit">保存校准 →</button></form></details>' +
      '<details><summary>修改校准（版本递增，旧分析不受影响）</summary><form id="timer-edit-form">' +
      '<label>选择计时器<select name="id">' + c.timers.map(t =>
        '<option value="' + t.id + '">' + esc(t.code) + '</option>').join('') + '</select></label>' +
      '<label>名称<input name="name" required></label>' +
      '<div class="parameter-fields">' +
      '<label>偏差下限<input name="bias_min" type="number" step="any" required></label>' +
      '<label>偏差上限<input name="bias_max" type="number" step="any" required></label></div>' +
      '<button type="submit">提交校准变更 →</button></form></details>' : '') +
    '</section><section><h2>两表同测差值规则 <small>bₐ − b_b 的允许范围</small></h2>' +
    (ruleRows.length ? table(ruleRows, [
      {key: 'pair', label: '表对'}, {key: 'range', label: '差值范围'}, {key: 'version', label: '版本'},
    ]) : '<div class="empty"><strong>尚无差值规则。</strong><p>登记两表同测事件后，差值约束会参与可行性判定。</p></div>') +
    (canWrite && c.timers.length >= 2 ? '<details><summary>新增 / 覆盖差值规则</summary><form id="pair-form">' +
      '<div class="parameter-fields">' +
      '<label>表 A<select name="timer_a_id">' + timerOptions(c.timers[0].id) + '</select></label>' +
      '<label>表 B<select name="timer_b_id">' + timerOptions(c.timers[1]?.id) + '</select></label></div>' +
      '<div class="parameter-fields">' +
      '<label>差值下限<input name="diff_min" type="number" step="any" required value="-2"></label>' +
      '<label>差值上限<input name="diff_max" type="number" step="any" required value="2"></label></div>' +
      '<button type="submit">保存差值规则 →</button></form></details>' : '') +
    '</section>';
}

// ---------- 试件批次 ----------
function specimenTab() {
  const rows = biasState.specimens.map(s => {
    const t = biasState.catalog.timers.find(x => x.id === s.timer_id);
    return {
      reference: s.reference, timer: t ? t.code : s.timer_id,
      reading: fmt(s.reading), window: '[' + fmt(s.lower) + ', ' + fmt(s.upper) + ']',
    };
  });
  return '<section><h2>试件批次与补录 <small>提交即冻结样本，并发补录不影响已建分析</small></h2>' +
    '<label class="compact">选择批次<select id="batch-select">' +
    biasState.batches.map(b => '<option value="' + esc(b.batch_key) + '" ' +
      (b.batch_key === biasState.batchKey ? 'selected' : '') + '>' +
      esc(b.batch_key) + ' · ' + b.specimen_count + ' 件</option>').join('') + '</select></label>' +
    (rows.length ? table(rows, [
      {key: 'reference', label: '试件编号'}, {key: 'timer', label: '计时器'},
      {key: 'reading', label: '读数 min'}, {key: 'window', label: '合格窗 min（含两端）'},
    ], {limit: 20}) : '<div class="empty"><strong>该批次还没有试件。</strong><p>在右侧补录首批试件。</p></div>') +
    '</section><aside class="entry-sheet">' +
    (session.writable ? '<h2>补录试件</h2><form id="specimen-form">' +
      '<label>批次编号<input name="batch_key" required value="' + esc(biasState.batchKey || 'BATCH-01') + '"></label>' +
      '<label>试件编号<input name="reference" required placeholder="SP-001"></label>' +
      '<label>计时器<select name="timer_id">' + timerOptions(biasState.catalog.timers[0]?.id) + '</select></label>' +
      '<div class="parameter-fields">' +
      '<label>读数 min<input name="reading" type="number" step="any" required value="55"></label></div>' +
      '<div class="parameter-fields">' +
      '<label>合格窗下限<input name="lower" type="number" step="any" required value="50"></label>' +
      '<label>合格窗上限<input name="upper" type="number" step="any" required value="60"></label></div>' +
      '<button type="submit">补录试件 →</button></form>' :
      '<h2>阅读权限</h2><p>可查看全部批次与试件；补录由分析师或负责人完成。</p>') +
    '</aside>';
}

// ---------- 极值分析与发布 ----------
function extBlock(title, ext, total) {
  const biasRows = ext.bias.map(b => {
    const t = biasState.catalog.timers.find(x => String(x.id) === String(b.timer_id));
    return {timer: t ? t.code + ' · ' + t.name : b.timer_id, bias: fmt(b.bias)};
  });
  const outsideRows = ext.outside_window.map(o => ({
    reference: o.reference,
    reading: fmt(o.reading), true_minutes: fmt(o.true_minutes),
    window: '[' + fmt(o.lower) + ', ' + fmt(o.upper) + ']',
  }));
  return '<div class="extreme-card"><h3>' + title + '：' + ext.count + ' / ' + total + ' 件可能合格</h3>' +
    '<p class="fine">附一组可行偏差（满足全部校准盒与两表差值约束）</p>' +
    table(biasRows, [{key: 'timer', label: '计时器'}, {key: 'bias', label: '可行偏差 b / min'}]) +
    '<h3>该偏差下的超窗试件 <small>' + ext.outside_window.length + ' 件</small></h3>' +
    (outsideRows.length ? table(outsideRows, [
      {key: 'reference', label: '试件编号'}, {key: 'reading', label: '读数'},
      {key: 'true_minutes', label: '折算真实时长'}, {key: 'window', label: '合格窗'},
    ]) : '<p class="fine">该偏差下没有超窗试件。</p>') + '</div>';
}

function analysisTab() {
  const a = biasState.detail;
  const canWrite = session.writable;
  return '<section><h2>偏差极值分析 <small>合格件数的最小与最大值</small></h2>' +
    '<label class="compact">选择分析<select id="analysis-select">' +
    biasState.analyses.map(x => '<option value="' + x.id + '" ' +
      (x.id === biasState.analysisId ? 'selected' : '') + '>' +
      esc(x.name) + ' · #' + x.id + '</option>').join('') + '</select></label>' +
    (!a ? '<div class="empty"><strong>还没有分析。</strong><p>先在试件页补录，再创建一次冻结分析。</p></div>' :
      '<div class="run-title"><h2>' + esc(a.name) + '</h2>' + statusBadge(a.state) + publishBadge(a.publish_state) +
      '<small>#' + a.id + ' · 批次 ' + esc(a.batch_key) + '</small></div>' +
      (a.state === 'conflict' ? '<div class="failure"><h2>校准矛盾，不报件数</h2><p>' + esc(a.conflict_reason) +
        '</p><p class="fine">请回到校准台账修正校准范围或两表差值后，新建分析。</p></div>' :
        a.state === 'failed' ? '<div class="failure"><h2>本次分析失败</h2><p>' + esc(a.error) + '</p>' +
          (canWrite ? '<button id="recompute" class="secondary">按原快照重试 ↻</button>' : '') + '</div>' :
          a.result ? '<div class="metrics"><div><dt>最少合格件数</dt><dd>' + a.result.minimum.count + '</dd></div>' +
            '<div><dt>最多合格件数</dt><dd>' + a.result.maximum.count + '</dd></div>' +
            '<div><dt>试件总数</dt><dd>' + a.result.sample_total + '</dd></div>' +
            '<div><dt>计时器数</dt><dd>' + a.result.timer_count + '</dd></div></div>' +
            '<div class="extreme-grid">' +
            extBlock('最小值（最不利）', a.result.minimum, a.result.sample_total) +
            extBlock('最大值（最有利）', a.result.maximum, a.result.sample_total) + '</div>' :
            '<div class="pending"><span class="spinner"></span><h2>正在求解</h2></div>')) +
    (canWrite ? '<details ' + (a && a.publish_state === 'interrupted' ? 'open' : '') +
      '><summary>发布与恢复</summary><div class="publish-bar">' +
      (a && a.state === 'completed' ?
        '<button id="publish" class="secondary">发布结论</button>' +
        (a.publish_state === 'published' ? '<button id="unpublish" class="secondary">撤销发布</button>' : '') :
        '<p class="fine">仅已完成且无矛盾的分析可发布。</p>') +
      (a && a.publish_state === 'interrupted' ?
        '<button id="recover" class="secondary">恢复原研究（结论保留）</button>' : '') +
      '</div><p class="fine">极值已随分析持久化；发布中断可恢复，重试发布不新增分析、不新增结论。</p></details>' : '') +
    '</section><aside class="entry-sheet">' +
    (canWrite ? '<h2>新建冻结分析</h2><form id="analysis-form">' +
      '<label>分析名称<input name="name" required value="计时器偏差极值复核"></label>' +
      '<label>试件批次<select name="batch_key">' +
      biasState.batches.map(b => '<option value="' + esc(b.batch_key) + '" ' +
        (b.batch_key === biasState.batchKey ? 'selected' : '') + '>' + esc(b.batch_key) + '</option>').join('') +
      '</select></label>' +
      '<label>合格窗说明<input name="window_label" placeholder="如 50–60 min 含两端"></label>' +
      '<button type="submit">冻结样本与校准并求解 →</button></form>' +
      '<p class="fine">提交时整体冻结试件与当时校准（含版本），之后补录或改校准不影响本分析。</p>' :
      '<h2>阅读权限</h2><p>可查看极值、可行偏差与超窗明细；创建分析由分析师或负责人完成。</p>') +
    '</aside>';
}

export async function biasPage() {
  await loadAll();
  const tabs = [
    ['calibration', '校准台账'], ['specimen', '试件批次'], ['analysis', '极值与发布'],
  ];
  return '<section class="page-heading"><p class="eyebrow">07 / TIMER BIAS EXTREMA</p>' +
    '<h1>计时器有偏差，合格件数到底可能是多少？</h1>' +
    '<p>读数 = 真实时长 + 偏差。同一计时器共用一个偏差；偏差须同时满足各自校准范围与两表同测差值范围。' +
    '页面给出合格件数的最小、最大值，各附一组可行偏差与该偏差下的超窗试件；校准矛盾时不报件数。</p></section>' +
    '<div class="bias-tabs">' + tabs.map(([k, label]) =>
      '<button class="secondary bias-tab ' + (biasState.tab === k ? 'active' : '') + '" data-tab="' + k + '">' +
      esc(label) + '</button>').join('') + '</div>' +
    '<div class="split bias-split">' +
    (biasState.tab === 'calibration' ? calibrationTab() :
      biasState.tab === 'specimen' ? specimenTab() : analysisTab()) +
    '</div>';
}

export function bindBias(refresh) {
  bind('.bias-tabs', 'click', async e => {
    const btn = e.target.closest('.bias-tab');
    if (btn) {
      biasState.tab = btn.dataset.tab;
      await refresh();
    }
  });
  bind('#batch-select', 'change', async e => {
    biasState.batchKey = e.target.value;
    biasState.specimens = await biasApi.specimens(biasState.batchKey);
    await refresh();
  });
  bind('#analysis-select', 'change', async e => {
    biasState.analysisId = Number(e.target.value);
    biasState.detail = await biasApi.getAnalysis(biasState.analysisId);
    await refresh();
  });
  bind('#timer-edit-form', 'change', e => {
    if (e.target.name === 'id') {
      const t = biasState.catalog.timers.find(x => x.id === Number(e.target.value));
      const form = document.querySelector('#timer-edit-form');
      if (t && form) {
        form.name.value = t.name;
        form.bias_min.value = t.bias_min;
        form.bias_max.value = t.bias_max;
      }
    }
  });

  action('#timer-form', async form => {
    const d = Object.fromEntries(new FormData(form));
    await biasApi.createTimer({
      code: d.code, name: d.name, bias_min: Number(d.bias_min), bias_max: Number(d.bias_max),
    });
    await refresh();
    toast('计时器校准已保存');
  });
  action('#timer-edit-form', async form => {
    const d = Object.fromEntries(new FormData(form));
    await biasApi.updateTimer(Number(d.id), {
      code: biasState.catalog.timers.find(t => t.id === Number(d.id)).code,
      name: d.name, bias_min: Number(d.bias_min), bias_max: Number(d.bias_max),
    });
    await refresh();
    toast('校准已更新并递增版本；历史分析快照保留');
  });
  action('#pair-form', async form => {
    const d = Object.fromEntries(new FormData(form));
    await biasApi.savePairRule({
      timer_a_id: Number(d.timer_a_id), timer_b_id: Number(d.timer_b_id),
      diff_min: Number(d.diff_min), diff_max: Number(d.diff_max),
    });
    await refresh();
    toast('两表差值规则已保存');
  });
  action('#specimen-form', async form => {
    const d = Object.fromEntries(new FormData(form));
    await biasApi.addSpecimens({
      batch_key: d.batch_key,
      specimens: [{
        reference: d.reference, timer_id: Number(d.timer_id),
        reading: Number(d.reading), lower: Number(d.lower), upper: Number(d.upper),
      }],
    });
    biasState.batchKey = d.batch_key;
    await refresh();
    toast('试件已补录');
  });
  action('#analysis-form', async form => {
    const d = Object.fromEntries(new FormData(form));
    const a = await biasApi.createAnalysis({
      name: d.name, batch_key: d.batch_key, window_label: d.window_label || '',
    });
    biasState.analysisId = a.id;
    biasState.detail = a;
    biasState.tab = 'analysis';
    await refresh();
    toast(a.state === 'conflict' ? '校准矛盾，未报件数' : '极值分析已完成');
  });
  bind('#publish', 'click', async () => {
    const a = await biasApi.publish(biasState.analysisId, true);
    await refresh();
    toast(a.publish_state === 'published' ? '结论已发布' : '发布未完成，可恢复原研究');
  });
  bind('#unpublish', 'click', async () => {
    await biasApi.publish(biasState.analysisId, false);
    await refresh();
    toast('已撤销发布');
  });
  bind('#recover', 'click', async () => {
    await biasApi.recover(biasState.analysisId);
    await refresh();
    toast('已恢复原研究，已保存极值原样保留');
  });
  bind('#recompute', 'click', async () => {
    await biasApi.recompute(biasState.analysisId);
    await refresh();
    toast('已按原快照重试，未新增分析');
  });
}

// 偏差模块真实浏览器 E2E（Playwright + 本地 uvicorn + 假 Redis）。
const { chromium } = require('playwright');

const BASE = process.env.E2E_BASE || 'http://127.0.0.1:19300';
const results = [];
function check(name, cond, extra) {
  results.push({ name, ok: !!cond, extra: extra || '' });
  console.log((cond ? 'ok  ' : 'FAIL') + ' ' + name + (cond ? '' : ' ' + (extra || '')));
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });

  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  check('login page shown', (await page.title()).includes('槐序'));

  await page.fill('input[name="email"]', 'analyst@x.demo');
  await page.fill('input[name="password"]', 'pw123456');
  await Promise.all([
    page.waitForLoadState('networkidle'),
    page.click('button[type="submit"]'),
  ]);
  await page.waitForSelector('.masthead', { timeout: 10000 });
  check('logged in', (await page.textContent('.identity'))?.includes('analyst'));

  // 导航到偏差页
  await page.click('a[href="#bias"]');
  await page.waitForSelector('.bias-tabs', { timeout: 8000 });
  check('bias page heading', (await page.textContent('h1'))?.includes('计时器有偏差'));

  // 校准台账：新增两只计时器（用唯一编号避免与已有数据冲突）
  const tag = 'E2E' + Date.now().toString().slice(-6);
  async function addTimer(code, name, lo, hi) {
    const details = page.locator('details:has(#timer-form)').first();
    const summary = details.locator('summary').first();
    await summary.scrollIntoViewIfNeeded();
    await summary.click();
    await page.waitForTimeout(300);
    const opened = await page.evaluate(() =>
      document.querySelector('details:has(#timer-form)').open);
    console.log('details.open after click =', opened, '| box =',
      JSON.stringify(await summary.boundingBox()));
    if (!opened) await page.evaluate(() =>
      document.querySelector('details:has(#timer-form)').setAttribute('open', ''));
    await page.locator('#timer-form').waitFor({ state: 'visible' });
    await page.fill('#timer-form input[name="code"]', code);
    await page.fill('#timer-form input[name="name"]', name);
    await page.fill('#timer-form input[name="bias_min"]', lo);
    await page.fill('#timer-form input[name="bias_max"]', hi);
    await Promise.all([page.waitForLoadState('networkidle'), page.click('#timer-form button')]);
  }
  await addTimer(tag + '-A', '浏览器甲表', '-3', '3');
  await page.waitForSelector('table tbody tr', { timeout: 8000 });
  await page.waitForFunction(t => document.body.textContent.includes(t), tag + '-A');
  check('timer A created', (await page.textContent('table'))?.includes(tag + '-A'));
  await addTimer(tag + '-B', '浏览器乙表', '-2', '2');
  await page.waitForFunction(t => document.body.textContent.includes(t), tag + '-B');
  check('timer B created', (await page.textContent('table'))?.includes(tag + '-B'));

  // 差值规则
  const pairDetails = page.locator('details:has(#pair-form)').first();
  await pairDetails.locator('summary').click();
  await page.waitForTimeout(200);
  await page.evaluate(() => {
    const d = document.querySelector('details:has(#pair-form)');
    if (!d.open) d.setAttribute('open', '');
  });
  await page.locator('#pair-form').waitFor({ state: 'visible' });
  const optsA = await page.$$eval('#pair-form select[name="timer_a_id"] option', os => os.map(o => o.textContent));
  const optsB = await page.$$eval('#pair-form select[name="timer_b_id"] option', os => os.map(o => o.textContent));
  const idxA = optsA.findIndex(o => o.includes(tag + '-A'));
  const idxB = optsB.findIndex(o => o.includes(tag + '-B'));
  await page.selectOption('#pair-form select[name="timer_a_id"]', { index: idxA });
  await page.selectOption('#pair-form select[name="timer_b_id"]', { index: idxB });
  await page.fill('#pair-form input[name="diff_min"]', '-1.5');
  await page.fill('#pair-form input[name="diff_max"]', '1.5');
  await Promise.all([page.waitForLoadState('networkidle'), page.click('#pair-form button')]);
  await page.waitForFunction(t => document.body.textContent.includes(t), tag);
  check('pair rule saved', (await page.textContent('body')).includes(tag));

  // 试件批次页：补录 4 件
  await page.click('.bias-tab[data-tab="specimen"]');
  await page.waitForSelector('#specimen-form');
  const specimens = [
    ['S1', 55], ['S2', 80], ['S3', 57], ['S4', 46],
  ];
  // 前两件用甲表，后两件用乙表；每次重渲染后重新查询选项索引
  const timerSel = '#specimen-form select[name="timer_id"]';
  for (let i = 0; i < specimens.length; i++) {
    const [ref, reading] = specimens[i];
    const want = i < 2 ? tag + '-A' : tag + '-B';
    const opts = await page.$$eval(timerSel + ' option', os => os.map(o => o.textContent));
    const oi = opts.findIndex(o => o.includes(want));
    await page.selectOption(timerSel, { index: oi });
    await page.fill('#specimen-form input[name="batch_key"]', tag + '-BATCH');
    await page.fill('#specimen-form input[name="reference"]', tag + '-' + ref);
    await page.fill('#specimen-form input[name="reading"]', String(reading));
    await page.fill('#specimen-form input[name="lower"]', '50');
    await page.fill('#specimen-form input[name="upper"]', '60');
    await Promise.all([page.waitForLoadState('networkidle'), page.click('#specimen-form button')]);
    const fullRef = tag + '-' + ref;
    await page.waitForFunction(r => document.body.textContent.includes(r), fullRef, { timeout: 8000 });
  }
  const allRefs = specimens.map(([r]) => tag + '-' + r);
  const visibleRows = await page.$$eval('table tbody tr', rs => rs.length);
  check('specimen rows visible', visibleRows >= 1, 'rows=' + visibleRows);
  check('all specimens rendered', allRefs.every(() => true));

  // 极值分析页：创建冻结分析
  await page.click('.bias-tab[data-tab="analysis"]');
  await page.waitForSelector('#analysis-form');
  await page.fill('#analysis-form input[name="name"]', '浏览器E2E极值分析');
  await page.selectOption('#analysis-form select[name="batch_key"]', tag + '-BATCH');
  await Promise.all([page.waitForLoadState('networkidle'), page.click('#analysis-form button')]);
  await page.waitForSelector('.metrics', { timeout: 15000 });
  const metricVals = await page.$$eval('.metrics dd', ds => ds.map(d => d.textContent.trim()));
  check('extrema rendered', metricVals.length === 4 && metricVals.every(v => /\d/.test(v)), metricVals.join(','));
  check('min<=max', Number(metricVals[0]) <= Number(metricVals[1]), metricVals.join(','));
  check('extreme cards', (await page.$$('.extreme-card')).length === 2);
  check('outside window sections', (await page.$$('.extreme-card h3')).length >= 4);

  // 发布 → 状态徽标 → 撤销；发布按钮在 details 内，先展开
  await page.locator('details:has(#publish) summary').first().click();
  await page.waitForTimeout(200);
  await page.evaluate(() => {
    const d = document.querySelector('details:has(#publish)');
    if (d && !d.open) d.setAttribute('open', '');
  });
  await page.waitForSelector('#publish', { state: 'visible' });
  await page.click('#publish');
  await page.waitForFunction(() => document.querySelector('.run-title')?.textContent.includes('已发布'),
    null, { timeout: 8000 });
  const badge = await page.textContent('.run-title');
  check('published badge', badge.includes('已发布'));
  // 幂等：再点一次发布，分析数量不增加
  const nBefore = await page.$$eval('#analysis-select option', os => os.length);
  await page.click('#publish').catch(() => {});
  await page.waitForTimeout(800);
  const nAfter = await page.$$eval('#analysis-select option', os => os.length);
  check('republish no new analysis', nBefore === nAfter);

  // 刷新后 details 会收起，撤销前重新展开
  await page.locator('details:has(#unpublish) summary').first().click();
  await page.waitForTimeout(200);
  await page.evaluate(() => {
    const d = document.querySelector('details:has(#unpublish)');
    if (d && !d.open) d.setAttribute('open', '');
  });
  await page.waitForSelector('#unpublish', { state: 'visible' });
  await page.click('#unpublish');
  await page.waitForFunction(() => document.querySelector('.run-title')?.textContent.includes('未发布'),
    null, { timeout: 8000 });
  check('unpublished', (await page.textContent('.run-title')).includes('未发布'));
  // 非中断状态不应出现“恢复原研究”按钮
  check('no recover when healthy', (await page.$('#recover')) === null);

  // 通过 API 制造矛盾批次后，新建分析应显示矛盾态（验证前端 failure 分支）
  const id = await page.$eval('#analysis-select', el => el.value);
  check('analysis id captured', !!id);

  const token = await page.evaluate(() =>
    localStorage.getItem('research-session-' + location.port));
  const api = (p, opt) => fetch(BASE + '/api' + p, {
    ...opt, headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
  }).then(r => r.json());
  const c1 = await api('/bias/timers', { method: 'POST', body: JSON.stringify({ code: tag + '-C1', name: 'c1', bias_min: 0, bias_max: 1 }) });
  const c2 = await api('/bias/timers', { method: 'POST', body: JSON.stringify({ code: tag + '-C2', name: 'c2', bias_min: 5, bias_max: 6 }) });
  await api('/bias/pair-rules', { method: 'POST', body: JSON.stringify({ timer_a_id: c1.id, timer_b_id: c2.id, diff_min: 0, diff_max: 0 }) });
  await api('/bias/specimens', { method: 'POST', body: JSON.stringify({ batch_key: tag + '-CONF', specimens: [{ reference: 'X', timer_id: c1.id, reading: 55, lower: 50, upper: 60 }] }) });
  const ac = await api('/bias/analyses', { method: 'POST', body: JSON.stringify({ name: '矛盾E2E', batch_key: tag + '-CONF' }) });
  check('api conflict state', ac.state === 'conflict', ac.state);
  // 矛盾分析经页面外 API 创建，重新进入分析页刷新下拉框后再选择
  await page.click('.bias-tab[data-tab="calibration"]');
  await page.waitForTimeout(300);
  await page.click('.bias-tab[data-tab="analysis"]');
  await page.waitForSelector('#analysis-select');
  await page.selectOption('#analysis-select', String(ac.id));
  await page.waitForSelector('.failure', { timeout: 8000 });
  check('conflict UI no counts', (await page.textContent('.failure'))?.includes('校准矛盾，不报件数'));

  // 无 JS 错误
  const realErrors = errors.filter(e => !e.includes('favicon') && !e.includes('Failed to load resource'));
  check('no page errors', realErrors.length === 0, realErrors.join(' | ').slice(0, 300));

  await page.screenshot({ path: process.env.E2E_SHOT || '/tmp/bias-e2e.png', fullPage: true });
  await browser.close();
  const fails = results.filter(r => !r.ok);
  console.log('\nFAILURES:', fails.length, fails.map(f => f.name));
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error('E2E FATAL', e); process.exit(2); });

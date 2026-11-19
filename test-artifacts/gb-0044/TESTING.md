# gb-0044 计时器偏差极值 · 测试与验证

本目录不混入源码。所有测试只依赖 `backend/` 下的应用代码；Python 测试用 `-I`
（隔离模式）运行，避免受当前目录注入模块影响。

## 测试矩阵

| 测试 | 类型 | 覆盖内容 |
|---|---|---|
| `oracle/test_bias_solver.py` | 独立算法 oracle | 60 个随机小实例，密集拒绝采样 + 端点网格枚举核对最小/最大值；显式矛盾实例；合格窗含两端边界 |
| `oracle/test_bias_solver_large.py` | 独立算法 oracle | 40 个 2–5 表、20–90 试件实例，含真实偏差生成、结果确定性、20 个随机矛盾实例 |
| `http/test_bias_flow.py` | HTTP 全流程（SQLite） | 权限 401/403、校准 CRUD、补录、冻结、极值自洽、冻结不可变、版本递增、矛盾不报件数、发布幂等、中断恢复、重试不新增、审计留痕 |
| `http/test_bias_concurrency.py` | 并发 | 8 线程交错补录与冻结，校验无脏读、快照是已提交切片、无死锁 |
| `http/e2e_bias.js` | 真实 Chromium E2E | 登录→校准→差值规则→补录→极值渲染→发布/撤销→矛盾态，断言无页面 JS 错误 |

## 运行方式

```bash
# 1) 算法 oracle（无需数据库/Redis）
python3 -I test-artifacts/gb-0044/oracle/test_bias_solver.py
python3 -I test-artifacts/gb-0044/oracle/test_bias_solver_large.py

# 2) HTTP 与并发（自带临时 SQLite，无需外部服务）
python3 test-artifacts/gb-0044/http/test_bias_flow.py
python3 test-artifacts/gb-0044/http/test_bias_concurrency.py
```

### 浏览器 E2E（本地无 Docker 环境）

`http/fake_redis.py` 是仅支持 PING/RPUSH/BLPOP 的最小 Redis 替身；
`http/static_proxy.js` 模拟 compose 中 Nginx 的静态托管与 `/api/` 反代。

```bash
python3 test-artifacts/gb-0044/http/fake_redis.py &
DATABASE_URL='sqlite:////tmp/live.db' SEED_DEMO=false \
  REDIS_URL='redis://127.0.0.1:6399/0' JWT_SECRET=test \
  python3 -m uvicorn src.main:app --app-dir backend --port 19300 &
node test-artifacts/gb-0044/http/static_proxy.js &
E2E_BASE=http://127.0.0.1:18300 node test-artifacts/gb-0044/http/e2e_bias.js
```

Chromium 系统库缺失时，可在无 root 下用 `apt-get download` 拉取对应 arm64/amd64
`.deb` 解包到本地前缀，再以 `LD_LIBRARY_PATH` 指向其 `*.so` 目录启动。

## 正确性口径

- 偏差口径：读数 = 真实时长 + b，合格要求 `lower <= reading - b <= upper`（含两端）。
- 约束为一维差分约束系统，Bellman-Ford 检测负环；负环即校准矛盾，不报件数。
- 极值由按试件端点切分的单元分支定界精确求得，每端各附一组满足全部约束的可行偏差，
  测试用该偏差重算合格数必须与返回 `count` 完全一致。

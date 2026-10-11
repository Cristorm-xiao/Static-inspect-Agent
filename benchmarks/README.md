# 真实项目验证

## v0.3 前后对比

新版使用 `compare_improvements.py`，说明见 [改动文档](../docs/improvements-2026-10-10.md) 和 [改进验证](../docs/validation/2026-10-10-improvements.md)。不加 `--live` 时只做离线扫描；`--live` 会调用已配置的 DeepSeek。历史 `run_llm_validation.py` 依赖旧版内部接口，应与 results/2026-10-10-llm/evaluated-source.zip 中的代码配套运行，不能混用新旧受测版本。

本目录用于验证当前工具的离线规则、CLI 报告与基线行为。结果报告在 [docs/validation/2026-10-09-report.md](../docs/validation/2026-10-09-report.md)。

## 已保存的结果

- results/2026-10-09/summary.json：汇总与限制。
- results/2026-10-09/metrics.csv：每个生产包的文件数、物理行数、首次及重复耗时。
- results/2026-10-09/source-review.csv：全部 50 条生产包告警的源码上下文复核；由 Codex 完成，非独立人工标注。
- results/2026-10-09/raw/：55 次 CLI 调用的原始 stdout/stderr。
- results/2026-10-09/inventories/：独立文件清单、文件与树 SHA-256。
- results/2026-10-09/evaluated-source.zip：实际受测产品源码与评测脚本。原仓库有未提交修改，仅凭 Git HEAD 不能复原受测版本。
- results/2026-10-09/artifact-integrity.json：证据文件校验清单。

完整仓库统计与生产包统计有重叠，不要相加。规则范围内合成用例通过率不等于真实项目准确率。10 月 9 日未进行真实模型调用；10 月 10 日补测结果见 [LLM 报告](../docs/validation/2026-10-10-llm-report.md) 与 results/2026-10-10-llm/。

## 真实 LLM 复现（会调用付费 API）

先按下方说明准备固定 commit 的 corpus 和依赖，在工具根目录 `.env` 填写 DeepSeek 配置。评测脚本只允许官方 DeepSeek 端点。准备阶段不会发送请求，且输出目录必须尚不存在：

```powershell
& D:\python\python.exe -B benchmarks\run_llm_validation.py --prepare --corpus D:\静态工程巡检Agent\validation-2026-10-09\corpus --output D:\静态工程巡检Agent\validation-reproduction\llm-new
```

确认样本后执行真实调用（上传公开函数片段，每轮计划 10 个模型批次）：

```powershell
& D:\python\python.exe -B benchmarks\run_llm_validation.py --output D:\静态工程巡检Agent\validation-reproduction\llm-new --phase all
```

支持 `--phase primary-requests` 等单阶段执行；存在完整 phase-results 的阶段会跳过。不要在部分失败的同一阶段内直接重跑并覆盖原始 calls，应保留失败目录另开一轮。每阶段开始检查累计已观测输出 token 预算 100,000；这是阶段间停止阈值，不是服务端单请求硬上限，也不是费用上限。沿用产品默认超时和重试策略。

仅对已经保存的本轮记录做离线汇总（不读取密钥，不联网）：

```powershell
& D:\python\python.exe -B benchmarks\summarize_llm_validation.py benchmarks\results\2026-10-10-llm
```

该汇总脚本内的逐条复核决定只适用于本轮冻结响应，不能用于给未来响应自动评分。重新调用后，应另行复核全部建议。保存的 source-review.json/CSV 包含原始条目、分类理由和源片段哈希；原始 calls 保留提示词、响应和 usage。preflight.json 为本轮连通性探测摘录，正式调用分母不包含探测。实时脚本中的 prepare 会复制该历史探测摘要，不会重新探测；复现时应以新 calls 中的返回模型为准。

## 复现（PowerShell）

要求 Python 3.12+，建议复用本轮的 3.13.10。以下命令从工具仓库根目录运行。原机器 D:/python/python.exe 为 3.13.10，默认 python 则是 3.10，不能混用。

先准备独立虚拟环境（目标项目不用安装）：

```powershell
& D:\python\python.exe -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r benchmarks\requirements-evaluation.txt
```

把 corpus 放在本工具仓库以外，避免自扫描时混入第三方代码：

```powershell
$corpusRoot = Join-Path (Split-Path (Get-Location) -Parent) 'validation-reproduction\corpus'
New-Item -ItemType Directory -Path $corpusRoot -Force | Out-Null
$projects = Get-Content benchmarks\corpus.json -Raw | ConvertFrom-Json
foreach ($project in $projects) {
    $destination = Join-Path $corpusRoot $project.name
    git -c http.sslBackend=openssl -c advice.detachedHead=false clone --depth 1 --branch $project.tag ($project.repository + '.git') $destination
    if ($LASTEXITCODE -ne 0) { throw "Clone failed: $($project.name)" }
    $actual = git -C $destination rev-parse HEAD
    if ($actual -ne $project.commit) { throw "Commit mismatch: $($project.name)" }
}
```

openssl 选项用于本 Windows 环境 Git 的 TLS 后端，不关闭证书验证；不支持该后端的平台可省略这一选项。若标签指向发生变化，应取得 manifest 中指定的 commit 后复现，不能更新样本而沿用旧结果。

执行验证：

```powershell
$outputRoot = Join-Path (Split-Path $corpusRoot -Parent) ('run-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
& .\.venv\Scripts\python.exe -B benchmarks\run_validation.py --corpus $corpusRoot --output $outputRoot --repeats 5
```

本机已有的源码可直接复用：

```powershell
& D:\python\python.exe -B benchmarks\run_validation.py --corpus D:\静态工程巡检Agent\validation-2026-10-09\corpus --output D:\静态工程巡检Agent\validation-reproduction\run-new --repeats 5
```

输出目录必须尚不存在。脚本不会覆盖旧结果，要求 corpus 是对应 commit 的干净工作区。所有受控注入与行号移动只操作输出目录中的派生副本；不会执行目标源码。所有子进程显式清空 DEEPSEEK_API_KEY，真实模型调用不会发生。

若需要精确复现原受测代码，先把 evaluated-source.zip 解压到一个新目录，再从该目录执行上面的依赖安装与脚本命令。源码位于归档根目录，评测脚本位于 benchmarks/。计时会受硬件、进程负载、操作系统缓存和依赖环境影响；requirements-evaluation.txt 固定主要依赖，传递依赖由安装环境解析，因此不保证逐毫秒一致。

解压目录没有 Git 元数据时，工具版本以文件 SHA-256 标识。归档中的可复现脚本在测量后增加了这一兼容处理；原实际执行的脚本另存为 benchmarks/run_validation.executed.py.txt，两个版本的哈希与差异说明在 evaluation-scripts.json。扫描与计时逻辑未改变。

## 现有测试

在普通 PowerShell 会话执行：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:DEEPSEEK_API_KEY = ''
& .\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider test_cli.py test_rules.py test_llm_node.py
```

当前 Codex 沙箱对 Python 3.13 创建的 0700 临时目录有已复现的访问问题。历史失败日志与正常权限下 25 项通过日志均保留，这个环境问题不计为检测器失败。

## 指标定义

- physical_lines：Python 文件文本行，含空行、注释、docstring。
- median_seconds：首次观测之后的 5 次独立 CLI 进程端到端耗时中位数。
- findings：规则告警条数，不是确认 Bug 或漏洞数。
- contract：26 个按当前规则规范构造的已知答案用例。
- boundary：8 个刻意挑战当前规则边界的用例，期望是更广的语义能力；不是随机样本，不作为总体准确率。
- unchanged baseline：原有告警过滤率；本轮 50/50。
- injected baseline：新增合成告警检出与旧告警过滤；本轮 4/4 与 32/32。
- line shift：原有 32 条告警因注释插入产生 19 条重复“新增”报告，揭示行号定位局限。
- JSON/SARIF parity：关键字段一致性；未包含 SARIF 服务端上传测试。

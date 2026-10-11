# Static Inspect Agent · Python 工程静态巡检

一个可直接放进 CI 的 Python 源码巡检 CLI。它用 AST 规则给出可复现的发现，选择性调用 OpenAI 兼容模型解释长函数的代码坏味道，并输出 Markdown、JSON 或 SARIF 报告。**默认扫描过程只读取源码，不执行被扫描项目。**

## 为什么做这个项目

已有工程往往积累了不少历史问题，直接启用新检查会让 CI 一次性失败。本项目把“确定性门禁”和“模型建议”分开：先建立规则基线，后续只拦截新问题；模型不可用时，离线规则仍能正常产出报告。这个设计覆盖了 Python 后端 / AI Agent 岗位常见的 CLI 工程化、状态编排、外部服务降级和 CI 接入场景。

## 30 秒离线演示

需要 Python 3.12+。在仓库根目录执行：

```bash
python -m pip install -e ".[dev]"
inspect-agent examples/demo_project --no-llm --format json
```

演示项目包含四种问题。命令会返回 **1**（发现规则问题），JSON 里包含相对文件路径、行号、规则 ID 和级别：

```json
{
  "file": "bad.py",
  "line": 1,
  "rule": "wildcard_import",
  "rule_id": "R004",
  "severity": "warning",
  "desc": "通配导入会模糊名称来源，建议显式导入所需名称"
}
```

没有 API key 也能完成这个演示。`--no-llm` 保证全程不调用模型。
完整的 Markdown 输出见 [演示报告](examples/demo-report.md)。

## 使用方式

```bash
# 人类可读报告
inspect-agent ./my-project --no-llm -o report.md

# 脚本消费：stdout 只有 JSON，日志走 stderr
inspect-agent ./my-project --no-llm --format json

# SARIF 2.1.0，可供代码扫描平台消费
inspect-agent ./my-project --no-llm --format sarif -o report.sarif

# 首次接入老项目：保存当下已有的规则发现，命令成功返回 0
inspect-agent ./my-project --no-llm --write-baseline inspect-baseline.json

# 以后只报告新增规则问题
inspect-agent ./my-project --no-llm --baseline inspect-baseline.json
```

`-o/--output` 将报告写入文件；未指定时打印到 stdout（JSON / SARIF 固定 UTF-8 编码）。`-v/--verbose` 显示进度日志。退出码：`0` 无新规则问题，`1` 有规则问题，`2` 输入或输出错误。LLM 建议会显示在报告中，不决定退出码，也不进入 SARIF。

## 规则

| ID | 严重级别 | 检查 |
|---|---|---|
| E000 | error | 语法错误或源文件读取失败 |
| R001 | error | 裸 `except:` |
| R002 | error | `list` / `dict` / `set` 字面量作为默认参数 |
| R003 | warning | 直接调用 `eval(...)` |
| R004 | warning | `from module import *` |

规则是 AST 语法模式匹配。`warning` 表示需要人工复核，不声称能证明漏洞。添加规则时在 `rules.py` 注册检查函数并为它添加正反例测试。

## 项目配置

在**被扫描项目**的 `pyproject.toml` 中添加配置：

```toml
[tool.inspect-agent]
long-func-threshold = 30
llm-batch-size = 5
skip-dirs = [".git", "__pycache__", ".venv", "venv", "node_modules"]
```

这三个字段都可省略。`skip-dirs` 如提供，会替换默认跳过目录名单。错误类型或非正数阈值会返回退出码 2。

## 可选的 LLM 分析

不带 `--no-llm` 时，工具结合有效行数、嵌套、分支和重复语句等指标挑选函数，再将源码片段发往配置的 OpenAI 兼容服务。默认使用 DeepSeek；可以通过环境变量改为其他兼容端点：

```text
DEEPSEEK_API_KEY=your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
```

也可以将这些变量放在本地 `.env`（已被 `.gitignore` 忽略）。模型请求失败或响应畸形时，规则报告仍可用。被扫描代码可能含敏感信息；开启模型前请确认允许向目标服务发送函数片段。

## 架构与验证

`scan → rule_check → llm_analyze → report` 是 LangGraph 状态流；CLI 在图执行前验证配置，在图执行后处理基线、SARIF 和退出码。实现边界、基线取舍及安全限制见 [架构说明](docs/architecture.md)。

```bash
python -m pytest -q
python -m build --wheel
inspect-agent . --no-llm --format json
```

最后一条会扫到 `examples/demo_project` 中故意留下的问题，因此预计返回 1。测试覆盖 AST 正反例、读取故障、LLM 超时和畸形响应、目标项目配置、CLI 错误码、基线及 SARIF。GitHub Actions 在 Ubuntu 与 Windows 的 Python 3.12 / 3.13 上运行测试、构建和已安装命令的烟雾验证。

## 后续方向

- 通过人工复核和留出样本继续改善建议质量。
- 扩展跨文件分析和流敏感名称解析。
- 为缓存增加容量管理，为供应商请求增加精确 token 计量和限流协调。

## v0.3 改进

- 规则检查与 LLM 候选提取复用一次 AST 解析；支持源码编码声明及源码缓存。
- LLM 按函数缓存结果，限制并发、批次大小和逻辑调用预算；失败、跳过、未完成状态明确展示。
- 建议必须包含可在当前函数中核对的原文证据；可按需读取同文件辅助函数定义，最多补查一轮。
- v2 基线采用结构指纹，支持注释插入后继续匹配；兼容读取 v1 基线。
- 新增 `--no-cache`、`--refresh-cache`、`--cache-dir`。默认缓存位于被扫描项目的 `.inspect-cache/`，包含源码片段，建议加入目标仓库的忽略列表。

完整改动原理、配置、示例和限制见 [改动说明](docs/improvements-2026-10-10.md)。退出码仍由规则决定，LLM 完成度请查看 JSON 的 `llm_stats` 和 `llm_outcomes`。

## 真实项目验证

2026-10-09 对 Requests、Flask、Click、HTTPX、Django 的固定发布版本进行了离线评测：生产包覆盖 965 个 Python 文件、192,774 行文本；完整仓库覆盖 3,059 个文件、557,478 行文本，两种范围不可相加。Django 生产包端到端扫描中位耗时 3.30 秒（首次观测后重复 5 次）。

验证了重复输出一致性、JSON/SARIF 关键字段一致性、50 条旧告警的基线过滤和 4 类新增合成问题检出。同时发现包入口导出告警噪声、裸 except 提示语不准确，以及注释插入导致旧告警重新报告等局限。告警数不等于缺陷数。

2026-10-10 补充真实 LLM 验证：10 次正式调用全部成功，5 个项目的 25 个函数产生 63 条建议；Codex 源码复核分为 13 条具体改进线索、36 条低优先级或设计取舍建议、14 条不准确或上下文不足。共使用 23,592 tokens。同批函数三次产生 10、5、11 条建议，4 个负对照均收到建议，表明仍需要人工复核；以上不是生产准确率。详见 [LLM 补充报告](docs/validation/2026-10-10-llm-report.md)。

详细数据、源码复核与适用边界见 [验证报告](docs/validation/2026-10-09-report.md)，复现脚本和原始证据见 [benchmarks](benchmarks/README.md)。

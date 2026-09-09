# 工程静态巡检 Agent

一个基于 **LangGraph** 的 Python 工程静态巡检工具：预置规则引擎做确定性检查（AST 分析），LLM 做代码坏味道的语义解读，输出 Markdown / JSON 巡检报告。

静态读取源码，**不运行被检查的代码**；目前只支持 Python 工程。

## 特性

- **规则引擎（确定性）**：基于 `ast` 模块解析语法树，零误报、零成本、离线可跑
- **LLM 语义分析（建议性）**：把长函数片段交给大模型解读坏味道，分批并发调用，自动超时重试
- **降级友好**：未配置 API key、网络超时、模型返回畸形 JSON 都不会中断巡检
- **CI 就绪**：发现问题时退出码为 1，可直接接入 GitHub Actions / pre-commit
- **可配置**：阈值、批量大小、跳过目录均在 `pyproject.toml` 中配置
- **双格式输出**：Markdown（给人看）/ JSON（给程序读）

## 安装

需要 Python 3.12+。

```bash
# 克隆后在项目目录内：
pip install -e .

# 开发模式（额外安装 pytest）：
pip install -e ".[dev]"
```

可选：配置 LLM 语义分析（不配也能用，规则引擎照常工作）。在项目根目录创建 `.env`：

```bash
DEEPSEEK_API_KEY=sk-你的密钥
# 以下两项有默认值，通常不用改
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
```

## 快速开始

```bash
# 巡检当前目录（Markdown 报告打印到控制台）
inspect-agent .

# 等价写法（未安装时）
python main.py .

# 只跑规则引擎，不调用 LLM（免费、秒级）
inspect-agent . --no-llm

# 报告写入文件
inspect-agent ./my-project -o report.md

# JSON 格式输出（配合 jq 等工具使用）
inspect-agent . --format json

# 显示详细进度日志
inspect-agent . -v
```

退出码：`0` = 未发现问题；`1` = 发现问题（CI 据此判断成败）。

## 规则清单

规则引擎基于 AST 做确定性检查，每条规则有稳定 ID 和严重级别（`error` / `warning` / `info`）：

| 规则 ID | 名称 | 级别 | 说明 |
|---------|------|------|------|
| R001 | `bare_except` | error | 裸 `except:` 会吞掉所有异常（含 `KeyboardInterrupt`），应写 `except Exception:` |
| R002 | `mutable_default_arg` | error | 函数默认参数为 `list`/`dict`/`set`，所有调用共享同一可变对象 |
| E000 | `syntax_error` | error | 文件无法解析（语法错误、编码错误、无读取权限），跳过检查但记录在案 |

LLM 语义分析不产生规则 ID，其发现标记为"建议"，与规则引擎的确定性结论分区展示。

## 命令行参数

| 参数 | 说明 |
|------|------|
| `project_path` | （必填）待巡检的项目根目录 |
| `-o, --output FILE` | 报告写入文件（默认只打印到控制台） |
| `--no-llm` | 禁用 LLM 语义分析，只跑规则引擎 |
| `--format {markdown,json}` | 输出格式，默认 `markdown` |
| `-v, --verbose` | 显示各节点的详细进度日志 |

JSON 输出结构：

```json
{
  "project": ".",
  "files_scanned": 10,
  "rule_findings": [
    {"file": "bad.py", "line": 3, "rule": "bare_except",
     "rule_id": "R001", "severity": "error", "desc": "..."}
  ],
  "llm_findings": [
    {"file": "long.py", "line": 20, "smell": "函数过长", "reason": "..."}
  ]
}
```

## 配置

在被巡检项目（或工具自身）的 `pyproject.toml` 中添加：

```toml
[tool.inspect-agent]
long-func-threshold = 30   # 函数超过多少行才交给 LLM
llm-batch-size = 5         # LLM 每批分析的函数数量（分批并发）
skip-dirs = [".git", "__pycache__", ".venv", "venv", "node_modules"]
```

LLM 相关密钥通过环境变量 / `.env` 文件提供，不写入代码（见"安装"一节）。

## CI 集成示例

GitHub Actions（`.github/workflows/inspect.yml`）：

```yaml
name: static-inspect
on: [push, pull_request]
jobs:
  inspect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install git+https://github.com/<你>/static-inspect-agent.git
      # 规则引擎离线运行；如需 LLM 分析，在仓库 Secrets 配置 DEEPSEEK_API_KEY
      - run: inspect-agent . --no-llm -o report.md
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: inspect-report
          path: report.md
```

发现问题时进程退出码为 1，CI 自动标红；报告作为 artifact 保留供查看。

## 架构

基于 LangGraph 的线性状态流转，所有节点共享一份 `InspectState`：

```
project_path
     │
     ▼
┌─────────┐   py_files    ┌────────────┐  rule_findings  ┌─────────────┐
│  scan   │ ───────────▶ │ rule_check │ ──────────────▶ │ llm_analyze │
│ 扫描.py │              │ AST 规则引擎 │                 │ 分批并发 LLM │
└─────────┘              └────────────┘                  └──────┬──────┘
                                                               │ llm_findings
                                                               ▼
                                                        ┌────────────┐
                                                        │   report   │
                                                        │ Markdown/  │
                                                        │ JSON 报告   │
                                                        └────────────┘
```

**混合架构的分工原则**：

- **规则引擎**（确定性）：AST 匹配，结果 100% 可复现，负责"明确的问题"（语法错误、危险写法）。离线、免费、零误判。
- **LLM 语义分析**（建议性）：只处理规则难以判断的"代码坏味道"（命名、职责、嵌套）。结果会在报告中分区呈现。
- 任一环节失败，前面环节的成果不丢失：LLM 超时/未配置/返回畸形都不影响规则引擎的结论出现在报告里。

## 安全与隐私说明

- LLM 分析会把**超过阈值的函数源码**发送到 DeepSeek API；规则引擎完全离线，`--no-llm` 可全程不联网。
- LLM 的返回内容不可信：代码对返回 JSON 做类型校验和越界检查，任何畸形响应只会被跳过，不会影响规则引擎结果。
- 被扫描代码若被恶意构造（如注释中嵌入指令），理论上存在 prompt injection 风险——因此 LLM 发现始终标记为"建议"。需要**确定性阻断**的 CI 场景建议使用 `--no-llm`，让退出码只由规则引擎决定；开启 LLM 时其发现同样会计入退出码。
- API 密钥只从环境变量 / `.env` 读取，`.env` 不应提交版本库。

## 开发与测试

```bash
pip install -e ".[dev]"
python -m pytest -v          # 10 个单元测试：规则引擎 6 个 + LLM 节点 4 个
```

LLM 相关测试全部使用 mock 替身（模拟超时、畸形 JSON、分批并发），不发起真实网络请求。

## Roadmap

- [ ] baseline 基线机制：首次全量结果存为基线，后续只报告新增问题（老项目渐进式接入）
- [ ] SARIF 输出格式，对接 GitHub 代码扫描的行内注释
- [ ] 支持本地模型（Ollama 等 OpenAI 兼容端点），源码不出内网
- [ ] 扩充规则库（`eval` 使用、通配导入、过深嵌套等），规则可在配置中单独开关
- [ ] rules 与 llm 节点复用同一次 AST 解析，消除重复 parse

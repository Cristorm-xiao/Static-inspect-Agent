# 工程静态巡检报告

## 摘要

- 扫描文件：2 个
- 规则引擎发现：4 处
- LLM 语义发现：0 处

---

## 规则引擎发现（AST 确定性检查）

### `bad.py`

| 行号 | 级别 | 规则 | 说明 |
|------|------|------|------|
| 1 | warning | wildcard_import | 通配导入会模糊名称来源，建议显式导入所需名称 |
| 4 | error | mutable_default_arg | 函数 accumulate 的默认参数是可变对象，调用间会共享状态 |
| 10 | warning | eval_call | 直接调用 eval 可能执行不可信输入，请确认输入来源并优先使用安全解析方式 |
| 11 | error | bare_except | 裸 except 会吞掉所有异常（含 KeyboardInterrupt），建议 except Exception: |

## LLM 语义分析

无发现（或未启用 LLM 分析）。


---
name: discount-credit-workflow
description: "Build and run an end-to-end discount-credit (贴现授信) pipeline in OpenCloud: confirm company name, perform web research to backfill required fields, return a field confirmation sheet, one-click generate the required credit-report files and customer material list, then send two emails (manager + customer). Use when packaging and operating a reusable credit workflow skill with strict naming and output conventions."
---

# Discount Credit Workflow

## Core Flow

执行顺序固定为 6 步：

1. 确认主体名称（你口述里的“节名称”按 `企业名称` 字段处理）。
2. 按企业名称做联网检索，回填必填字段。
3. 生成并确认 `01_字段确认单.md`。
4. 一键生成授信材料（至少两份：`调查报告`、`附件4`；可同时生成《操作规范》）。
5. 生成两封邮件草稿（客户经理 + 客户）。
6. 发送两封邮件（OpenCloud/n8n 节点发送，或本地 SMTP 直发）。

## Naming Rule

所有输出文件统一命名为：

`企业名称-文件名`

例如：
- `厦门某某公司-调查报告-填报版.xlsx`
- `厦门某某公司-附件4-贴现额度授信审批文件移交清单-填报版.xlsx`
- `厦门某某公司-客户盖章材料清单.txt`

## Required Checklist

必填字段见 [references/required-checklist.md](references/required-checklist.md)。

如果必填缺失，默认应停止正式生成（除非你明确启用强制继续）。

## One-Click Command

推荐使用一键流水线脚本：

```bash
python scripts/run_discount_pipeline.py assets/input_sample_minimal.json --yes --output 输出结果
```

带邮件发送（SMTP）示例：

```bash
python scripts/run_discount_pipeline.py assets/input_sample_minimal.json \
  --yes \
  --send-email \
  --manager-to manager@example.com \
  --customer-to customer@example.com \
  --smtp-host smtp.example.com \
  --smtp-user bot@example.com
```

## Scripts

- `scripts/run_discount_pipeline.py`：总控流程（确认单 -> 生成 -> 可选发信）。
- `scripts/generate_discount_docs.py`：模板填充引擎（xlsx/docx/txt + 输出清单 manifest）。
- `scripts/interactive_discount_assistant.py`：交互式流程（适合人工逐步确认）。

## OpenCloud / n8n

编排建议见 [references/n8n-flow.md](references/n8n-flow.md)。
部署与仓库发布见 [references/deployment.md](references/deployment.md)。

# n8n / OpenCloud Flow（贴现授信）

## Target

单次触发后完成以下结果：

1. 生成字段确认单（`企业名称-01_字段确认单.md`）
2. 生成授信材料（至少两份报告 + 材料清单 + 邮件草稿）
3. 发送两封邮件（客户经理 + 客户）

## Node Sequence

1. `Webhook / Form Trigger`
- 入参至少包含：`企业名称`、`申请日期`、`上一年度营业收入`、`净资产`

2. `Company Search`（可选）
- 根据 `企业名称` 联网检索工商字段
- 产出 `enrich.json`

3. `Execute Command`（字段确认 + 生成）

```bash
python /path/to/discount-credit-workflow/scripts/run_discount_pipeline.py \
  /path/to/input.json \
  --auto-web-search \
  --enrich-json /path/to/enrich.json \
  --yes \
  --output /path/to/output
```

4. `IF`（校验缺失字段）
- 读取 `企业名称-02_流水线执行结果.json`
- 若 `missing_fields` 非空：回退给人工补录

5. `Read Files`
- 读取输出目录下 `企业名称-输出清单.json`
- 取报告附件和草稿

6. `Send Email (Manager)`
- 收件人：客户经理
- 附件：授信材料 + 客户盖章材料清单

7. `Send Email (Customer)`
- 收件人：客户联系人
- 附件：客户盖章材料清单

## Output Contract

命名统一为：`企业名称-文件名`。

必须有：
- `企业名称-联网检索补充.json`（启用自动检索时）
- `企业名称-联网检索结果.md`（启用自动检索时）
- `企业名称-调查报告-填报版.xlsx`
- `企业名称-附件4-贴现额度授信审批文件移交清单-填报版.xlsx`
- `企业名称-客户盖章材料清单.txt`
- `企业名称-邮件草稿-客户经理.txt`
- `企业名称-邮件草稿-客户.txt`
- `企业名称-输出清单.json`

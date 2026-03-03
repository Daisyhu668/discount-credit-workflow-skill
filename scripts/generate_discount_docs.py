#!/usr/bin/env python3
"""步骤2：贴现模板引擎，负责批量生成校对清单与模板成品。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, Sequence, Tuple
from zipfile import ZIP_DEFLATED, ZipFile


# Skill 内脚本默认以当前工作目录作为项目根目录，便于在 OpenCloud 挂载仓库后直接执行。
PROJECT_DIR = Path.cwd()
SCRIPT_DIR = Path(__file__).resolve().parent
ASSETS_DIR = SCRIPT_DIR.parent / "assets"
DEFAULT_USER_TEMPLATE = PROJECT_DIR / "user_inputs_template.txt"
if not DEFAULT_USER_TEMPLATE.exists():
    DEFAULT_USER_TEMPLATE = ASSETS_DIR / "input_template_minimal.txt"
TEMPLATES_OVERRIDE = PROJECT_DIR / "templates"
if not TEMPLATES_OVERRIDE.exists():
    TEMPLATES_OVERRIDE = ASSETS_DIR / "templates"
DEFAULT_TEMPLATES_DIR = TEMPLATES_OVERRIDE
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "输出结果"

PLACEHOLDER_PATTERN = re.compile(r"{{(.*?)}}")
UNRESOLVED_PLACEHOLDER_PATTERN = re.compile(r"\{\{[^{}]*\}\}")

LABEL_ALIASES: Dict[str, str] = {
    "上一年度主营产品占比描述": "上一年度主营产品占比",
    "本年度主营产品占比描述": "本年度主营产品占比",
    "采购渠道概述": "采购渠道",
    "销售渠道概述": "销售渠道",
    "本年营业收入": "本年度营业收入",
}
for idx in range(1, 6):
    LABEL_ALIASES[f"上游供货商{idx}主营品类"] = f"上游供货商{idx}品类"
    LABEL_ALIASES[f"下游客户{idx}采购产品"] = f"下游客户{idx}产品"

REQUIRED_FIELDS: Sequence[str] = (
    "企业名称",
    "统一社会信用代码",
    "注册时间",
    "注册地址",
    "法定代表人",
    "行业类型",
    "申请日期",
    "上一年度营业收入",
    "净资产",
)

COMPUTED_SUMMARY_FIELDS = ["申请日期+1年", "上一年度营业收入*80%", "净资产*12", "授信额度"]


@dataclass
class TemplatePaths:
    survey: Path
    attachment4: Path
    guideline: Path


@dataclass
class GenerationResult:
    checklist_path: Path
    snapshot_path: Path
    manifest_path: Path
    missing_fields: list[str]
    parse_warnings: list[str]
    outputs: list[Path]
    mapping: Dict[str, str]


@dataclass
class InputData:
    raw: Dict[str, str]

    def _require(self, key: str) -> str:
        if key not in self.raw or self.raw[key] in (None, ""):
            raise ValueError(f"缺少必填项：{key}")
        return str(self.raw[key])

    def numeric(self, key: str) -> float:
        value = self._require(key)
        try:
            return float(value)
        except ValueError as exc:
            raise ValueError(f"字段“{key}”必须为数字，当前值 {value!r}") from exc

    def date(self, key: str) -> date:
        value = self._require(key)
        try:
            parts = [int(part) for part in value.split("-")]
            if len(parts) != 3:
                raise ValueError
            return date(parts[0], parts[1], parts[2])
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"字段“{key}”需满足 YYYY-MM-DD 格式，当前值 {value!r}") from exc


def add_one_year(d: date) -> date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:
        return d.replace(year=d.year + 1, month=2, day=28)


def format_decimal(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".") if value % 1 else f"{int(value)}"


def escape_xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def safe_slug(text: str) -> str:
    if not text:
        return "用户清单"
    slug = re.sub(r'[\/:*?"<>|]+', "_", text.strip())
    slug = re.sub(r"\s+", "_", slug)
    return slug or "用户清单"


def prefixed_output_name(company: str, file_name: str) -> str:
    """按“企业名称-文件名”规则输出文件名。"""
    return f"{safe_slug(company)}-{file_name}"


def normalize_label(label: str) -> str:
    cleaned = re.sub(r"^[0-9.、\s]+", "", label)
    cleaned = cleaned.lstrip("-•·")
    cleaned = re.sub(r"（.*?）", "", cleaned)
    cleaned = cleaned.replace("(", "").replace(")", "")
    cleaned = cleaned.replace(" ", "")
    return cleaned.strip()


def guess_key_from_prefix(prefix: str) -> str | None:
    if not prefix:
        return None
    cleaned = re.sub(r"[:：]+\s*$", "", prefix)
    candidate = normalize_label(cleaned)
    if not candidate:
        return None
    return LABEL_ALIASES.get(candidate, candidate)


def _assign_value(
    store: Dict[str, str],
    key: str | None,
    raw_value: str,
    warnings: list[str],
    source_line: str,
) -> None:
    value = raw_value.strip()
    if value.startswith("{{") and value.endswith("}}"):
        value = value[2:-2].strip()
    if key:
        if value or key not in store:
            store[key] = value
    elif source_line.strip():
        warnings.append(source_line.strip())


def _split_line_value(line: str) -> Tuple[str | None, str]:
    idx = -1
    for sep in ("：", ":"):
        idx = line.find(sep)
        if idx != -1:
            break
    if idx == -1:
        return None, ""
    label = line[:idx]
    value = line[idx + 1 :]
    return guess_key_from_prefix(label), value


def parse_template_txt(path: Path) -> Tuple[Dict[str, str], list[str]]:
    text = path.read_text(encoding="utf-8")
    # 兼容“占位服务”导出格式：文本内包含字面量 \n / \r\n。
    if "\\n" in text and text.count("\n") <= 2:
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n")
    values: Dict[str, str] = {}
    warnings: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        matches = list(PLACEHOLDER_PATTERN.finditer(raw_line))
        if matches:
            for match in matches:
                prefix = raw_line[: match.start()]
                key = guess_key_from_prefix(prefix)
                _assign_value(values, key, match.group(1), warnings, raw_line)
            continue
        key, value = _split_line_value(raw_line)
        if key:
            _assign_value(values, key, value, warnings, raw_line)
        elif "{{" in raw_line and "}}" in raw_line:
            _assign_value(values, None, raw_line, warnings, raw_line)
    return values, warnings


def load_json_inputs(path: Path) -> Dict[str, str]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("输入文件必须是 JSON 对象")
    return {k: v for k, v in data.items()}


def load_inputs(path: Path) -> Tuple[Dict[str, str], list[str]]:
    if path.suffix.lower() == ".json":
        return load_json_inputs(path), []
    return parse_template_txt(path)


def compute_fields(raw: Dict[str, str]) -> Dict[str, str]:
    data = InputData(raw)
    sale_rev = data.numeric("上一年度营业收入")
    net_asset = data.numeric("净资产")
    apply_date = data.date("申请日期")

    rev_cap = sale_rev * 0.8
    net_cap = net_asset * 12
    credit_limit = min(rev_cap, net_cap)

    computed = {
        "申请日期": apply_date.strftime("%Y-%m-%d"),
        "申请日期+1年": add_one_year(apply_date).strftime("%Y-%m-%d"),
        "上一年度营业收入": format_decimal(sale_rev),
        "上一年度营业收入*80%": format_decimal(rev_cap),
        "净资产": format_decimal(net_asset),
        "净资产*12": format_decimal(net_cap),
        "授信额度": format_decimal(credit_limit),
    }

    # 如果实际经营地址未填写且勾选“同注册地址”，则默认复制注册地址。
    actual_addr_key = "实际经营地址"
    same_addr_flag = str(raw.get("实际地址是否同注册地址", "")).strip().lower()
    same_addr_flag = same_addr_flag in {"是", "yes", "y", "true", "1", "同注册地址", "一致"}
    if not raw.get(actual_addr_key) and same_addr_flag and raw.get("注册地址"):
        raw = {**raw, actual_addr_key: str(raw.get("注册地址"))}

    # 模板中若使用“本年营业收入”，则从“本年度营业收入”补齐。
    if raw.get("本年度营业收入") and not raw.get("本年营业收入"):
        raw = {**raw, "本年营业收入": str(raw.get("本年度营业收入"))}

    final = {**raw, **computed}
    return {k: str(v) for k, v in final.items()}


def collect_missing_fields(values: Dict[str, str]) -> list[str]:
    missing: list[str] = []
    for key in REQUIRED_FIELDS:
        if not values.get(key):
            missing.append(key)
    return missing


def render_user_template(
    template: Path,
    output: Path,
    values: Dict[str, str],
    *,
    missing: Sequence[str] = (),
    include_summary: bool = True,
) -> None:
    text = template.read_text(encoding="utf-8")
    result_lines: list[str] = []

    for line in text.splitlines():
        def repl(match: re.Match[str]) -> str:
            token = match.group(1).strip()
            if token in values:
                return values[token]
            prefix = line[: match.start()]
            key = guess_key_from_prefix(prefix)
            if key and key in values:
                return values[key]
            return ""

        result_lines.append(PLACEHOLDER_PATTERN.sub(repl, line))

    if include_summary:
        result_lines.append("")
        result_lines.append("六、脚本自动计算字段（仅供校对）")
        for key in COMPUTED_SUMMARY_FIELDS:
            result_lines.append(f"- {key}：{values.get(key, '')}")

        if missing:
            result_lines.append("")
            result_lines.append("七、待补字段（系统检测为空）")
            for key in missing:
                result_lines.append(f"- {key}")

    output.write_text("\\n".join(result_lines) + "\\n", encoding="utf-8")


def replace_placeholders(text: str, mapping: Dict[str, str]) -> str:
    for key, value in mapping.items():
        placeholder = f"{{{{{key}}}}}"
        if placeholder in text:
            text = text.replace(placeholder, escape_xml(value))
    # 未提供的可选字段统一清空，避免将占位符键名残留在成品文档内。
    return UNRESOLVED_PLACEHOLDER_PATTERN.sub("", text)


def fill_docx(template: Path, output: Path, mapping: Dict[str, str]) -> None:
    with ZipFile(template) as zin, ZipFile(output, "w", ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                text = data.decode("utf-8")
                # 兼容 docx 占位符被拆分到多个 runs 的情况。
                def repl(match: re.Match[str]) -> str:
                    raw = match.group(0)
                    token = re.sub(r"<[^>]+>", "", raw)
                    key = token.strip().lstrip("{").rstrip("}").strip()
                    return escape_xml(mapping.get(key, ""))

                text = re.sub(r"\{\{.*?\}\}", repl, text, flags=re.S)
                data = text.encode("utf-8")
            zout.writestr(item, data)


def fill_xlsx(template: Path, output: Path, mapping: Dict[str, str]) -> None:
    with ZipFile(template) as zin, ZipFile(output, "w", ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith(".xml"):
                text = data.decode("utf-8")
                text = replace_placeholders(text, mapping)
                data = text.encode("utf-8")
            zout.writestr(item, data)


def render_customer_material_list(output_dir: Path, mapping: Dict[str, str]) -> Path:
    company = mapping.get("企业名称", "客户")
    path = output_dir / prefixed_output_name(company, "客户盖章材料清单.txt")
    lines = [
        f"客户盖章材料清单（{company}）",
        "",
        "请按以下顺序准备并回传：",
        "一、我行固定版本材料",
        "1. 贴现申请书",
        "2. 征信授权书",
        "3. 贴现协议",
        "4. 承诺书",
        "",
        "二、客户资料",
        "1. 营业执照",
        "2. 公司章程",
        "3. 近两年及最近一个月纳税申报表",
        "4. 近两年及最近一期财务报表",
        "5. 法人身份证",
        "",
        "三、税务状态截图",
        "1. 纳税评级截图",
        "2. 纳税状态正常截图",
        "",
        "备注：经营与生产信息、上下游信息如暂缺，可由客户经理在授信报告内后补。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def collect_customer_templates(templates_dir: Path) -> list[Path]:
    candidates = [
        "客户-附件7申请书.xlsx",
        "客户-商业汇票贴现协议（卖方付息）（2024年2月版）.doc",
        "客户-厦门银行授管〔2024〕7号 附件2：厦门银行企业征信授权书（20230901启用）.doc",
    ]
    found: list[Path] = []
    for name in candidates:
        path = templates_dir / name
        if path.exists():
            found.append(path)
    # 兜底：任何包含“承诺书”的文件都加入
    for path in templates_dir.glob("*承诺书*"):
        if path not in found and path.is_file():
            found.append(path)
    return found


def copy_customer_templates(
    templates_dir: Path,
    output_dir: Path,
    company: str,
) -> list[Path]:
    outputs: list[Path] = []
    for src in collect_customer_templates(templates_dir):
        target = output_dir / prefixed_output_name(company, src.name)
        target.write_bytes(src.read_bytes())
        outputs.append(target)
    return outputs


def render_email_drafts(
    output_dir: Path,
    mapping: Dict[str, str],
    generated_docs: Sequence[Path],
    customer_material_list: Path,
    customer_templates: Sequence[Path],
) -> list[Path]:
    company = mapping.get("企业名称", "客户")
    approval_notes = mapping.get("审批注意事项", "").strip()
    manager_to = mapping.get("客户经理收件人", "客户经理（待填写）")
    manager_cc = mapping.get("客户经理抄送", "风险经理/审批岗（待填写）")
    customer_to = mapping.get("客户收件人", "客户联系人（待填写）")
    customer_cc = mapping.get("客户抄送", "客户经理（待填写）")

    manager_mail = output_dir / prefixed_output_name(company, "邮件草稿-客户经理.txt")
    manager_lines = [
        f"邮件主题：[线上贴] {company} 授信贴现材料（待客户经理完善）",
        f"收件人：{manager_to}",
        f"抄送：{manager_cc}",
        "",
        "正文：",
        f"{company} 的贴现授信材料已自动生成，请补充并复核后发起审批。",
        "",
        "请重点补充：",
        "1. 授信报告中的经营与生产信息（如有）",
        "2. 授信报告中的上下游明细（如有）",
        "3. 审批要点及风险提示",
        "",
        "建议附件：",
    ]
    manager_lines.extend(f"- {path.name}" for path in generated_docs)
    manager_lines.append(f"- {customer_material_list.name}")
    manager_lines.extend(f"- {path.name}" for path in customer_templates)
    if approval_notes:
        manager_lines.append("")
        manager_lines.append(f"审批注意事项：{approval_notes}")
    manager_mail.write_text("\n".join(manager_lines) + "\n", encoding="utf-8")

    customer_mail = output_dir / prefixed_output_name(company, "邮件草稿-客户.txt")
    customer_lines = [
        f"邮件主题：[请盖章回传] {company} 贴现授信申请材料",
        f"收件人：{customer_to}",
        f"抄送：{customer_cc}",
        "",
        "正文：",
        f"您好，{company} 贴现授信申请进入资料确认阶段。",
        "请按附件《客户盖章材料清单》完成盖章并回传。",
        "如对材料项有疑问，请直接联系客户经理。",
        "",
        "建议附件：",
        f"- {customer_material_list.name}",
    ]
    customer_lines.extend(f"- {path.name}" for path in customer_templates)
    customer_mail.write_text("\n".join(customer_lines) + "\n", encoding="utf-8")

    return [manager_mail, customer_mail]


def prepare_user_checklist(
    company: str | None,
    output_dir: Path,
    user_template: Path = DEFAULT_USER_TEMPLATE,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    checklist_path = output_dir / prefixed_output_name(company or "待填写", "00_用户清单.txt")
    render_user_template(
        user_template,
        checklist_path,
        {"企业名称": company or ""},
        include_summary=False,
    )
    return checklist_path


def generate_documents(
    input_file: Path,
    output_dir: Path,
    user_template: Path,
    templates: TemplatePaths,
    *,
    preview: bool,
) -> GenerationResult:
    raw_values, parse_warnings = load_inputs(input_file)
    mapping = compute_fields(raw_values)
    output_dir.mkdir(parents=True, exist_ok=True)

    missing_fields = collect_missing_fields(mapping)
    company = mapping.get("企业名称", "用户清单")
    checklist_path = output_dir / prefixed_output_name(company, "00_用户清单.txt")
    render_user_template(
        user_template,
        checklist_path,
        mapping,
        missing=missing_fields,
        include_summary=True,
    )

    snapshot_path = output_dir / prefixed_output_name(company, "_values_snapshot.json")
    snapshot_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

    outputs: list[Path] = []
    if not preview:
        survey_output = output_dir / prefixed_output_name(company, "调查报告-填报版.xlsx")
        fill_xlsx(templates.survey, survey_output, mapping)
        outputs.append(survey_output)

        attachment4_output = output_dir / prefixed_output_name(
            company,
            "附件4-贴现额度授信审批文件移交清单-填报版.xlsx",
        )
        fill_xlsx(templates.attachment4, attachment4_output, mapping)
        outputs.append(attachment4_output)

        guideline_output = output_dir / prefixed_output_name(
            company,
            "关于暂时采用线下方式审批银行承兑汇票贴现限额的操作规范-填报版.docx",
        )
        fill_docx(templates.guideline, guideline_output, mapping)
        outputs.append(guideline_output)

        customer_templates = copy_customer_templates(DEFAULT_TEMPLATES_DIR, output_dir, company)
        outputs.extend(customer_templates)

        customer_material_list = render_customer_material_list(output_dir, mapping)
        outputs.append(customer_material_list)

        email_drafts = render_email_drafts(
            output_dir,
            mapping,
            [survey_output, attachment4_output, guideline_output],
            customer_material_list,
            customer_templates,
        )
        outputs.extend(email_drafts)

    manifest_path = output_dir / prefixed_output_name(company, "输出清单.json")
    manifest_payload = {
        "企业名称": company,
        "required_fields": list(REQUIRED_FIELDS),
        "missing_fields": list(missing_fields),
        "parse_warnings": parse_warnings,
        "checklist_path": str(checklist_path),
        "snapshot_path": str(snapshot_path),
        "outputs": [str(path) for path in outputs],
        "preview": preview,
    }
    manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return GenerationResult(
        checklist_path=checklist_path,
        snapshot_path=snapshot_path,
        manifest_path=manifest_path,
        missing_fields=list(missing_fields),
        parse_warnings=parse_warnings,
        outputs=outputs,
        mapping=mapping,
    )


def prompt_confirm(msg: str, default_yes: bool = True) -> bool:
    hint = "Y/n" if default_yes else "y/N"
    choice = input(f"{msg} ({hint}): ").strip().lower()
    if not choice:
        return default_yes
    return choice.startswith("y")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="贴现模板生成脚本")
    parser.add_argument("input_file", nargs="?", help="用户输入 JSON/TXT 文件")
    parser.add_argument("--preview", action="store_true", help="仅生成校对清单，不输出模板")
    parser.add_argument("--prepare", action="store_true", help="生成空白用户清单后退出")
    parser.add_argument("--company", help="prepare 模式下的企业名称")
    parser.add_argument("--yes", action="store_true", help="自动确认生成模板")
    parser.add_argument(
        "--output",
        "--out",
        dest="output_dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help='输出目录（默认：脚本所在目录"输出结果"）',
    )
    parser.add_argument(
        "--user-template",
        dest="user_template",
        type=Path,
        default=DEFAULT_USER_TEMPLATE,
        help="用户清单模板路径",
    )
    parser.add_argument(
        "--survey",
        dest="survey_template",
        type=Path,
        default=DEFAULT_TEMPLATES_DIR / "调查报告.xlsx",
        help="调查报告模板路径",
    )
    parser.add_argument(
        "--attachment4",
        dest="attachment4_template",
        type=Path,
        default=DEFAULT_TEMPLATES_DIR / "附件4：贴现额度授信审批文件移交清单.xlsx",
        help="附件4 模板路径",
    )
    parser.add_argument(
        "--guideline",
        dest="guideline_template",
        type=Path,
        default=DEFAULT_TEMPLATES_DIR / "关于暂时采用线下方式审批银行承兑汇票贴现限额的操作规范2023.12.18修订.docx",
        help="《操作规范》模板路径",
    )
    return parser.parse_args(argv[1:])


def main(argv: Sequence[str]) -> None:
    args = parse_args(argv)

    if args.prepare:
        checklist = prepare_user_checklist(args.company, Path(args.output_dir), Path(args.user_template))
        print(f"[OK] 待填写用户清单已生成：{checklist}")
        print("请补充全部字段后，重新运行脚本进行校对/生成。")
        return

    if not args.input_file:
        print("请提供输入文件，或使用 --prepare 生成清单。", file=sys.stderr)
        sys.exit(1)

    input_path = Path(args.input_file).resolve()
    if not input_path.exists():
        print(f"未找到输入文件：{input_path}", file=sys.stderr)
        sys.exit(1)

    templates = TemplatePaths(
        survey=Path(args.survey_template).resolve(),
        attachment4=Path(args.attachment4_template).resolve(),
        guideline=Path(args.guideline_template).resolve(),
    )

    user_template = Path(args.user_template).resolve()
    output_dir = Path(args.output_dir).resolve()

    preview_mode = args.preview
    force_followup = False
    if not preview_mode and not args.yes:
        print("[INFO] 将先生成校对清单，确认后继续生成模板。")
        preview_mode = True
        force_followup = True

    result = generate_documents(
        input_path,
        output_dir,
        user_template,
        templates,
        preview=preview_mode,
    )

    if result.missing_fields:
        print("[WARN] 以下字段为空：")
        for key in result.missing_fields:
            print(f" - {key}")
    if result.parse_warnings:
        print("[WARN] 无法识别的条目：")
        for line in result.parse_warnings:
            print(f" - {line}")

    print(f"[OK] 用户校验清单：{result.checklist_path}")
    print(f"[OK] 输出清单(JSON)：{result.manifest_path}")

    if preview_mode:
        print("[INFO] 已生成校对清单；请核对/补充内容后再执行正式生成。")
        if force_followup and prompt_confirm("是否立即基于当前清单继续生成模板?", default_yes=False):
            result = generate_documents(
                input_path,
                output_dir,
                user_template,
                templates,
                preview=False,
            )
        else:
            return

    if not result.outputs:
        print("[INFO] 无模板输出（可能处于预览模式）。")
        return

    print("已生成以下模板文件：")
    for path in result.outputs:
        print(f" - {path}")


if __name__ == "__main__":
    main(sys.argv)

#!/usr/bin/env python3
"""贴现授信一键流程：字段确认 -> 模板生成 -> 可选邮件发送。"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from generate_discount_docs import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TEMPLATES_DIR,
    DEFAULT_USER_TEMPLATE,
    REQUIRED_FIELDS,
    TemplatePaths,
    collect_missing_fields,
    compute_fields,
    generate_documents,
    load_inputs,
    prefixed_output_name,
)
from search_company_web import run_search  # noqa: E402


def prompt_confirm(msg: str, default_yes: bool = False) -> bool:
    hint = "Y/n" if default_yes else "y/N"
    choice = input(f"{msg} ({hint}): ").strip().lower()
    if not choice:
        return default_yes
    return choice.startswith("y")


def parse_comma_list(text: str | None) -> list[str]:
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def load_optional_json(path: Path | None) -> Dict[str, str]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"附加字段文件必须是 JSON 对象: {path}")
    return {str(k): "" if v is None else str(v) for k, v in data.items()}


def render_confirmation_sheet(
    output_dir: Path,
    mapping: Dict[str, str],
    missing_fields: Sequence[str],
    *,
    research_note: str,
) -> Path:
    company = mapping.get("企业名称", "待确认主体")
    path = output_dir / prefixed_output_name(company, "01_字段确认单.md")
    lines: list[str] = [
        f"# 字段确认单（{company}）",
        "",
        "## 必填字段核对",
    ]

    for key in REQUIRED_FIELDS:
        value = mapping.get(key, "").strip()
        status = "[x]" if value else "[ ]"
        lines.append(f"- {status} {key}: `{value}`")

    lines.extend([
        "",
        "## 自动计算字段",
        f"- 申请日期+1年: `{mapping.get('申请日期+1年', '')}`",
        f"- 上一年度营业收入*80%: `{mapping.get('上一年度营业收入*80%', '')}`",
        f"- 净资产*12: `{mapping.get('净资产*12', '')}`",
        f"- 授信额度: `{mapping.get('授信额度', '')}`",
        "",
        "## 结论",
    ])

    if missing_fields:
        lines.append("- 仍有缺失字段，暂不建议发起正式生成。")
    else:
        lines.append("- 必填字段已齐全，可进入模板生成和邮件发送。")

    if research_note.strip():
        lines.extend([
            "",
            "## 联网检索备注",
            research_note.strip(),
        ])

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def parse_email_draft(path: Path) -> tuple[str, str]:
    subject = ""
    body_lines: list[str] = []
    in_body = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("邮件主题："):
            subject = line.split("：", 1)[1].strip()
            continue
        if line.startswith("正文："):
            in_body = True
            continue
        if in_body:
            body_lines.append(line)
    if not subject:
        subject = path.stem
    body = "\n".join(body_lines).strip() or "请见附件。"
    return subject, body


def build_message(
    sender: str,
    to_list: Sequence[str],
    cc_list: Sequence[str],
    subject: str,
    body: str,
    attachments: Sequence[Path],
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject
    msg.set_content(body)

    for path in attachments:
        payload = path.read_bytes()
        msg.add_attachment(
            payload,
            maintype="application",
            subtype="octet-stream",
            filename=path.name,
        )
    return msg


def send_message(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    use_starttls: bool,
    msg: EmailMessage,
) -> None:
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.ehlo()
        if use_starttls:
            server.starttls()
            server.ehlo()
        if smtp_user:
            server.login(smtp_user, smtp_password)
        server.send_message(msg)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="贴现授信一键流程脚本")
    parser.add_argument("input_file", type=Path, help="输入 JSON/TXT 文件")
    parser.add_argument("--enrich-json", type=Path, help="可选：联网检索补充字段 JSON")
    parser.add_argument("--auto-web-search", action="store_true", help="根据企业名称自动联网检索并回填")
    parser.add_argument("--web-search-results", type=int, default=8, help="自动联网检索保留条数")
    parser.add_argument("--research-note", default="", help="可选：记录联网检索结论")
    parser.add_argument("--allow-missing", action="store_true", help="允许必填字段缺失也继续生成")
    parser.add_argument("--yes", action="store_true", help="跳过人工确认")
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

    parser.add_argument("--send-email", action="store_true", help="生成后直接发邮件")
    parser.add_argument("--manager-to", help="客户经理邮箱，多个用逗号分隔")
    parser.add_argument("--manager-cc", help="客户经理抄送邮箱，多个用逗号分隔")
    parser.add_argument("--customer-to", help="客户邮箱，多个用逗号分隔")
    parser.add_argument("--customer-cc", help="客户抄送邮箱，多个用逗号分隔")
    parser.add_argument("--smtp-host", help="SMTP 主机")
    parser.add_argument("--smtp-port", type=int, default=587, help="SMTP 端口")
    parser.add_argument("--smtp-user", help="SMTP 账号")
    parser.add_argument("--smtp-password-env", default="SMTP_PASSWORD", help="SMTP 密码环境变量名")
    parser.add_argument("--smtp-no-starttls", action="store_true", help="禁用 STARTTLS")
    parser.add_argument("--smtp-from", help="发件人地址，默认使用 --smtp-user")

    return parser.parse_args(argv[1:])


def main(argv: Sequence[str]) -> None:
    args = parse_args(argv)

    input_path = args.input_file.resolve()
    if not input_path.exists():
        print(f"[ERR] 未找到输入文件: {input_path}", file=sys.stderr)
        sys.exit(1)

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_values, parse_warnings = load_inputs(input_path)
    auto_enrich: Dict[str, str] = {}
    auto_note = ""
    auto_enrich_path: Path | None = None
    auto_note_path: Path | None = None

    if args.auto_web_search:
        company_name = str(raw_values.get("企业名称", "")).strip()
        if not company_name:
            print("[ERR] 启用 --auto-web-search 时，输入必须包含“企业名称”。", file=sys.stderr)
            sys.exit(1)
        auto_enrich, auto_note = run_search(company_name, max_results=args.web_search_results)
        auto_enrich_path = output_dir / prefixed_output_name(company_name, "联网检索补充.json")
        auto_note_path = output_dir / prefixed_output_name(company_name, "联网检索结果.md")
        auto_enrich_path.write_text(json.dumps(auto_enrich, ensure_ascii=False, indent=2), encoding="utf-8")
        auto_note_path.write_text(auto_note.strip() + "\n", encoding="utf-8")
        print(f"[OK] 自动检索补充: {auto_enrich_path}")
        print(f"[OK] 自动检索备注: {auto_note_path}")

    enrich_values = load_optional_json(args.enrich_json.resolve() if args.enrich_json else None)
    # 优先级：原始输入 < 自动检索 < 人工补充文件
    merged_values = {**raw_values, **auto_enrich, **enrich_values}

    try:
        mapping = compute_fields(merged_values)
    except ValueError as exc:
        print(f"[ERR] 字段校验失败: {exc}", file=sys.stderr)
        sys.exit(1)

    missing_fields = collect_missing_fields(mapping)
    company = mapping.get("企业名称", "待确认主体")
    combined_note = args.research_note.strip()
    if auto_note.strip():
        combined_note = (
            (combined_note + "\n\n" + auto_note.strip())
            if combined_note
            else auto_note.strip()
        )
    confirmation_path = render_confirmation_sheet(
        output_dir,
        mapping,
        missing_fields,
        research_note=combined_note,
    )

    if parse_warnings:
        print("[WARN] 输入清单中存在未识别条目:")
        for item in parse_warnings:
            print(f" - {item}")

    print(f"[OK] 字段确认单: {confirmation_path}")

    if missing_fields and not args.allow_missing:
        print("[ERR] 必填字段未补齐，已停止。可先补齐后重试，或使用 --allow-missing 强制继续。")
        for key in missing_fields:
            print(f" - {key}")
        sys.exit(2)

    if not args.yes and not prompt_confirm("字段确认完成，继续生成授信材料吗?", default_yes=False):
        print("[INFO] 已取消生成。")
        return

    merged_input_path = output_dir / prefixed_output_name(company, "_merged_inputs.json")
    merged_input_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

    templates = TemplatePaths(
        survey=args.survey_template.resolve(),
        attachment4=args.attachment4_template.resolve(),
        guideline=args.guideline_template.resolve(),
    )

    result = generate_documents(
        merged_input_path,
        output_dir,
        args.user_template.resolve(),
        templates,
        preview=False,
    )

    print("[OK] 已生成文件:")
    for item in result.outputs:
        print(f" - {item}")
    print(f"[OK] 输出清单(JSON): {result.manifest_path}")

    sent_manager = False
    sent_customer = False

    if args.send_email:
        manager_to = parse_comma_list(args.manager_to)
        manager_cc = parse_comma_list(args.manager_cc)
        customer_to = parse_comma_list(args.customer_to)
        customer_cc = parse_comma_list(args.customer_cc)

        if not manager_to or not customer_to:
            print("[ERR] --send-email 模式下必须提供 --manager-to 和 --customer-to", file=sys.stderr)
            sys.exit(3)

        smtp_host = (args.smtp_host or "").strip()
        smtp_user = (args.smtp_user or "").strip()
        smtp_sender = (args.smtp_from or smtp_user).strip()

        if not smtp_host or not smtp_sender:
            print("[ERR] --send-email 模式下必须提供 --smtp-host 和 --smtp-user/--smtp-from", file=sys.stderr)
            sys.exit(3)

        smtp_password = os.getenv(args.smtp_password_env, "")
        if smtp_user and not smtp_password:
            smtp_password = getpass.getpass(f"请输入 SMTP 密码 ({args.smtp_password_env}): ")

        manager_draft = next((p for p in result.outputs if "邮件草稿-客户经理" in p.name), None)
        customer_draft = next((p for p in result.outputs if "邮件草稿-客户" in p.name), None)
        material_list = next((p for p in result.outputs if "客户盖章材料清单" in p.name), None)

        if manager_draft is None or customer_draft is None or material_list is None:
            print("[ERR] 未找到邮件草稿或材料清单，无法发送邮件。", file=sys.stderr)
            sys.exit(4)

        manager_subject, manager_body = parse_email_draft(manager_draft)
        customer_subject, customer_body = parse_email_draft(customer_draft)

        manager_attachments = [
            p
            for p in result.outputs
            if p.suffix.lower() in {".xlsx", ".docx", ".txt"}
            and "邮件草稿" not in p.name
        ]
        customer_attachments = [material_list]

        manager_msg = build_message(
            smtp_sender,
            manager_to,
            manager_cc,
            manager_subject,
            manager_body,
            manager_attachments,
        )
        customer_msg = build_message(
            smtp_sender,
            customer_to,
            customer_cc,
            customer_subject,
            customer_body,
            customer_attachments,
        )

        send_message(
            smtp_host,
            args.smtp_port,
            smtp_user,
            smtp_password,
            not args.smtp_no_starttls,
            manager_msg,
        )
        sent_manager = True

        send_message(
            smtp_host,
            args.smtp_port,
            smtp_user,
            smtp_password,
            not args.smtp_no_starttls,
            customer_msg,
        )
        sent_customer = True

        print("[OK] 两封邮件已发送成功。")

    pipeline_summary = {
        "企业名称": company,
        "输入文件": str(input_path),
        "合并输入": str(merged_input_path),
        "字段确认单": str(confirmation_path),
        "输出清单": str(result.manifest_path),
        "missing_fields": missing_fields,
        "联网检索": {
            "enabled": bool(args.auto_web_search),
            "auto_enrich_json": str(auto_enrich_path) if auto_enrich_path else "",
            "auto_note_md": str(auto_note_path) if auto_note_path else "",
            "manual_enrich_json": str(args.enrich_json.resolve()) if args.enrich_json else "",
        },
        "邮件发送": {
            "enabled": bool(args.send_email),
            "manager_sent": sent_manager,
            "customer_sent": sent_customer,
        },
    }
    summary_path = output_dir / prefixed_output_name(company, "02_流水线执行结果.json")
    summary_path.write_text(json.dumps(pipeline_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 流水线结果: {summary_path}")


if __name__ == "__main__":
    main(sys.argv)

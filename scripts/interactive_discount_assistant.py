#!/usr/bin/env python3
"""步骤1：交互式贴现助手，用于信息确认/校对/触发生成。"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from generate_discount_docs import (  # noqa: E402
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TEMPLATES_DIR,
    DEFAULT_USER_TEMPLATE,
    TemplatePaths,
    generate_documents,
    prompt_confirm,
)


def prompt(message: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{message}{suffix}: ").strip()
    return value or (default or "")


def normalize_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def prompt_path(
    message: str,
    default: Path,
    *,
    expect: str,
    create: bool = False,
) -> Path:
    default_str = str(default.resolve())
    while True:
        raw = prompt(message, default_str)
        value = normalize_path(raw)
        if expect == "file":
            if value.is_file():
                return value
            if create:
                value.parent.mkdir(parents=True, exist_ok=True)
                return value
            print(f"[ERR] 文件不存在：{value}")
        else:
            if value.exists() and not value.is_dir():
                print(f"[ERR] 期望目录但得到文件：{value}")
                continue
            if not value.exists() and create:
                value.mkdir(parents=True, exist_ok=True)
            if value.exists() or create:
                return value
            print(f"[ERR] 目录不存在：{value}")


def main() -> None:
    print("\n=== 贴现模板交互助手 ===\n")
    print(
        "步骤：\n"
        "  1) 指定用户信息文件 (JSON/TXT)\n"
        "  2) 确认模板路径和输出目录\n"
        "  3) 自动生成校对文件供人工核对\n"
        "  4) 确认后填写模板\n"
    )

    default_input = DEFAULT_USER_TEMPLATE
    input_file = prompt_path("1) 用户信息文件路径", default_input, expect="file")

    templates_dir = prompt_path(
        "2) 模板所在目录 (回车默认为当前目录)",
        DEFAULT_TEMPLATES_DIR,
        expect="dir",
    )
    survey_tpl = prompt_path(
        "   - 调查报告模板",
        templates_dir / "调查报告.xlsx",
        expect="file",
    )
    attachment_tpl = prompt_path(
        "   - 附件4模板",
        templates_dir / "附件4：贴现额度授信审批文件移交清单.xlsx",
        expect="file",
    )
    guideline_tpl = prompt_path(
        "   - 《操作规范》模板",
        templates_dir
        / "关于暂时采用线下方式审批银行承兑汇票贴现限额的操作规范2023.12.18修订.docx",
        expect="file",
    )

    user_template = prompt_path(
        "3) 用户清单填报模板",
        DEFAULT_USER_TEMPLATE,
        expect="file",
    )
    output_dir = prompt_path(
        "4) 输出目录",
        DEFAULT_OUTPUT_DIR,
        expect="dir",
        create=True,
    )

    templates = TemplatePaths(
        survey=survey_tpl,
        attachment4=attachment_tpl,
        guideline=guideline_tpl,
    )

    print("\n[STEP 3] 生成校对文件...")
    preview = generate_documents(
        input_file,
        output_dir,
        user_template,
        templates,
        preview=True,
    )

    if preview.missing_fields:
        print("[WARN] 以下字段为空：")
        for key in preview.missing_fields:
            print(f" - {key}")
    if preview.parse_warnings:
        print("[WARN] 清单中存在未识别条目：")
        for line in preview.parse_warnings:
            print(f" - {line}")

    print(f"[INFO] 校对清单：{preview.checklist_path}")
    print(f"[INFO] 输出清单(JSON)：{preview.manifest_path}")
    print("请打开上述文件，核对并补充缺失信息。")

    if not prompt_confirm("已完成校对并准备生成模板吗?", default_yes=False):
        print("已结束：保留校对清单，稍后可重新运行。")
        return

    print("\n[STEP 4] 正在填充模板...")
    final = generate_documents(
        input_file,
        output_dir,
        user_template,
        templates,
        preview=False,
    )

    if not final.outputs:
        print("[WARN] 未产出模板文件，请检查输入。")
        return

    print("已生成以下文件：")
    for path in final.outputs:
        print(f" - {path}")
    print(f"[INFO] 输出清单(JSON)：{final.manifest_path}")


if __name__ == "__main__":
    main()

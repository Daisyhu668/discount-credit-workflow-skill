#!/usr/bin/env python3
"""Local debug web server for discount-credit workflow."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
SKILL_ROOT = ROOT.parent
PIPELINE = SKILL_ROOT / "scripts" / "run_discount_pipeline.py"
DEFAULT_OUTPUT = ROOT / "output"


def parse_multipart(body: bytes, boundary: bytes) -> tuple[Dict[str, str], Dict[str, tuple[str, bytes]]]:
    fields: Dict[str, str] = {}
    files: Dict[str, tuple[str, bytes]] = {}
    delimiter = b"--" + boundary
    parts = body.split(delimiter)
    for part in parts:
        if not part or part in (b"--\r\n", b"--"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        header_blob, _, content = part.partition(b"\r\n\r\n")
        if not header_blob:
            continue
        headers = header_blob.decode("utf-8", errors="ignore").split("\r\n")
        disposition = ""
        for h in headers:
            if h.lower().startswith("content-disposition:"):
                disposition = h
                break
        if not disposition:
            continue
        name_match = re.search(r'name="([^"]+)"', disposition)
        if not name_match:
            continue
        name = name_match.group(1)
        filename_match = re.search(r'filename="([^"]*)"', disposition)
        if filename_match and filename_match.group(1):
            filename = Path(filename_match.group(1)).name
            files[name] = (filename, content.rstrip(b"\r\n"))
        else:
            fields[name] = content.decode("utf-8", errors="ignore").strip("\r\n")
    return fields, files


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._send_file(ROOT / "index.html", "text/html; charset=utf-8")
            return
        self.send_error(404, "Not Found")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/run":
            self.send_error(404, "Not Found")
            return
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._send_json({"ok": False, "error": "Content-Type must be multipart/form-data"}, code=400)
            return
        match = re.search(r"boundary=([^;]+)", content_type)
        if not match:
            self._send_json({"ok": False, "error": "Missing multipart boundary"}, code=400)
            return
        boundary = match.group(1).encode("utf-8")
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        fields, files = parse_multipart(body, boundary)

        input_mode = fields.get("input_mode", "manual")
        output_dir_raw = fields.get("output_dir", "").strip()
        auto_web_search = fields.get("auto_web_search", "no") == "yes"
        allow_missing = fields.get("allow_missing", "no") == "yes"

        output_dir = Path(output_dir_raw) if output_dir_raw else DEFAULT_OUTPUT
        if not output_dir.is_absolute():
            output_dir = (SKILL_ROOT / output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        input_path = None
        if input_mode == "upload":
            fileitem = files.get("input_file")
            if fileitem is None:
                self._send_json({"ok": False, "error": "未检测到上传文件"}, code=400)
                return
            filename, file_bytes = fileitem
            input_path = output_dir / f"upload_{int(time.time())}_{filename}"
            with input_path.open("wb") as f:
                f.write(file_bytes)
        else:
            payload = {
                "企业名称": fields.get("company", "").strip(),
                "统一社会信用代码": fields.get("tax_id", "").strip(),
                "注册时间": fields.get("reg_date", "").strip(),
                "注册地址": fields.get("address", "").strip(),
                "实际地址是否同注册地址": fields.get("same_address", "yes").strip(),
                "实际经营地址": fields.get("actual_address", "").strip(),
                "法定代表人": fields.get("legal_rep", "").strip(),
                "行业类型": fields.get("industry", "").strip(),
                "申请日期": fields.get("apply_date", "").strip(),
                "上一年度营业收入": fields.get("last_year_revenue", "").strip(),
                "净资产": fields.get("net_asset", "").strip(),
                "客户经理收件人": fields.get("manager_to", "").strip(),
                "客户经理抄送": fields.get("manager_cc", "").strip(),
                "客户收件人": fields.get("customer_to", "").strip(),
                "客户抄送": fields.get("customer_cc", "").strip(),
            }
            extra_json_raw = fields.get("extra_json", "").strip()
            if extra_json_raw:
                try:
                    extra = json.loads(extra_json_raw)
                    if isinstance(extra, dict):
                        payload.update(extra)
                except json.JSONDecodeError as exc:
                    self._send_json({"ok": False, "error": f"额外字段 JSON 解析失败: {exc}"}, code=400)
                    return
            input_path = output_dir / f"manual_input_{int(time.time())}.json"
            input_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        cmd = [sys.executable, str(PIPELINE), str(input_path), "--yes", "--output", str(output_dir)]
        if auto_web_search:
            cmd.append("--auto-web-search")
        if allow_missing:
            cmd.append("--allow-missing")

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(SKILL_ROOT))
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": f"执行失败: {exc}"}, code=500)
            return

        files = []
        try:
            files = sorted([p.name for p in output_dir.iterdir() if p.is_file()])
        except Exception:
            files = []

        ok = proc.returncode == 0
        log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        resp: Dict[str, object] = {
            "ok": ok,
            "log": log.strip(),
            "files": files,
            "output_dir": str(output_dir),
        }
        if not ok:
            resp["error"] = "流程执行失败，请查看日志。"
        self._send_json(resp, code=200 if ok else 500)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(404, "Not Found")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: Dict[str, object], code: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    host = os.getenv("DEBUG_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("DEBUG_WEB_PORT", "8787"))
    server = HTTPServer((host, port), Handler)
    print(f"Debug UI running on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

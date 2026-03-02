#!/usr/bin/env python3
"""根据企业名称做联网检索，提取可回填字段。"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Sequence
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

RESULT_RE = re.compile(
    r'<a rel="nofollow" class="result__a" href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
    r'<a class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.S,
)

USCC_RE = re.compile(r"\b[0-9A-Z]{18}\b")
DATE_RE = re.compile(r"(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def clean_text(raw: str) -> str:
    text = html.unescape(raw)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fetch_duckduckgo_results(query: str) -> list[SearchResult]:
    encoded = quote_plus(query)
    url = f"https://duckduckgo.com/html/?q={encoded}"
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="ignore")

    results: list[SearchResult] = []
    for match in RESULT_RE.finditer(body):
        results.append(
            SearchResult(
                title=clean_text(match.group("title")),
                url=html.unescape(match.group("url")),
                snippet=clean_text(match.group("snippet")),
            )
        )
    return results


def normalize_date(text: str) -> str | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    y, m, d = match.groups()
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"


def pick_first(pattern: re.Pattern[str], corpus: str) -> str | None:
    match = pattern.search(corpus)
    if not match:
        return None
    return match.group(0)


def extract_enrichment(company_name: str, results: Sequence[SearchResult]) -> Dict[str, str]:
    corpus = "\n".join(f"{item.title}\n{item.snippet}" for item in results)

    enrich: Dict[str, str] = {"企业名称": company_name}

    uscc = pick_first(USCC_RE, corpus)
    if uscc:
        enrich["统一社会信用代码"] = uscc

    date_value = normalize_date(corpus)
    if date_value:
        enrich["注册时间"] = date_value

    legal_match = re.search(r"法定代表人[:：\s]*([\u4e00-\u9fa5A-Za-z·]{2,20})", corpus)
    if legal_match:
        enrich["法定代表人"] = legal_match.group(1)

    addr_match = re.search(
        r"(?:住所|注册地址|登记机关地址)[:：\s]*([\u4e00-\u9fa5A-Za-z0-9（）()·,，\-]{8,80})",
        corpus,
    )
    if addr_match:
        enrich["注册地址"] = addr_match.group(1).strip("，,。；;")

    industry_match = re.search(r"(?:所属行业|行业|行业分类)[:：\s]*([\u4e00-\u9fa5A-Za-z0-9（）()·]{2,40})", corpus)
    if industry_match:
        enrich["行业类型"] = industry_match.group(1).strip("，,。；;")

    return enrich


def render_note(company_name: str, results: Sequence[SearchResult]) -> str:
    lines = [f"企业名称：{company_name}", "", "检索结果（Top 命中）："]
    for idx, item in enumerate(results, start=1):
        lines.extend([
            f"{idx}. {item.title}",
            f"   - URL: {item.url}",
            f"   - 摘要: {item.snippet}",
        ])
    return "\n".join(lines)


def run_search(company_name: str, max_results: int = 8) -> tuple[Dict[str, str], str]:
    results = fetch_duckduckgo_results(f"{company_name} 工商信息 法定代表人 统一社会信用代码")
    if not results:
        return {"企业名称": company_name}, "未检索到可用结果。"

    selected = results[: max(1, max_results)]
    enrich = extract_enrichment(company_name, selected)
    note = render_note(company_name, selected)
    return enrich, note


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="企业名称联网检索并提取回填字段")
    parser.add_argument("company_name", help="企业名称")
    parser.add_argument("--output", type=Path, default=Path.cwd(), help="输出目录")
    parser.add_argument("--max-results", type=int, default=8, help="最多保留检索结果条数")
    return parser.parse_args(argv[1:])


def main(argv: Sequence[str]) -> None:
    args = parse_args(argv)

    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    enrich, note = run_search(args.company_name, max_results=args.max_results)
    slug = re.sub(r"[/:*?\"<>|\s]+", "_", args.company_name).strip("_") or "company"

    enrich_path = output_dir / f"{slug}-联网检索补充.json"
    note_path = output_dir / f"{slug}-联网检索结果.md"

    enrich_path.write_text(json.dumps(enrich, ensure_ascii=False, indent=2), encoding="utf-8")
    note_path.write_text(note + "\n", encoding="utf-8")

    print(f"[OK] 补充字段: {enrich_path}")
    print(f"[OK] 检索备注: {note_path}")


if __name__ == "__main__":
    main(sys.argv)

#!/usr/bin/env python3
"""defect-kb CLI — project-agnostic defect knowledge base tool.

Commands: init | govern | index | search | browse | stats | report
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from config import find_project_root, load_config, resolve_path, get_api_key, get_provider_config
from llm import call_llm, get_embedding
from parser import parse_llm_json, LLMParseError
from schema import ExperienceCard, QualityScore

_MAX_RETRY = 2

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_id(cards_path: Path, today: str) -> str:
    prefix = f"DEF-{today}-"
    max_seq = 0
    if cards_path.exists():
        for line in cards_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            card = json.loads(line)
            cid = card.get("metadata", {}).get("id", "")
            if cid.startswith(prefix):
                seq = int(cid.split("-")[-1])
                max_seq = max(max_seq, seq)
    return f"{prefix}{max_seq + 1:03d}"


def _load_prompt(name: str) -> str:
    prompts_dir = Path(__file__).parent / "prompts"
    path = prompts_dir / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def _run_quality_check(cfg: dict, card_data: dict) -> dict:
    """Run LLM quality check and return parsed score dict."""
    qc_template = _load_prompt("quality_check.txt")
    qc_prompt = qc_template.format(
        card_json=json.dumps(card_data, ensure_ascii=False, indent=2)
    )
    qc_raw = call_llm(cfg, qc_prompt)
    return parse_llm_json(qc_raw)


_LEGACY_SCORE_MAP = {
    "root_cause_credibility": "root_cause_depth",
    "fix_strategy_portability": "fix_portability",
    "patch_digest_completeness": "patch_digest_quality",
    "verification_plan_actionability": "verification_plan",
    "information_security": "infosec",
}


def _build_quality_score(qc_data: dict, override: bool = False) -> QualityScore:
    """Build a QualityScore model from the LLM quality check result.

    Accepts both current schema field names (signal_clarity, root_cause_depth, ...)
    and legacy prompt field names (root_cause_credibility, ...) for backward compat.
    """
    raw = qc_data.get("scores", {})
    scores: dict[str, float] = {}
    for k, v in raw.items():
        canonical = _LEGACY_SCORE_MAP.get(k, k)
        scores[canonical] = v
    return QualityScore(
        signal_clarity=scores.get("signal_clarity", 1),
        root_cause_depth=scores.get("root_cause_depth", 1),
        fix_portability=scores.get("fix_portability", 1),
        patch_digest_quality=scores.get("patch_digest_quality", 1),
        verification_plan=scores.get("verification_plan", 1),
        infosec=scores.get("infosec", 1),
        average=qc_data.get("average", 1),
        passed=qc_data.get("pass", False),
        quality_override=override,
        issues=qc_data.get("issues", []),
    )


def _write_card(cards_path: Path, card: ExperienceCard) -> None:
    cards_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cards_path, "a", encoding="utf-8") as f:
        f.write(card.model_dump_json(ensure_ascii=False) + "\n")


def _output(data: Any, fmt: str) -> None:
    """Print output in requested format (text or json)."""
    if fmt == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif isinstance(data, str):
        print(data)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def _log_event(cfg: dict, event: dict) -> None:
    """Append a timestamped event to defect-kb-data/events.jsonl."""
    root = Path(cfg["_root"])
    events_path = root / "defect-kb-data" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    event["ts"] = datetime.now().isoformat(timespec="seconds")
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _record_search_hits(cards_path: Path, hit_card_ids: list[str]) -> dict[str, int]:
    """Atomically increment usage_count and refresh last_hit_at for the given card IDs.

    Returns a map of {card_id: new_usage_count} for the cards that were actually updated.
    Cards not present in the file are silently skipped (e.g. ChromaDB has stale entries).
    Uses tmp-file + os.replace for crash safety on POSIX/NTFS.
    """
    if not hit_card_ids or not cards_path.exists():
        return {}

    hit_set = set(hit_card_ids)
    now_iso = datetime.now().isoformat(timespec="seconds")
    new_counts: dict[str, int] = {}

    tmp_path = cards_path.with_suffix(cards_path.suffix + ".tmp")
    with open(cards_path, "r", encoding="utf-8") as src, open(tmp_path, "w", encoding="utf-8") as dst:
        for line in src:
            stripped = line.strip()
            if not stripped:
                dst.write(line)
                continue
            try:
                card = json.loads(stripped)
            except json.JSONDecodeError:
                dst.write(line)
                continue
            cid = card.get("metadata", {}).get("id", "")
            if cid in hit_set:
                meta = card["metadata"]
                meta["usage_count"] = int(meta.get("usage_count", 0)) + 1
                meta["last_hit_at"] = now_iso
                new_counts[cid] = meta["usage_count"]
                dst.write(json.dumps(card, ensure_ascii=False) + "\n")
            else:
                dst.write(line)

    os.replace(tmp_path, cards_path)
    return new_counts


def _usage_boost(usage_count: int) -> float:
    """Convert usage_count into a small multiplicative boost for ranking.

    Uses log1p so the curve is generous early (0→0, 3→0.139, 10→0.240, 50→0.392)
    and saturates: even 100 hits only adds ~46% — keeps semantic similarity dominant.
    """
    import math
    return 1.0 + math.log1p(usage_count) * 0.1


def _format_index_md(cards: list[dict]) -> str:
    """Render INDEX.md content from a list of card dicts.

    Layout:
      # Title + summary stats (split: own vs seed/quick)
      ## Own cards by platform     (project-authored, not seed/quick)
      ## Quick notes               (metadata.quick==true, awaiting upgrade)
      ## Seed cards by platform    (metadata.seed==true, bundled examples)
      ## Cold candidates           (usage=0 AND quality<4, own cards only)
    """
    if not cards:
        return (
            "# Defect Knowledge Base — INDEX\n\n"
            "_No cards yet. Run `python defect-kb/cli.py govern --input ...` to add one._\n"
        )

    own_cards = [
        c for c in cards
        if not c.get("metadata", {}).get("seed")
        and not c.get("metadata", {}).get("quick")
    ]
    quick_cards = [c for c in cards if c.get("metadata", {}).get("quick")]
    seed_cards = [c for c in cards if c.get("metadata", {}).get("seed")]

    total = len(cards)
    with_quality = [c for c in own_cards if c.get("metadata", {}).get("quality")]
    avg_quality = (
        sum(c["metadata"]["quality"]["average"] for c in with_quality) / len(with_quality)
        if with_quality else 0.0
    )
    total_hits = sum(int(c.get("metadata", {}).get("usage_count", 0)) for c in cards)
    project = cards[0].get("metadata", {}).get("project", "")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []
    lines.append(f"# Defect Knowledge Base — INDEX")
    lines.append("")
    summary_parts = [
        f"Project: **{project}**",
        f"Cards: **{total}**",
        f"Own: **{len(own_cards)}**",
    ]
    if quick_cards:
        summary_parts.append(f"Quick: **{len(quick_cards)}**")
    if seed_cards:
        summary_parts.append(f"Seed: **{len(seed_cards)}**")
    summary_parts.extend([
        f"Avg quality (own): **{avg_quality:.2f}**",
        f"Total hits: **{total_hits}**",
        f"Updated: {now_str}",
    ])
    lines.append("> " + " &nbsp; · &nbsp; ".join(summary_parts))
    lines.append("")
    lines.append("> Auto-generated by `cli.py govern` / `cli.py index --rebuild-md`. Do not hand-edit.")
    lines.append("")

    by_platform: dict[str, list[dict]] = {}
    for c in own_cards:
        plat = c.get("metadata", {}).get("platform", "unknown")
        by_platform.setdefault(plat, []).append(c)

    severity_emoji = {"P0": "🔴", "P1": "🟠", "P2": "🟡"}

    def _render_card_line(c: dict) -> tuple[str, str]:
        m = c["metadata"]
        idx = c["index_layer"]
        usage = int(m.get("usage_count", 0))
        badge_parts: list[str] = []
        sev = m.get("severity", "")
        if sev:
            badge_parts.append(f"{severity_emoji.get(sev, '')}{sev}".strip())
        badge_parts.append(m.get("module", "unknown"))
        if usage > 0:
            badge_parts.append(f"★{usage}")
        q = m.get("quality")
        if q:
            badge_parts.append(f"Q{q['average']:.1f}")
        if m.get("quick"):
            badge_parts.append("quick")
        if m.get("seed"):
            badge_parts.append("seed")
        if m.get("upgraded_at"):
            badge_parts.append("upgraded")
        badge = " · ".join(p for p in badge_parts if p)
        return f"- **[{m['id']}]** {idx['problem_summary']}  ", f"  _{badge}_"

    if by_platform:
        lines.append("## Own cards by platform")
        lines.append("")
        for plat in sorted(by_platform.keys()):
            plat_cards = sorted(
                by_platform[plat],
                key=lambda c: (c.get("metadata", {}).get("date", ""), c.get("metadata", {}).get("id", "")),
                reverse=True,
            )
            plat_cards.sort(
                key=lambda c: int(c.get("metadata", {}).get("usage_count", 0)),
                reverse=True,
            )
            lines.append(f"### {plat}  ({len(plat_cards)})")
            lines.append("")
            for c in plat_cards:
                a, b = _render_card_line(c)
                lines.append(a)
                lines.append(b)
            lines.append("")
    else:
        lines.append("## Own cards")
        lines.append("")
        lines.append("_No project-authored cards yet — only seeds/quick notes below._")
        lines.append("")

    if quick_cards:
        lines.append(f"## ✏️ Quick notes  ({len(quick_cards)})")
        lines.append("")
        lines.append("> Captured via `cli.py quick` and **awaiting promotion**. "
                     "Run `cli.py upgrade --id <ID>` to flesh them out and pass the quality gate.")
        lines.append("")
        for c in sorted(
            quick_cards,
            key=lambda c: c.get("metadata", {}).get("date", ""),
            reverse=True,
        ):
            a, b = _render_card_line(c)
            lines.append(a)
            lines.append(b)
        lines.append("")

    if seed_cards:
        seed_by_plat: dict[str, list[dict]] = {}
        for c in seed_cards:
            seed_by_plat.setdefault(c["metadata"].get("platform", "common"), []).append(c)
        lines.append(f"## 🌱 Seed cards  ({len(seed_cards)})")
        lines.append("")
        lines.append("> Bundled with the skill via `init --import-seeds`. "
                     "Excluded from project quality stats by default (use `stats --include-seeds` to include).")
        lines.append("")
        for plat in sorted(seed_by_plat.keys()):
            sp_cards = sorted(
                seed_by_plat[plat],
                key=lambda c: int(c.get("metadata", {}).get("usage_count", 0)),
                reverse=True,
            )
            lines.append(f"### {plat}  ({len(sp_cards)})")
            lines.append("")
            for c in sp_cards:
                a, b = _render_card_line(c)
                lines.append(a)
                lines.append(b)
            lines.append("")

    cold = [
        c for c in own_cards
        if int(c.get("metadata", {}).get("usage_count", 0)) == 0
        and (
            (c.get("metadata", {}).get("quality") or {}).get("average", 5.0) < 4.0
        )
    ]
    if cold:
        lines.append(f"## ⚠️ Cold Candidates  ({len(cold)})")
        lines.append("")
        lines.append("> Cards never returned by search **and** quality average < 4.0. Consider improving signals or merging into a similar card.")
        lines.append("")
        for c in cold[:20]:
            m = c["metadata"]
            q_avg = (m.get("quality") or {}).get("average", 0.0)
            lines.append(f"- **[{m['id']}]** {c['index_layer']['problem_summary']}  ")
            lines.append(f"  _{m['platform']} · {m.get('module', 'unknown')} · Q{q_avg:.1f}_")
        if len(cold) > 20:
            lines.append("")
            lines.append(f"_…and {len(cold) - 20} more (run `cli.py stats` to see all)._")
        lines.append("")

    return "\n".join(lines) + "\n"


def _regenerate_index_md(cfg: dict) -> Path | None:
    """Rebuild defect-kb-data/INDEX.md from the current cards.jsonl. Returns the written path."""
    cards_path = resolve_path(cfg, "data.cards_path")
    if not cards_path.exists():
        return None
    cards: list[dict] = []
    for line in cards_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                cards.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    md = _format_index_md(cards)
    index_path = cards_path.parent / "INDEX.md"
    index_path.write_text(md, encoding="utf-8")
    return index_path


def _update_config_field(cfg: dict, key_path: str, value: Any) -> None:
    """Read defect-kb.yaml, set a nested field, and write back."""
    root = Path(cfg["_root"])
    cfg_path = root / "defect-kb.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    parts = key_path.split(".")
    target = data
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# init — project scanning, template recommendation, preview
# ---------------------------------------------------------------------------

import re as _re

_KNOWN_PLATFORMS = ["ios", "android", "web", "backend", "harmony", "flutter", "recommend"]

_PITFALLS_CANDIDATES = [
    "dev_context/evolution/pitfalls.md",
    "docs/pitfalls.md",
]

_FEATURE_GLOB_CANDIDATES = [
    "dev_context/features/*/v*.md",
    "docs/features/*/v*.md",
]

_PREVIEW_FILENAME = "defect-kb-init-preview.md"

_DEFAULT_PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {
        "env_key": "OPENAI_API_KEY",
        "base_url": "",
        "model": "gpt-4o-mini",
        "embedding_model": "text-embedding-3-small",
    },
    "claude": {
        "env_key": "ANTHROPIC_API_KEY",
        "base_url": "",
        "model": "claude-sonnet-4-20250514",
    },
    "deepseek": {
        "env_key": "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "qwen": {
        "env_key": "DASHSCOPE_API_KEY",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    "doubao": {
        "env_key": "ARK_API_KEY",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-1-5-pro-32k",
    },
}


_RULES_FILE_CANDIDATES = [
    "docs/api-business-rules.md",
    "docs/business-rules.md",
]

_CONTRACT_FILE_CANDIDATES = [
    "contracts/openapi.yaml",
    "contracts/openapi.json",
    "openapi.yaml",
]

_CONTENT_SCAN_PATTERNS: list[dict[str, Any]] = [
    {
        "name": "pitfalls",
        "globs": ["dev_context/evolution/pitfalls.md", "docs/pitfalls.md"],
        "extract_mode": "split_by_heading",
        "heading_levels": [3, 4],
        "source_tag": "pitfalls",
        "confidence": "confirmed",
    },
    {
        "name": "evolution-lessons",
        "globs": ["dev_context/evolution/*.md"],
        "exclude_globs": ["dev_context/evolution/pitfalls.md"],
        "extract_mode": "heading_keyword",
        "heading_patterns": ["踩坑", "教训", "遗留问题", "放弃方案"],
        "source_tag": "pitfalls",
        "confidence": "confirmed",
    },
    {
        "name": "feature-context",
        "globs": ["dev_context/features/**/*.md", "dev_context/architecture/**/*.md"],
        "extract_mode": "heading_keyword",
        "heading_patterns": ["踩坑", "教训", "遗留问题", "根因", "失败原因"],
        "source_tag": "pitfalls",
        "confidence": "confirmed",
    },
    {
        "name": "design-risks",
        "globs": ["backend/docs/design/**/*.md", "recommend/docs/design/**/*.md"],
        "extract_mode": "heading_keyword",
        "heading_patterns": ["风险", "注意事项", "已知问题"],
        "source_tag": "pitfalls",
        "confidence": "likely",
    },
    {
        "name": "ios-fix-plans",
        "globs": ["ios/docs/**/FIX_PLAN*.md", "ios/docs/**/fidelity_report*.md"],
        "extract_mode": "split_by_heading",
        "heading_levels": [2, 3],
        "source_tag": "pitfalls",
        "confidence": "confirmed",
    },
    {
        "name": "playbook-lessons",
        "globs": ["playbooks/**/*.md"],
        "extract_mode": "heading_keyword",
        "heading_patterns": ["经验沉淀", "经验教训", "Lessons Learned", "注意事项"],
        "source_tag": "pitfalls",
        "confidence": "confirmed",
    },
    {
        "name": "system-opt-findings",
        "globs": ["docs/system-opt-*/**/*.md"],
        "extract_mode": "heading_keyword",
        "heading_patterns": ["根因", "findings", "decisions"],
        "source_tag": "pitfalls",
        "confidence": "likely",
    },
    {
        "name": "testing-postmortems",
        "globs": ["testing/**/*.md"],
        "extract_mode": "heading_keyword",
        "heading_patterns": ["根因", "修复", "结论"],
        "source_tag": "pitfalls",
        "confidence": "likely",
    },
]


_TOOL_REGISTRY: list[dict[str, Any]] = [
    {
        "platform": "backend",
        "name": "pmd",
        "detect": {"file": "pom.xml", "pattern": "maven-pmd-plugin"},
        "command": "mvn pmd:pmd -Dformat=xml -q",
        "report_glob": "target/pmd.xml",
        "format": "pmd-xml",
        "working_dir": "backend",
    },
    {
        "platform": "backend",
        "name": "checkstyle",
        "detect": {"file": "pom.xml", "pattern": "maven-checkstyle-plugin"},
        "command": "mvn checkstyle:checkstyle -q",
        "report_glob": "target/checkstyle-result.xml",
        "format": "checkstyle-xml",
        "working_dir": "backend",
    },
    {
        "platform": "backend",
        "name": "spotbugs",
        "detect": {"file": "pom.xml", "pattern": "spotbugs-maven-plugin"},
        "command": "mvn spotbugs:spotbugs -q",
        "report_glob": "target/spotbugsXml.xml",
        "format": "spotbugs-xml",
        "working_dir": "backend",
    },
    {
        "platform": "ios",
        "name": "swiftlint",
        "detect": {"file": ".swiftlint.yml"},
        "command": "swiftlint lint --reporter json 2>/dev/null || true",
        "format": "swiftlint-json",
        "working_dir": "ios",
    },
    {
        "platform": "web",
        "name": "eslint",
        "detect": {"file": ".eslintrc.json"},
        "command": "npx eslint . --format json 2>/dev/null || true",
        "format": "eslint-json",
        "working_dir": "web",
    },
    {
        "platform": "harmony",
        "name": "eslint",
        "detect": {"file": ".eslintrc.json"},
        "command": "npx eslint entry/src --ext .ts,.ets --format json 2>/dev/null || true",
        "format": "eslint-json",
        "working_dir": "harmony",
    },
    {
        "platform": "android",
        "name": "ktlint",
        "detect": {"file": "build.gradle.kts", "pattern": "org.jlleitschuh.gradle.ktlint"},
        "command": "./gradlew ktlintCheck --continue 2>&1 || true",
        "format": "ktlint-text",
        "working_dir": "android",
    },
]

_SEVERITY_WEIGHT = {"error": 3, "warning": 2, "info": 1}


class NormalizedFinding:
    """A single finding from a static analysis tool, normalized across formats."""

    __slots__ = ("tool", "platform", "rule_id", "severity", "message", "file_path", "line", "category")

    def __init__(
        self,
        tool: str,
        platform: str,
        rule_id: str,
        severity: str,
        message: str,
        file_path: str,
        line: int | None = None,
        category: str = "",
    ):
        self.tool = tool
        self.platform = platform
        self.rule_id = rule_id
        self.severity = severity
        self.message = message
        self.file_path = file_path
        self.line = line
        self.category = category

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "platform": self.platform,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "file_path": self.file_path,
            "line": self.line,
            "category": self.category,
        }


def _detect_tools(root: Path) -> list[dict[str, Any]]:
    """Auto-detect which static analysis tools are configured in the project."""
    detected: list[dict[str, Any]] = []
    for tool in _TOOL_REGISTRY:
        detect = tool["detect"]
        working = root / tool["working_dir"]
        detect_file = working / detect["file"]
        if not detect_file.exists():
            continue
        if "pattern" in detect:
            try:
                content = detect_file.read_text(encoding="utf-8", errors="ignore")
                if detect["pattern"] not in content:
                    continue
            except OSError:
                continue
        detected.append(tool)
    return detected


def _parse_pmd_xml(xml_text: str, platform: str) -> list[NormalizedFinding]:
    """Parse PMD XML report into NormalizedFinding list."""
    from xml.etree import ElementTree as ET
    findings: list[NormalizedFinding] = []
    try:
        root_el = ET.fromstring(xml_text)
    except ET.ParseError:
        return findings
    for file_el in root_el.findall(".//file"):
        fpath = file_el.get("name", "")
        for viol in file_el.findall("violation"):
            prio = int(viol.get("priority", "3"))
            sev = "error" if prio <= 2 else "warning" if prio <= 4 else "info"
            findings.append(NormalizedFinding(
                tool="pmd",
                platform=platform,
                rule_id=viol.get("rule", ""),
                severity=sev,
                message=(viol.text or "").strip(),
                file_path=fpath,
                line=int(viol.get("beginline", "0")) or None,
                category=viol.get("ruleset", ""),
            ))
    return findings


def _parse_checkstyle_xml(xml_text: str, platform: str) -> list[NormalizedFinding]:
    """Parse Checkstyle XML report into NormalizedFinding list."""
    from xml.etree import ElementTree as ET
    findings: list[NormalizedFinding] = []
    try:
        root_el = ET.fromstring(xml_text)
    except ET.ParseError:
        return findings
    for file_el in root_el.findall(".//file"):
        fpath = file_el.get("name", "")
        for err in file_el.findall("error"):
            raw_sev = (err.get("severity", "warning")).lower()
            sev = raw_sev if raw_sev in _SEVERITY_WEIGHT else "warning"
            source = err.get("source", "")
            rule_id = source.rsplit(".", 1)[-1] if source else ""
            findings.append(NormalizedFinding(
                tool="checkstyle",
                platform=platform,
                rule_id=rule_id,
                severity=sev,
                message=err.get("message", ""),
                file_path=fpath,
                line=int(err.get("line", "0")) or None,
                category=source.rsplit(".", 2)[-2] if "." in source else "",
            ))
    return findings


def _parse_spotbugs_xml(xml_text: str, platform: str) -> list[NormalizedFinding]:
    """Parse SpotBugs XML report into NormalizedFinding list."""
    from xml.etree import ElementTree as ET
    findings: list[NormalizedFinding] = []
    try:
        root_el = ET.fromstring(xml_text)
    except ET.ParseError:
        return findings
    prio_map = {"1": "error", "2": "error", "3": "warning", "4": "info", "5": "info"}
    for bug in root_el.findall(".//BugInstance"):
        prio = bug.get("priority", "3")
        sev = prio_map.get(prio, "warning")
        source_line = bug.find("SourceLine")
        fpath = source_line.get("sourcepath", "") if source_line is not None else ""
        line_no = None
        if source_line is not None:
            start = source_line.get("start")
            if start:
                line_no = int(start)
        long_msg_el = bug.find("LongMessage")
        message = long_msg_el.text if long_msg_el is not None and long_msg_el.text else ""
        if not message:
            short_msg_el = bug.find("ShortMessage")
            message = short_msg_el.text if short_msg_el is not None and short_msg_el.text else ""
        findings.append(NormalizedFinding(
            tool="spotbugs",
            platform=platform,
            rule_id=bug.get("type", ""),
            severity=sev,
            message=message.strip(),
            file_path=fpath,
            line=line_no,
            category=bug.get("category", ""),
        ))
    return findings


def _parse_eslint_json(json_text: str, platform: str) -> list[NormalizedFinding]:
    """Parse ESLint JSON report into NormalizedFinding list."""
    findings: list[NormalizedFinding] = []
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return findings
    if not isinstance(data, list):
        return findings
    for file_entry in data:
        fpath = file_entry.get("filePath", "")
        for msg in file_entry.get("messages", []):
            raw_sev = msg.get("severity", 1)
            sev = "error" if raw_sev == 2 else "warning"
            findings.append(NormalizedFinding(
                tool="eslint",
                platform=platform,
                rule_id=msg.get("ruleId") or "",
                severity=sev,
                message=msg.get("message", ""),
                file_path=fpath,
                line=msg.get("line"),
                category="",
            ))
    return findings


def _parse_swiftlint_json(json_text: str, platform: str) -> list[NormalizedFinding]:
    """Parse SwiftLint JSON report into NormalizedFinding list."""
    findings: list[NormalizedFinding] = []
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return findings
    if not isinstance(data, list):
        return findings
    for entry in data:
        raw_sev = (entry.get("severity", "warning") or "warning").lower()
        if raw_sev == "violation":
            raw_sev = "warning"
        sev = raw_sev if raw_sev in _SEVERITY_WEIGHT else "warning"
        findings.append(NormalizedFinding(
            tool="swiftlint",
            platform=platform,
            rule_id=entry.get("rule_id", ""),
            severity=sev,
            message=entry.get("reason", ""),
            file_path=entry.get("file", ""),
            line=entry.get("line"),
            category=entry.get("type", ""),
        ))
    return findings


def _parse_ktlint_text(text: str, platform: str) -> list[NormalizedFinding]:
    """Parse ktlint text output into NormalizedFinding list.

    ktlint output lines look like:
      path/to/File.kt:10:1: Rule-id description (rule-set:rule-id)
    """
    findings: list[NormalizedFinding] = []
    pattern = _re.compile(r"^(.+?):(\d+):\d+:\s*(.+?)\s*\(([^)]+)\)\s*$")
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        fpath, line_no, message, rule_id = m.groups()
        findings.append(NormalizedFinding(
            tool="ktlint",
            platform=platform,
            rule_id=rule_id.strip(),
            severity="warning",
            message=message.strip(),
            file_path=fpath.strip(),
            line=int(line_no),
            category="style",
        ))
    return findings


_REPORT_PARSERS: dict[str, Any] = {
    "pmd-xml": _parse_pmd_xml,
    "checkstyle-xml": _parse_checkstyle_xml,
    "spotbugs-xml": _parse_spotbugs_xml,
    "eslint-json": _parse_eslint_json,
    "swiftlint-json": _parse_swiftlint_json,
    "ktlint-text": _parse_ktlint_text,
}


def _parse_report(report_text: str, fmt: str, platform: str) -> list[NormalizedFinding]:
    """Dispatch to the correct parser based on format string."""
    parser = _REPORT_PARSERS.get(fmt)
    if not parser:
        return []
    return parser(report_text, platform)


def _aggregate_findings(
    findings: list[NormalizedFinding],
    threshold: int = 3,
    min_severity: str = "warning",
) -> list[dict[str, Any]]:
    """Group findings by (tool, rule_id), filter by threshold, rank by hotness.

    Returns a sorted list of aggregated groups, each containing:
      - tool, platform, rule_id, category, severity (highest), count
      - representative_messages: up to 5 distinct messages
      - representative_files: up to 5 distinct file paths
      - hotness: count * severity_weight
    """
    min_weight = _SEVERITY_WEIGHT.get(min_severity, 2)
    filtered = [f for f in findings if _SEVERITY_WEIGHT.get(f.severity, 0) >= min_weight]

    groups: dict[tuple[str, str], list[NormalizedFinding]] = {}
    for f in filtered:
        key = (f.tool, f.rule_id)
        groups.setdefault(key, []).append(f)

    result: list[dict[str, Any]] = []
    for (tool, rule_id), items in groups.items():
        if len(items) < threshold:
            continue

        severities = [i.severity for i in items]
        highest_sev = max(severities, key=lambda s: _SEVERITY_WEIGHT.get(s, 0))

        messages = list(dict.fromkeys(i.message for i in items if i.message))[:5]
        files = list(dict.fromkeys(i.file_path for i in items if i.file_path))[:5]
        platforms = list(dict.fromkeys(i.platform for i in items))
        categories = list(dict.fromkeys(i.category for i in items if i.category))

        hotness = len(items) * _SEVERITY_WEIGHT.get(highest_sev, 1)

        result.append({
            "tool": tool,
            "rule_id": rule_id,
            "platform": platforms[0] if len(platforms) == 1 else ",".join(platforms),
            "category": categories[0] if categories else "",
            "severity": highest_sev,
            "count": len(items),
            "representative_messages": messages,
            "representative_files": files,
            "hotness": hotness,
        })

    result.sort(key=lambda g: g["hotness"], reverse=True)
    return result


def _discover_content_sources(root: Path) -> list[dict[str, Any]]:
    """Scan project for content sources that match _CONTENT_SCAN_PATTERNS."""
    import glob as _glob_mod
    import copy

    discovered: list[dict[str, Any]] = []
    for pattern in _CONTENT_SCAN_PATTERNS:
        matched_globs: list[str] = []
        for g in pattern["globs"]:
            if _glob_mod.glob(str(root / g), recursive=True):
                matched_globs.append(g)
        if not matched_globs:
            continue

        entry = copy.deepcopy(pattern)
        entry["globs"] = matched_globs
        discovered.append(entry)

    return discovered


def _scan_project(root: Path) -> dict:
    """Scan project root to detect platforms, fix-bug skills, data sources,
    git repo, module examples, integration skills, and rule/contract files."""
    import glob as _glob_mod
    import subprocess as _sp

    resolved = root.resolve()

    platforms: list[str] = []
    for name in _KNOWN_PLATFORMS:
        if (root / name).is_dir():
            platforms.append(name)

    fix_bug_skills: set[str] = set()
    write_context_skill = ""
    read_context_skill = ""
    for skills_dir in [root / ".cursor" / "skills", root / ".claude" / "skills"]:
        if not skills_dir.is_dir():
            continue
        for d in skills_dir.iterdir():
            if not d.is_dir() or not (d / "SKILL.md").exists():
                continue
            if "fix-bug" in d.name:
                fix_bug_skills.add(d.name)
            if "write-dev-context" in d.name and not write_context_skill:
                write_context_skill = d.name
            if "read-dev-context" in d.name and not read_context_skill:
                read_context_skill = d.name

    pitfalls_file = ""
    for candidate in _PITFALLS_CANDIDATES:
        if (root / candidate).exists():
            pitfalls_file = candidate
            break

    feature_glob = ""
    for candidate in _FEATURE_GLOB_CANDIDATES:
        if _glob_mod.glob(str(root / candidate)):
            feature_glob = candidate
            break

    module_examples: list[str] = []
    if feature_glob:
        for f in sorted(_glob_mod.glob(str(root / feature_glob))):
            parent = Path(f).parent.name
            if _re.match(r"^M\d{3}-", parent) and parent not in module_examples:
                module_examples.append(parent)

    project_name = resolved.name

    repo = ""
    try:
        url = _sp.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=str(resolved), stderr=_sp.DEVNULL, text=True,
        ).strip()
        m = _re.search(r"[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
        if m:
            repo = m.group(1)
    except (OSError, _sp.CalledProcessError):
        pass

    rules_file = ""
    for candidate in _RULES_FILE_CANDIDATES:
        if (root / candidate).exists():
            rules_file = candidate
            break

    contract_file = ""
    for candidate in _CONTRACT_FILE_CANDIDATES:
        if (root / candidate).exists():
            contract_file = candidate
            break

    commit_count = 0
    try:
        count_str = _sp.check_output(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(resolved), stderr=_sp.DEVNULL, text=True,
        ).strip()
        commit_count = int(count_str)
    except (OSError, _sp.CalledProcessError, ValueError):
        pass

    content_sources = _discover_content_sources(root)

    static_tools = _detect_tools(root)

    rule_context_sources: list[str] = []
    for p in platforms:
        for rules_dir in [root / ".cursor" / "rules", root / ".claude" / "rules"]:
            candidate = rules_dir / f"{p}.mdc"
            if candidate.exists():
                rule_context_sources.append(str(candidate.relative_to(root)))

    return {
        "platforms": platforms,
        "fix_bug_skills": sorted(fix_bug_skills),
        "pitfalls_file": pitfalls_file,
        "feature_glob": feature_glob,
        "content_sources": content_sources,
        "static_tools": static_tools,
        "rule_context_sources": rule_context_sources,
        "project_name": project_name,
        "repo": repo,
        "module_examples": module_examples,
        "write_context_skill": write_context_skill,
        "read_context_skill": read_context_skill,
        "rules_file": rules_file,
        "contract_file": contract_file,
        "commit_count": commit_count,
    }


def _recommend_template(scan: dict) -> str:
    """Recommend the best template based on detected platforms and data sources."""
    has_pitfalls = bool(scan.get("pitfalls_file"))
    has_feature_ctx = bool(scan.get("feature_glob"))
    if not has_pitfalls and not has_feature_ctx and scan.get("commit_count", 0) >= 50:
        return "legacy"

    platforms = set(scan["platforms"])
    has_mobile = bool(platforms & {"ios", "android", "flutter"})
    has_web = "web" in platforms
    has_backend = bool(platforms & {"backend", "recommend"})

    categories = sum([has_mobile, has_web, has_backend])
    if categories >= 2:
        return "fullstack"
    if has_mobile and not has_web and not has_backend:
        return "mobile"
    if has_web and not has_mobile and not has_backend:
        return "web"
    if has_backend and not has_mobile and not has_web:
        return "backend"
    return "fullstack"


_PROJECT_TEMPLATES: dict[str, dict] = {
    "mobile": {
        "platforms": ["ios", "android"],
        "module_pattern": "M{NNN}-{name}",
        "data_sources": {
            "pitfalls_file": "docs/pitfalls.md",
            "feature_context_glob": "docs/features/*/v*.md",
            "issue_trackers": [{"type": "github", "bug_label": "bug", "state": "closed", "env_token": "GITHUB_TOKEN", "limit": 50}],
        },
        "focus_areas": ["state-lifecycle", "concurrency", "input-validation", "error-handling"],
        "fix_bug_skills": ["ios-fix-bug-ui", "android-fix-bug-ui"],
    },
    "web": {
        "platforms": ["web"],
        "module_pattern": "",
        "data_sources": {
            "pitfalls_file": "docs/pitfalls.md",
            "feature_context_glob": "",
            "issue_trackers": [{"type": "github", "bug_label": "bug", "state": "closed", "env_token": "GITHUB_TOKEN", "limit": 50}],
        },
        "focus_areas": ["input-validation", "error-handling", "cache-consistency"],
        "fix_bug_skills": ["web-fix-bug-ui"],
    },
    "backend": {
        "platforms": ["backend"],
        "module_pattern": "",
        "data_sources": {
            "pitfalls_file": "docs/pitfalls.md",
            "feature_context_glob": "",
            "issue_trackers": [{"type": "github", "bug_label": "bug", "state": "closed", "env_token": "GITHUB_TOKEN", "limit": 50}],
        },
        "focus_areas": ["concurrency", "cache-consistency", "input-validation", "error-handling"],
        "fix_bug_skills": ["backend-dev-lifecycle"],
    },
    "fullstack": {
        "platforms": ["ios", "android", "web", "backend"],
        "module_pattern": "M{NNN}-{name}",
        "data_sources": {
            "pitfalls_file": "dev_context/evolution/pitfalls.md",
            "feature_context_glob": "dev_context/features/*/v*.md",
            "issue_trackers": [{"type": "github", "bug_label": "bug", "state": "closed", "env_token": "GITHUB_TOKEN", "limit": 50}],
        },
        "focus_areas": ["concurrency", "input-validation", "error-handling", "cache-consistency", "state-lifecycle"],
        "fix_bug_skills": ["ios-fix-bug-ui", "web-fix-bug-ui", "backend-dev-lifecycle"],
    },
    "legacy": {
        "platforms": [],
        "module_pattern": "",
        "data_sources": {
            "pitfalls_file": "",
            "feature_context_glob": "",
            "issue_trackers": [{"type": "github", "bug_label": "bug", "state": "closed", "env_token": "GITHUB_TOKEN", "limit": 50}],
            "git_history": {
                "enabled": True,
                "branches": {
                    "default": True,
                    "patterns": ["bf-*", "hf-*", "*bugfix*", "*fix*"],
                    "include_all": False,
                },
                "keywords": ["fix", "bug", "hotfix", "patch", "修复", "缺陷"],
                "limit": 100,
            },
            "code_comments": {
                "enabled": True,
                "markers": ["TODO", "FIXME", "HACK", "WORKAROUND", "XXX"],
                "extensions": [".swift", ".kt", ".ts", ".tsx", ".java", ".py", ".go"],
                "limit": 50,
            },
        },
        "focus_areas": ["concurrency", "input-validation", "error-handling", "cache-consistency"],
        "fix_bug_skills": [],
    },
}


def _generate_preview_md(
    root: Path,
    scan: dict,
    template_name: str,
    template: dict,
    merged_skills: list[str],
) -> Path:
    """Generate an editable Markdown preview of the proposed config."""
    scanned_platforms = set(scan["platforms"])
    template_platforms = set(template["platforms"])
    all_platforms = sorted(scanned_platforms | template_platforms | set(_KNOWN_PLATFORMS), key=_KNOWN_PLATFORMS.index)

    scanned_skills = set(scan["fix_bug_skills"])
    template_skills = set(template.get("fix_bug_skills", []))
    all_skills = sorted(set(merged_skills) | scanned_skills | template_skills)

    all_focus_areas = ["concurrency", "input-validation", "error-handling",
                       "cache-consistency", "state-lifecycle"]
    template_focus = set(template.get("focus_areas", []))

    pitfalls = scan["pitfalls_file"] or template["data_sources"].get("pitfalls_file", "")
    feature_glob = scan["feature_glob"] or template["data_sources"].get("feature_context_glob", "")

    lines: list[str] = []
    lines.append("# Defect KB 初始化配置预览")
    lines.append("")
    lines.append("> 以下配置由 `init` 自动扫描项目目录生成，请检查并按需修改。")
    lines.append('> 确认后说 "确认初始化" 或运行 `init --confirm` 生成 defect-kb.yaml。')
    lines.append("")

    lines.append("## 项目信息")
    lines.append("")
    lines.append(f"- 项目名称: `{scan['project_name']}`")
    lines.append(f"- GitHub Repo: `{scan.get('repo', '')}`")
    lines.append("")

    lines.append("## 自动检测结果")
    lines.append("")
    lines.append(f"- 检测到的平台目录: {', '.join(scan['platforms']) or '(无)'}")
    lines.append(f"- 检测到的 fix-bug Skills: {', '.join(scan['fix_bug_skills']) or '(无)'}")
    lines.append(f"- 检测到的 pitfalls 文件: {scan['pitfalls_file'] or '(无)'}")
    lines.append(f"- 检测到的 feature context: {scan['feature_glob'] or '(无)'}")
    lines.append(f"- 检测到的模块: {', '.join(scan.get('module_examples', [])) or '(无)'}")
    lines.append(f"- 检测到的 write-dev-context Skill: {scan.get('write_context_skill', '') or '(无)'}")
    lines.append(f"- 检测到的 read-dev-context Skill: {scan.get('read_context_skill', '') or '(无)'}")
    lines.append(f"- 检测到的业务规则文件: {scan.get('rules_file', '') or '(无)'}")
    lines.append(f"- 检测到的合约文件: {scan.get('contract_file', '') or '(无)'}")
    lines.append(f"- **推荐模板**: `{template_name}`")
    lines.append("")

    lines.append("## 配置项（可修改）")
    lines.append("")

    lines.append("### 平台列表")
    lines.append("<!-- 修改 [x] 为 [ ] 可移除平台，反之亦然 -->")
    lines.append("")
    for p in all_platforms:
        checked = p in (scanned_platforms | template_platforms)
        tag = "x" if checked else " "
        note_parts: list[str] = []
        if p in scanned_platforms:
            note_parts.append("扫描到")
        if p in template_platforms:
            note_parts.append("模板预设")
        note = f"  ({', '.join(note_parts)})" if note_parts else ""
        lines.append(f"- [{tag}] {p}{note}")
    lines.append("")

    lines.append("### fix_bug_skills（修复 Bug 后自动触发沉淀的 Skill）")
    lines.append("<!-- 修改 [x] 为 [ ] 可移除，也可添加新行 - [x] your-custom-skill -->")
    lines.append("")
    for s in all_skills:
        in_scan = s in scanned_skills
        in_template = s in template_skills
        checked = in_scan or in_template
        tag = "x" if checked else " "
        note_parts = []
        if in_scan:
            note_parts.append("扫描到")
        if in_template:
            note_parts.append("模板预设")
        if not in_scan and in_template:
            note_parts.append("未检测到对应 Skill 文件")
        note = f"  ({', '.join(note_parts)})" if note_parts else ""
        lines.append(f"- [{tag}] {s}{note}")
    lines.append("")

    lines.append("### 关注领域")
    lines.append("")
    for area in all_focus_areas:
        tag = "x" if area in template_focus else " "
        lines.append(f"- [{tag}] {area}")
    lines.append("")

    lines.append("### 模块规范")
    lines.append("")
    lines.append(f"- 模块 ID 模式: `{template.get('module_pattern', '')}`")
    examples_str = ", ".join(scan.get("module_examples", []))
    lines.append(f"- 模块示例: `{examples_str}`")
    lines.append("")

    lines.append("### LLM 配置（高级，快速路径无需配置）")
    lines.append("<!-- 快速路径 (--json + --quality-json) 由 Cursor / Claude Code 的 Agent LLM 完成，无需外部 Key -->")
    lines.append("<!-- 仅在使用 --input 或 --auto-retry 路径时才需要配置 LLM Provider -->")
    lines.append("")
    lines.append("- LLM Provider（仅 --input/--auto-retry 需要）: ``")
    lines.append("- Embedding Provider（用于向量索引/检索）: `local`")
    lines.append("")
    lines.append("### LLM Providers（高级：仅 --input/--auto-retry 路径需要）")
    lines.append("<!-- 勾选的 Provider 会写入 defect-kb.yaml 的 providers 字典 -->")
    lines.append("<!-- 快速路径 (--json + --quality-json) 不需要勾选任何 Provider -->")
    lines.append("")
    for name, p in _DEFAULT_PROVIDERS.items():
        model_str = p.get("model", "")
        base_url_str = p.get("base_url", "") or "(默认)"
        lines.append(f"- [ ] **{name}** — env: `{p['env_key']}`, model: `{model_str}`, base_url: `{base_url_str}`")
    lines.append("")

    lines.append("### 数据源（旧字段，向后兼容）")
    lines.append("<!-- content_sources 存在时忽略这两个旧字段 -->")
    lines.append("")
    lines.append(f"- Pitfalls 文件: `{pitfalls}`")
    lines.append(f"- Feature Context Glob: `{feature_glob}`")
    lines.append("")

    lines.append("### 内容源 Content Sources（通用扫描）")
    lines.append("<!-- 勾选启用的内容源。每个源扫描匹配的 Markdown 文件，提取踩坑/教训段落 -->")
    lines.append("<!-- extract_mode: split_by_heading = 按标题分条, heading_keyword = 按关键词提取段落 -->")
    lines.append("")
    content_sources = scan.get("content_sources", [])
    for cs in content_sources:
        globs_str = ", ".join(cs["globs"])
        mode = cs["extract_mode"]
        conf = cs.get("confidence", "confirmed")
        if mode == "heading_keyword":
            patterns_str = ", ".join(cs.get("heading_patterns", []))
            lines.append(f"- [x] **{cs['name']}** — globs: `{globs_str}`, mode: `{mode}`, patterns: `{patterns_str}`, confidence: `{conf}`")
        else:
            levels_str = ", ".join(str(l) for l in cs.get("heading_levels", [3, 4]))
            lines.append(f"- [x] **{cs['name']}** — globs: `{globs_str}`, mode: `{mode}`, levels: `{levels_str}`, confidence: `{conf}`")
        if cs.get("exclude_globs"):
            lines.append(f"  - exclude: `{', '.join(cs['exclude_globs'])}`")
    lines.append("")

    lines.append("### 静态分析工具 Static Analysis Tools（Mode D0 主动发现）")
    lines.append("<!-- 勾选启用的工具。D0 优先使用已有工具报告，不足时才 fallback 到 AI 扫描 -->")
    lines.append("<!-- mode: auto = 有报告读报告、无则运行工具, run = 总是运行, report = 仅读已有报告 -->")
    lines.append("")
    static_tools = scan.get("static_tools", [])
    if static_tools:
        for t in static_tools:
            lines.append(
                f"- [x] **{t['name']}** — platform: `{t['platform']}`, "
                f"command: `{t['command']}`, report_glob: `{t.get('report_glob', '')}`, "
                f"format: `{t['format']}`, working_dir: `{t['working_dir']}`"
            )
    else:
        lines.append("(未检测到静态分析工具)")
    lines.append("")
    lines.append("- mode: `auto`")
    lines.append("- report_max_age_hours: `24`")
    lines.append("- aggregate_threshold: `3`")
    lines.append("- min_severity: `warning`")
    lines.append("- ai_fallback: `when_d0_insufficient`")
    lines.append("- min_static_findings: `5`")
    lines.append("")

    rule_ctx = scan.get("rule_context_sources", [])
    lines.append("### Rule Context（Cursor/Claude 规则注入 D1 审查）")
    lines.append("<!-- 勾选启用。这些规则文件将作为 D1 Code Review 的增强审查标准 -->")
    lines.append("")
    if rule_ctx:
        for rc in rule_ctx:
            lines.append(f"- [x] `{rc}`")
    else:
        lines.append("(未检测到平台级规则文件)")
    lines.append("")

    lines.append("### Issue Trackers（缺陷来源平台）")
    lines.append("<!-- 勾选启用的平台，填写对应配置。每个平台独立配置认证和参数。 -->")
    lines.append("")
    github_checked = "x" if scan.get("repo") else " "
    lines.append(f"- [{github_checked}] **github** — repo: `{scan.get('repo', '')}`, bug_label: `bug`, state: `closed`, env_token: `GITHUB_TOKEN`, limit: `50`")
    lines.append("- [ ] **yunxiao** — organization_id: ``, project_id: ``, category: `Bug`, env_token: `YUNXIAO_TOKEN`, base_url: `https://devops.aliyun.com`, limit: `50`")
    lines.append("- [ ] **gitlab** — project: ``, bug_label: `bug`, state: `closed`, env_token: `GITLAB_TOKEN`, base_url: `https://gitlab.com`, limit: `50`")
    lines.append("")

    git_hist = template.get("data_sources", {}).get("git_history", {})
    git_hist_checked = "x" if git_hist.get("enabled") else " "
    git_hist_keywords = ", ".join(git_hist.get("keywords", ["fix", "bug", "hotfix", "patch", "修复", "缺陷"]))
    git_hist_limit = git_hist.get("limit", 100)
    branches_cfg = git_hist.get("branches", {})
    branches_default = "true" if branches_cfg.get("default", True) else "false"
    branches_patterns = ", ".join(branches_cfg.get("patterns", ["bf-*", "hf-*", "*bugfix*", "*fix*"]))
    branches_include_all = "true" if branches_cfg.get("include_all", False) else "false"
    lines.append("### Git History Mining（从 git commit 提取缺陷经验）")
    lines.append("<!-- 适用于老项目冷启动，从 bug-fix commit 中挖掘历史缺陷经验 -->")
    lines.append("<!-- 双层过滤：先按分支名模式筛选分支范围，再按关键词筛选 commit -->")
    lines.append("")
    lines.append(f"- [{git_hist_checked}] **启用** — keywords: `{git_hist_keywords}`, limit: `{git_hist_limit}`, branches_default: `{branches_default}`, branches_patterns: `{branches_patterns}`, branches_include_all: `{branches_include_all}`")
    lines.append("")

    code_comments = template.get("data_sources", {}).get("code_comments", {})
    cc_checked = "x" if code_comments.get("enabled") else " "
    cc_markers = ", ".join(code_comments.get("markers", ["TODO", "FIXME", "HACK", "WORKAROUND", "XXX"]))
    cc_extensions = ", ".join(code_comments.get("extensions", [".swift", ".kt", ".ts", ".tsx", ".java", ".py", ".go"]))
    cc_limit = code_comments.get("limit", 50)
    lines.append("### Code Comment Mining（从代码注释提取已知隐患）")
    lines.append("<!-- 扫描 TODO/FIXME/HACK 等标记，提取开发者已知但未修复的潜在缺陷 -->")
    lines.append("")
    lines.append(f"- [{cc_checked}] **启用** — markers: `{cc_markers}`, extensions: `{cc_extensions}`, limit: `{cc_limit}`")
    lines.append("")

    lines.append("### 检索配置（高级）")
    lines.append("<!-- 控制 search 命令的检索质量优化。默认纯语义检索，可开启混合检索和 Reranker -->")
    lines.append("")
    lines.append("- [ ] **hybrid**（混合检索：关键词 + 语义）")
    lines.append("- semantic_weight: `0.7`")
    lines.append("- keyword_weight: `0.3`")
    lines.append("- [ ] **rerank**（cross-encoder 精排）")
    lines.append("- rerank_model: `cross-encoder/ms-marco-MiniLM-L-6-v2`")
    lines.append("")

    lines.append("### Integrations（联动 Skill）")
    lines.append("")
    lines.append(f"- write_context_skill: `{scan.get('write_context_skill', '')}`")
    lines.append(f"- read_context_skill: `{scan.get('read_context_skill', '')}`")
    lines.append(f"- 业务规则文件 (business-rule-audit): `{scan.get('rules_file', '')}`")
    lines.append(f"- 合约文件 (business-rule-audit): `{scan.get('contract_file', '')}`")
    lines.append("")

    preview_path = root / _PREVIEW_FILENAME
    preview_path.write_text("\n".join(lines), encoding="utf-8")
    return preview_path


def _parse_issue_tracker_line(line: str) -> dict[str, Any] | None:
    """Parse a checked issue tracker checkbox line into a tracker config dict.

    Expected format: ``- [x] **github** — key: `val`, key: `val`, ...``
    """
    m = _re.match(r"^- \[[xX]\]\s+\*\*(\w+)\*\*\s*—\s*(.+)$", line)
    if not m:
        return None
    tracker_type = m.group(1)
    kv_str = m.group(2)
    tracker: dict[str, Any] = {"type": tracker_type}
    for kv in _re.findall(r"(\w+):\s*`([^`]*)`", kv_str):
        key, val = kv
        if key == "limit":
            tracker[key] = int(val) if val else 50
        else:
            tracker[key] = val
    return tracker


def _parse_content_source_line(line: str) -> dict[str, Any] | None:
    """Parse a checked content source checkbox line into a source config dict.

    Expected format: ``- [x] **name** — globs: `...`, mode: `...`, ...``
    """
    m = _re.match(r"^- \[[xX]\]\s+\*\*(\S+)\*\*\s*—\s*(.+)$", line)
    if not m:
        return None
    name = m.group(1)
    kv_str = m.group(2)
    kvs = dict(_re.findall(r"(\w+):\s*`([^`]*)`", kv_str))

    globs_raw = kvs.get("globs", "")
    globs = [g.strip() for g in globs_raw.split(",") if g.strip()]
    mode = kvs.get("mode", "heading_keyword")
    confidence = kvs.get("confidence", "confirmed")

    entry: dict[str, Any] = {
        "name": name,
        "globs": globs,
        "extract_mode": mode,
        "source_tag": "pitfalls",
        "confidence": confidence,
    }

    if mode == "heading_keyword":
        patterns_raw = kvs.get("patterns", "")
        entry["heading_patterns"] = [p.strip() for p in patterns_raw.split(",") if p.strip()]
    else:
        levels_raw = kvs.get("levels", "3, 4")
        entry["heading_levels"] = [int(l.strip()) for l in levels_raw.split(",") if l.strip()]

    return entry


def _parse_preview_md(preview_path: Path) -> dict:
    """Parse the preview markdown to extract user's checkbox selections and config values."""
    text = preview_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    result: dict[str, Any] = {
        "project_name": "",
        "repo": "",
        "platforms": [],
        "fix_bug_skills": [],
        "focus_areas": [],
        "module_pattern": "",
        "module_examples": [],
        "llm_provider": "",
        "embedding_provider": "local",
        "enabled_providers": [],
        "pitfalls_file": "",
        "feature_glob": "",
        "content_sources": [],
        "static_tools": [],
        "rule_context_sources": [],
        "static_analysis_config": {},
        "issue_trackers": [],
        "git_history": None,
        "code_comments": None,
        "search": {},
        "write_context_skill": "",
        "read_context_skill": "",
        "rules_file": "",
        "contract_file": "",
    }

    section = ""
    for line in lines:
        stripped = line.strip()

        if stripped.startswith("### "):
            section = stripped[4:].strip()
            continue

        if stripped.startswith("## ") and "项目信息" in stripped:
            section = "project_info"
            continue

        if section == "project_info":
            m = _re.match(r"^- 项目名称:\s*`([^`]*)`", stripped)
            if m:
                result["project_name"] = m.group(1) or ""
            m = _re.match(r"^- GitHub Repo:\s*`([^`]*)`", stripped)
            if m:
                result["repo"] = m.group(1) or ""

        # Checkbox sections
        checkbox_match = _re.match(r"^- \[([ xX])\]\s+(\S+)", stripped)
        if checkbox_match:
            checked = checkbox_match.group(1).lower() == "x"
            value = checkbox_match.group(2)
            if not checked:
                continue
            if section.startswith("平台"):
                result["platforms"].append(value)
            elif "fix_bug_skills" in section:
                result["fix_bug_skills"].append(value)
            elif "关注领域" in section:
                result["focus_areas"].append(value)
            elif "Providers" in section or "providers" in section:
                provider_name = value.strip("*")
                result["enabled_providers"].append(provider_name)
            elif "内容源" in section or "Content Sources" in section:
                cs_entry = _parse_content_source_line(stripped)
                if cs_entry:
                    result["content_sources"].append(cs_entry)
                continue
            elif "Static Analysis" in section or "静态分析" in section:
                kv_pairs = dict(_re.findall(r"(\w+):\s*`([^`]*)`", stripped))
                if kv_pairs and "platform" in kv_pairs:
                    result["static_tools"].append({
                        "platform": kv_pairs.get("platform", ""),
                        "name": value.strip("*"),
                        "command": kv_pairs.get("command", ""),
                        "report_glob": kv_pairs.get("report_glob", ""),
                        "format": kv_pairs.get("format", ""),
                        "working_dir": kv_pairs.get("working_dir", ""),
                    })
            elif "Rule Context" in section or "规则注入" in section:
                m = _re.match(r"^- \[[xX]\]\s+`([^`]+)`", stripped)
                if m:
                    result["rule_context_sources"].append(m.group(1))
            elif "Issue Trackers" in section or "issue_trackers" in section.lower():
                tracker = _parse_issue_tracker_line(stripped)
                if tracker:
                    result["issue_trackers"].append(tracker)
            elif "Git History" in section:
                kv_pairs = dict(_re.findall(r"(\w+):\s*`([^`]*)`", stripped))
                if kv_pairs:
                    keywords = [k.strip() for k in kv_pairs.get("keywords", "fix, bug, hotfix, patch, 修复, 缺陷").split(",")]
                    branches_default = kv_pairs.get("branches_default", "true").lower() == "true"
                    branches_patterns = [p.strip() for p in kv_pairs.get("branches_patterns", "bf-*, hf-*, *bugfix*, *fix*").split(",")]
                    branches_include_all = kv_pairs.get("branches_include_all", "false").lower() == "true"
                    result["git_history"] = {
                        "enabled": True,
                        "branches": {
                            "default": branches_default,
                            "patterns": branches_patterns,
                            "include_all": branches_include_all,
                        },
                        "keywords": keywords,
                        "limit": int(kv_pairs.get("limit", "100")),
                    }
            elif "Code Comment" in section:
                kv_pairs = dict(_re.findall(r"(\w+):\s*`([^`]*)`", stripped))
                if kv_pairs:
                    markers = [m.strip() for m in kv_pairs.get("markers", "TODO, FIXME, HACK, WORKAROUND, XXX").split(",")]
                    extensions = [e.strip() for e in kv_pairs.get("extensions", ".swift, .kt, .ts, .tsx, .java, .py, .go").split(",")]
                    result["code_comments"] = {
                        "enabled": True,
                        "markers": markers,
                        "extensions": extensions,
                        "limit": int(kv_pairs.get("limit", "50")),
                    }
            elif "检索配置" in section:
                if "hybrid" in value.lower():
                    result["search"]["hybrid"] = True
                elif "rerank" in value.lower():
                    result["search"]["rerank"] = True

        if ("内容源" in section or "Content Sources" in section) and stripped.startswith("- exclude:"):
            m = _re.match(r"^- exclude:\s*`([^`]*)`", stripped)
            if m and result["content_sources"]:
                excludes = [e.strip() for e in m.group(1).split(",") if e.strip()]
                result["content_sources"][-1]["exclude_globs"] = excludes

        # Key-value fields
        if "模块规范" in section or "模块" in section:
            m = _re.match(r"^- 模块 ID 模式:\s*`([^`]*)`", stripped)
            if m:
                result["module_pattern"] = m.group(1) or ""
            m = _re.match(r"^- 模块示例:\s*`([^`]*)`", stripped)
            if m and m.group(1):
                result["module_examples"] = [x.strip() for x in m.group(1).split(",") if x.strip()]

        if "LLM 配置" in section:
            m = _re.match(r"^- LLM Provider.*:\s*`([^`]*)`", stripped)
            if m:
                result["llm_provider"] = m.group(1) or ""
            m = _re.match(r"^- Embedding Provider.*:\s*`([^`]*)`", stripped)
            if m:
                result["embedding_provider"] = m.group(1) or "local"

        if "数据源" in section:
            m = _re.match(r"^- Pitfalls 文件:\s*`([^`]*)`", stripped)
            if m:
                result["pitfalls_file"] = m.group(1) or ""
            m = _re.match(r"^- Feature Context Glob:\s*`([^`]*)`", stripped)
            if m:
                result["feature_glob"] = m.group(1) or ""

        if "检索配置" in section:
            m = _re.match(r"^- semantic_weight:\s*`([^`]*)`", stripped)
            if m:
                try:
                    result["search"]["semantic_weight"] = float(m.group(1))
                except ValueError:
                    pass
            m = _re.match(r"^- keyword_weight:\s*`([^`]*)`", stripped)
            if m:
                try:
                    result["search"]["keyword_weight"] = float(m.group(1))
                except ValueError:
                    pass
            m = _re.match(r"^- rerank_model:\s*`([^`]*)`", stripped)
            if m and m.group(1):
                result["search"]["rerank_model"] = m.group(1)

        if "Static Analysis" in section or "静态分析" in section:
            for cfg_key in ("mode", "report_max_age_hours", "aggregate_threshold",
                            "min_severity", "ai_fallback", "min_static_findings"):
                m = _re.match(rf"^- {cfg_key}:\s*`([^`]*)`", stripped)
                if m:
                    result["static_analysis_config"][cfg_key] = m.group(1)

        if "Integrations" in section or "联动" in section:
            m = _re.match(r"^- write_context_skill:\s*`([^`]*)`", stripped)
            if m:
                result["write_context_skill"] = m.group(1) or ""
            m = _re.match(r"^- read_context_skill:\s*`([^`]*)`", stripped)
            if m:
                result["read_context_skill"] = m.group(1) or ""
            m = _re.match(r"^- 业务规则文件.*:\s*`([^`]*)`", stripped)
            if m:
                result["rules_file"] = m.group(1) or ""
            m = _re.match(r"^- 合约文件.*:\s*`([^`]*)`", stripped)
            if m:
                result["contract_file"] = m.group(1) or ""

    return result


def _build_config_from_parsed(parsed: dict) -> dict[str, Any]:
    """Build the final defect-kb.yaml config dict from parsed preview data."""
    llm_section = _build_llm_section(
        provider=parsed["llm_provider"],
        embedding_provider=parsed["embedding_provider"],
        enabled_providers=parsed.get("enabled_providers", []),
    )
    return {
        "project": {"name": parsed["project_name"], "repo": parsed["repo"]},
        "platforms": parsed["platforms"],
        "modules": {"pattern": parsed["module_pattern"], "examples": parsed["module_examples"]},
        "data": {
            "cards_path": "defect-kb-data/cards.jsonl",
            "chroma_path": "defect-kb-data/chroma_db/",
        },
        "llm": llm_section,
        "data_sources": {
            "pitfalls_file": parsed["pitfalls_file"],
            "feature_context_glob": parsed["feature_glob"],
            **({"content_sources": parsed["content_sources"]} if parsed.get("content_sources") else {}),
            "issue_trackers": parsed.get("issue_trackers", []),
            **({"git_history": parsed["git_history"]} if parsed.get("git_history") else {}),
            **({"code_comments": parsed["code_comments"]} if parsed.get("code_comments") else {}),
        },
        "proactive_discovery": _build_proactive_discovery_section(parsed),
        **({"search": parsed["search"]} if parsed.get("search") else {}),
        "integrations": {
            "write_context_skill": parsed.get("write_context_skill", ""),
            "read_context_skill": parsed.get("read_context_skill", ""),
            "fix_bug_skills": parsed["fix_bug_skills"],
        },
    }


def _build_proactive_discovery_section(parsed: dict) -> dict[str, Any]:
    """Build the proactive_discovery config section with static_analysis, rule_context, and methods."""
    static_tools = parsed.get("static_tools", [])
    rule_context_sources = parsed.get("rule_context_sources", [])
    static_cfg = parsed.get("static_analysis_config", {})

    section: dict[str, Any] = {
        "enabled": True,
        "trigger": "when_zero",
    }

    if static_tools:
        section["static_analysis"] = {
            "enabled": True,
            "auto_detect": True,
            "mode": static_cfg.get("mode", "auto"),
            "report_max_age_hours": int(static_cfg.get("report_max_age_hours", 24)),
            "aggregate_threshold": int(static_cfg.get("aggregate_threshold", 3)),
            "min_severity": static_cfg.get("min_severity", "warning"),
            "tools": static_tools,
        }

    if rule_context_sources:
        section["rule_context"] = {
            "enabled": True,
            "sources": rule_context_sources,
        }

    ai_fallback = static_cfg.get("ai_fallback", "when_d0_insufficient" if static_tools else "always")
    min_static = int(static_cfg.get("min_static_findings", 5))
    section["methods"] = {
        "ai_fallback": ai_fallback,
        "min_static_findings": min_static,
        "items": [
            {"type": "code-review", "scope": "git diff HEAD~20", "min_severity": "Important"},
            {"type": "business-rule-audit",
             "rules_file": parsed.get("rules_file", ""),
             "contract_file": parsed.get("contract_file", "")},
            {"type": "brainstorm-edge-case", "focus_areas": parsed["focus_areas"], "max_cards": 10},
        ],
    }

    section["quality_gate"] = {"require_user_confirm": True, "min_quality_score": 3.5}
    return section


def _build_llm_section(
    provider: str = "",
    embedding_provider: str = "local",
    enabled_providers: list[str] | None = None,
) -> dict[str, Any]:
    """Build the llm config section with providers dict."""
    if not enabled_providers:
        enabled_providers = []

    providers_dict: dict[str, Any] = {}
    for name in enabled_providers:
        if name in _DEFAULT_PROVIDERS:
            entry: dict[str, str] = {}
            default = _DEFAULT_PROVIDERS[name]
            entry["env_key"] = default["env_key"]
            if default.get("base_url"):
                entry["base_url"] = default["base_url"]
            entry["model"] = default["model"]
            if default.get("embedding_model"):
                entry["embedding_model"] = default["embedding_model"]
            providers_dict[name] = entry

    section: dict[str, Any] = {
        "provider": provider,
        "embedding_provider": embedding_provider,
        "providers": providers_dict,
    }
    return section


def _ensure_venv(data_dir: Path) -> None:
    """Create a venv in data_dir/.venv and install requirements if not present."""
    import subprocess as _sp

    venv_dir = data_dir / ".venv"
    venv_python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python3")
    if venv_python.exists():
        return

    req_file = Path(__file__).resolve().parent / "requirements.txt"
    if not req_file.exists():
        print("Warning: requirements.txt not found, skipping venv creation.")
        return

    print(f"\n[init] Creating virtual environment at {venv_dir} ...")
    _sp.check_call([sys.executable, "-m", "venv", str(venv_dir)], stdout=_sp.DEVNULL)

    print(f"[init] Installing dependencies from {req_file.name} ...")
    _sp.check_call(
        [str(venv_python), "-m", "pip", "install", "-q", "--upgrade", "pip"],
        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
    )
    _sp.check_call(
        [str(venv_python), "-m", "pip", "install", "-q", "-r", str(req_file)],
    )
    print("[init] Dependencies installed successfully.")


def _resolve_seed_filter(args: argparse.Namespace) -> list[str] | str | None:
    """Translate `--import-seeds` / `--skip-seeds` flags into the value
    `_finalize_init` and `_import_seeds` expect.

    Precedence:
      1. --skip-seeds                      → None (don't import)
      2. --import-seeds=all                → "all" (import everything)
      3. --import-seeds=p1,p2              → ["p1", "p2"] (always plus "common")
      4. --import-seeds without value      → []  (auto-detected from project platforms)
      5. (no flag at all)                  → None (preserve current behavior — opt-in)
    """
    if getattr(args, "skip_seeds", False):
        return None
    raw = getattr(args, "import_seeds", None)
    if raw is None:
        return None
    if raw == "":
        return []
    if raw.strip().lower() == "all":
        return "all"
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    return parts


def _import_seeds(
    root: Path, project_name: str, seed_filter: list[str] | str | None
) -> tuple[int, int]:
    """Import bundled seed cards from defect-kb/seeds/built-in.jsonl into the project.

    `seed_filter`:
      - None         → skip entirely
      - "all"        → import every seed regardless of platform
      - list[str]    → import seeds whose `metadata.platform` is in the list,
                       plus all `"common"` seeds (always included)

    Each imported card gets:
      - new ID (`DEF-{today}-NNN`, continues the project's existing sequence)
      - `metadata.date` set to today
      - `metadata.project` set to the project name
      - `metadata.seed = True`

    Returns `(imported_count, skipped_count)`. Idempotent only when called once
    per init; subsequent calls would import again with new IDs.
    """
    seeds_file = Path(__file__).parent / "seeds" / "built-in.jsonl"
    if seed_filter is None or not seeds_file.exists():
        return 0, 0

    cards_path = root / "defect-kb-data" / "cards.jsonl"
    cards_path.parent.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y%m%d")
    today_iso = datetime.now().strftime("%Y-%m-%d")

    next_seq = 0
    prefix = f"DEF-{today}-"
    if cards_path.exists():
        for line in cards_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ex = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = ex.get("metadata", {}).get("id", "")
            if cid.startswith(prefix):
                try:
                    next_seq = max(next_seq, int(cid.split("-")[-1]))
                except ValueError:
                    pass

    if isinstance(seed_filter, list):
        wanted: set[str] | None = {p.lower() for p in seed_filter} | {"common"}
    else:
        wanted = None

    imported, skipped = 0, 0
    with open(cards_path, "a", encoding="utf-8") as out:
        for line in seeds_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                seed = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            plat = (seed.get("metadata", {}).get("platform") or "common").lower()
            if wanted is not None and plat not in wanted:
                skipped += 1
                continue
            next_seq += 1
            md = seed.setdefault("metadata", {})
            md["id"] = f"DEF-{today}-{next_seq:03d}"
            md["date"] = today_iso
            md["project"] = project_name
            md["seed"] = True
            try:
                card = ExperienceCard(**seed)
            except Exception:
                skipped += 1
                next_seq -= 1
                continue
            out.write(card.model_dump_json(ensure_ascii=False) + "\n")
            imported += 1

    return imported, skipped


def _finalize_init(
    root: Path, config: dict, install_skills: bool, force: bool = False,
    seed_filter: list[str] | str | None = None,
) -> None:
    """Write defect-kb.yaml, create data dir, optionally install skills and import seeds."""
    out_path = root / "defect-kb.yaml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    data_dir = root / "defect-kb-data"
    data_dir.mkdir(parents=True, exist_ok=True)

    if force:
        events_path = data_dir / "events.jsonl"
        if events_path.exists():
            old_count = sum(1 for _ in open(events_path, encoding="utf-8"))
            events_path.write_text("")
            print(f"Warning: events.jsonl cleared (was {old_count} events). cards.jsonl preserved.")

    gitignore_path = data_dir / ".gitignore"
    new_template = (
        "# Defect KB — runtime artifacts (NOT committed)\n"
        "chroma_db/\n"
        "events.jsonl\n"
        "reports/\n"
        ".venv/\n"
        "cards.jsonl.tmp\n"
        "\n"
        "# cards.jsonl and INDEX.md are intentionally tracked so the team\n"
        "# can review the knowledge base alongside code changes.\n"
    )
    if not gitignore_path.exists():
        gitignore_path.write_text(new_template)
    else:
        content = gitignore_path.read_text(encoding="utf-8")
        changed = False
        if "cards.jsonl\n" in content and "cards.jsonl.tmp" not in content:
            content = content.replace("cards.jsonl\n", "cards.jsonl.tmp\n")
            changed = True
        if ".venv/" not in content:
            content = content.rstrip("\n") + "\n.venv/\n"
            changed = True
        if changed:
            gitignore_path.write_text(content)

    if install_skills:
        _install_skills(root)
        _ensure_venv(data_dir)

    if seed_filter is not None:
        project_name = config.get("project", {}).get("name", root.name)
        if isinstance(seed_filter, list) and not seed_filter:
            project_platforms = (
                config.get("project", {}).get("platforms")
                or [config.get("project", {}).get("platform")]
            )
            seed_filter = [p for p in project_platforms if p]
            print(f"  → Auto-selected seed platforms from project: {seed_filter or '(common only)'}")
        imported, skipped = _import_seeds(root, project_name, seed_filter)
        if imported:
            cfg_for_index = {
                "_root": str(root),
                "project": config.get("project", {}),
                "data": {"cards_path": str(data_dir / "cards.jsonl")},
            }
            try:
                _regenerate_index_md(cfg_for_index)
            except Exception as e:
                print(f"  (Skipped INDEX.md regen: {e})")
            print(f"\nImported {imported} seed cards "
                  f"(skipped {skipped}; filter={seed_filter}).")
            print(f"  → They are marked metadata.seed=true and excluded from stats by default.")
            print(f"  → Run 'cli.py search ...' immediately to use them.")
        elif skipped:
            print(f"\nNo seed cards matched filter={seed_filter} ({skipped} skipped).")

    print(f"\nCreated {out_path}")
    print(f"Created {data_dir}/")
    if not install_skills:
        print("Tip: Run with --install-skills to auto-copy skill files into the project.")
    if seed_filter is None:
        print("Tip: Run with --import-seeds=common,<platform> to bootstrap with example cards.")
    print("Done!")


def cmd_init(args: argparse.Namespace) -> None:
    root = Path(args.project_root or ".")
    out_path = root / "defect-kb.yaml"
    preview_path = root / _PREVIEW_FILENAME

    seed_filter = _resolve_seed_filter(args)

    if getattr(args, "confirm", False):
        if not preview_path.exists():
            print(f"No preview file found at {preview_path}.")
            print("Run 'init' first to generate the preview.")
            return
        parsed = _parse_preview_md(preview_path)
        config = _build_config_from_parsed(parsed)
        _finalize_init(
            root, config,
            install_skills=getattr(args, "install_skills", False),
            force=getattr(args, "force", False),
            seed_filter=seed_filter,
        )
        preview_path.unlink(missing_ok=True)
        print(f"Removed preview file: {preview_path}")
        return

    # --- Check existing config ---
    if out_path.exists() and not args.force:
        print(f"defect-kb.yaml already exists at {out_path}. Use --force to overwrite.")
        return

    print("=== defect-kb init ===\n")

    # --- Scan project ---
    scan = _scan_project(root)
    print(f"Scanned project: {root}")
    print(f"  Platforms detected: {', '.join(scan['platforms']) or '(none)'}")
    print(f"  Fix-bug skills detected: {', '.join(scan['fix_bug_skills']) or '(none)'}")
    print(f"  Pitfalls file: {scan['pitfalls_file'] or '(none)'}")
    print(f"  Feature context: {scan['feature_glob'] or '(none)'}")
    cs_names = [cs["name"] for cs in scan.get("content_sources", [])]
    print(f"  Content sources: {', '.join(cs_names) or '(none)'}")
    st_names = [f"{t['name']}({t['platform']})" for t in scan.get("static_tools", [])]
    print(f"  Static analysis tools: {', '.join(st_names) or '(none)'}")
    rc_names = scan.get("rule_context_sources", [])
    print(f"  Rule context sources: {', '.join(rc_names) or '(none)'}")

    # --- Determine template ---
    template_name = args.template or _recommend_template(scan)
    template = _PROJECT_TEMPLATES.get(template_name)
    if not template:
        print(f"Unknown template '{template_name}'. Available: {', '.join(_PROJECT_TEMPLATES.keys())}")
        return
    print(f"\n  Recommended template: {template_name}")

    # --- Merge fix_bug_skills: template preset + scanned ---
    template_skills = set(template.get("fix_bug_skills", []))
    scanned_skills = set(scan["fix_bug_skills"])
    merged_skills = sorted(template_skills | scanned_skills)

    # --- No-preview mode: build config directly and write ---
    if getattr(args, "no_preview", False):
        data_sources = dict(template["data_sources"])
        if scan["pitfalls_file"]:
            data_sources["pitfalls_file"] = scan["pitfalls_file"]
        if scan["feature_glob"]:
            data_sources["feature_context_glob"] = scan["feature_glob"]
        if scan.get("content_sources"):
            data_sources["content_sources"] = scan["content_sources"]
        repo = scan.get("repo", "")
        if repo and "issue_trackers" in data_sources:
            import copy
            trackers = copy.deepcopy(data_sources["issue_trackers"])
            for t in trackers:
                if t.get("type") == "github" and not t.get("repo"):
                    t["repo"] = repo
            data_sources["issue_trackers"] = trackers

        llm_section = _build_llm_section(
            provider="",
            embedding_provider="local",
            enabled_providers=[],
        )

        config: dict[str, Any] = {
            "project": {"name": scan["project_name"], "repo": scan.get("repo", "")},
            "platforms": sorted(set(scan["platforms"]) | set(template["platforms"])),
            "modules": {
                "pattern": template["module_pattern"],
                "examples": scan.get("module_examples", []),
            },
            "data": {
                "cards_path": "defect-kb-data/cards.jsonl",
                "chroma_path": "defect-kb-data/chroma_db/",
            },
            "llm": llm_section,
            "data_sources": data_sources,
            "proactive_discovery": _build_proactive_discovery_section({
                "static_tools": scan.get("static_tools", []),
                "rule_context_sources": scan.get("rule_context_sources", []),
                "static_analysis_config": {},
                "rules_file": scan.get("rules_file", ""),
                "contract_file": scan.get("contract_file", ""),
                "focus_areas": template["focus_areas"],
            }),
            "integrations": {
                "write_context_skill": scan.get("write_context_skill", ""),
                "read_context_skill": scan.get("read_context_skill", ""),
                "fix_bug_skills": merged_skills,
            },
        }
        _finalize_init(
            root, config,
            install_skills=getattr(args, "install_skills", False),
            force=args.force,
            seed_filter=seed_filter,
        )
        return

    # --- Default: generate preview ---
    preview = _generate_preview_md(root, scan, template_name, template, merged_skills)
    print(f"\nPreview generated: {preview}")
    print("Please review and edit the file, then:")
    print('  - Say "确认初始化" in Cursor / Claude Code')
    print("  - Or run: python cli.py init --confirm [--install-skills]")


def _install_skills(project_root: Path) -> None:
    """Copy skill files into the project's .cursor/skills/ (and .claude/skills/ if applicable)."""
    skill_src = Path(__file__).parent.parent.resolve()
    targets = [project_root / ".cursor" / "skills" / "defect-knowledge-base"]

    claude_dir = project_root / ".claude"
    if claude_dir.exists():
        targets.append(project_root / ".claude" / "skills" / "defect-knowledge-base")

    for target in targets:
        resolved_target = target.resolve()
        if resolved_target == skill_src:
            print(f"  Skipped (source == target): {target}")
            continue
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(skill_src, target)
        print(f"  Installed skills to {target}")

    rule_template = skill_src / "templates" / "defect-kb.mdc"
    if rule_template.exists():
        cursor_rules_dir = project_root / ".cursor" / "rules"
        cursor_rules_dir.mkdir(parents=True, exist_ok=True)
        rule_dest = cursor_rules_dir / "defect-kb.mdc"
        if rule_dest.exists():
            print(f"  Rule file already exists, skipped: {rule_dest}")
        else:
            shutil.copy2(rule_template, rule_dest)
            print(f"  Installed rule to {rule_dest}")


# ---------------------------------------------------------------------------
# govern
# ---------------------------------------------------------------------------

def cmd_govern(args: argparse.Namespace) -> None:
    cfg = load_config(args.project_root)
    output_fmt = getattr(args, "output_format", "text")
    cards_path = resolve_path(cfg, "data.cards_path")
    today = datetime.now().strftime("%Y%m%d")

    # --- Step 1: Get card data (from --json or --input via LLM) ---
    if args.json_input:
        try:
            card_data = parse_llm_json(args.json_input)
        except LLMParseError as e:
            _output({"status": "error", "message": str(e)}, output_fmt)
            sys.exit(1)
        needs_llm_key = False
    else:
        if not args.input:
            _output({"status": "error", "message": "--input or --json is required"}, output_fmt)
            sys.exit(1)

        platform = args.platform
        module = args.module or "unknown"
        prompt_template = _load_prompt("standardize.txt")
        prompt = prompt_template.format(
            project_name=cfg["project"]["name"],
            platform=platform,
            module=module,
            raw_text=args.input,
        )

        if output_fmt == "text":
            print("Calling LLM for standardization...")
        raw_result = call_llm(cfg, prompt)

        try:
            card_data = parse_llm_json(raw_result)
        except LLMParseError as e:
            _output(
                {"status": "error", "message": f"LLM returned unparseable output: {e}"},
                output_fmt,
            )
            sys.exit(1)
        needs_llm_key = True

    # --- Determine pipeline path for observability ---
    if args.json_input:
        if getattr(args, "quality_json", None):
            pipeline_path = "agent-preeval"
        else:
            pipeline_path = "agent-std-cli-quality"
    else:
        pipeline_path = "cli-full"

    # --- Step 2: Fill metadata ---
    platform = getattr(args, "platform", None) or card_data.get("metadata", {}).get("platform", "unknown")
    module = getattr(args, "module", None) or card_data.get("metadata", {}).get("module", "unknown")
    source = getattr(args, "source", None) or card_data.get("metadata", {}).get("source", "manual")
    confidence = getattr(args, "confidence", None)
    if not confidence and source in ("pitfalls", "github-issue", "yunxiao-issue", "gitlab-issue",
                                     "manual", "agent-transcript"):
        confidence = "confirmed"
    if not confidence and source == "git-history":
        confidence = "likely"
    if not confidence and source == "code-comment":
        confidence = "hypothesis"
    discovery_method = getattr(args, "discovery_method", None)

    card_id = _next_id(cards_path, today)

    if "metadata" not in card_data:
        card_data["metadata"] = {}
    card_data["metadata"].update({
        "id": card_id,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "project": cfg["project"]["name"],
        "platform": platform,
        "module": module or "unknown",
        "source": source,
    })
    if confidence:
        card_data["metadata"]["confidence"] = confidence
    if source == "ai-proactive" and discovery_method:
        card_data["metadata"]["discovery_method"] = discovery_method

    # --- Step 3: Quality check ---
    quality_score = None
    if not args.skip_quality:
        if getattr(args, "quality_json", None):
            # Fast path: Agent already evaluated quality, CLI only validates
            try:
                qc_data = parse_llm_json(args.quality_json)
            except LLMParseError as e:
                _output({"status": "error", "message": f"--quality-json parse error: {e}"}, output_fmt)
                sys.exit(1)
            quality_score = _build_quality_score(qc_data, override=args.force)
            if output_fmt == "text":
                status = "PASSED" if quality_score.passed else "FAILED"
                print(f"[{pipeline_path}] Quality check (pre-evaluated): {status} (average: {quality_score.average:.1f})")
                if not quality_score.passed:
                    _print_quality_report(quality_score)
        else:
            # Standard path: CLI calls LLM for quality evaluation
            attempt = 0
            max_attempts = (_MAX_RETRY + 1) if args.auto_retry else 1

            while attempt < max_attempts:
                if output_fmt == "text":
                    label = f" (retry {attempt})" if attempt > 0 else ""
                    print(f"Running quality check{label}...")

                try:
                    qc_data = _run_quality_check(cfg, card_data)
                except (LLMParseError, Exception) as e:
                    if output_fmt == "text":
                        print(f"WARNING: Quality check parse error: {e}")
                        print("Proceeding without quality gate.")
                    break

                quality_score = _build_quality_score(qc_data, override=args.force)

                if quality_score.passed:
                    if output_fmt == "text":
                        print(f"[{pipeline_path}] Quality check PASSED (average: {quality_score.average:.1f})")
                    break

                if output_fmt == "text":
                    print(f"[{pipeline_path}] Quality check FAILED (average: {quality_score.average:.1f})")
                    _print_quality_report(quality_score)

                if args.auto_retry and attempt < _MAX_RETRY:
                    improvement_prompt = _load_prompt("standardize.txt").format(
                        project_name=cfg["project"]["name"],
                        platform=platform,
                        module=module,
                        raw_text=(
                            f"IMPROVE the following card based on quality feedback.\n\n"
                            f"Original card:\n{json.dumps(card_data, ensure_ascii=False, indent=2)}\n\n"
                            f"Quality issues to fix:\n"
                            + "\n".join(f"- {issue}" for issue in quality_score.issues)
                        ),
                    )
                    raw_improved = call_llm(cfg, improvement_prompt)
                    try:
                        improved_data = parse_llm_json(raw_improved)
                        preserved_meta = card_data["metadata"].copy()
                        card_data = improved_data
                        card_data["metadata"] = preserved_meta
                    except LLMParseError:
                        if output_fmt == "text":
                            print("WARNING: Retry produced unparseable output, keeping original.")
                        break

                attempt += 1

    # --- Step 4: Decide whether to write ---
    should_write = True
    if quality_score and not quality_score.passed and not args.force:
        _log_event(cfg, {
            "action": "govern",
            "card_id": card_id,
            "pipeline_path": pipeline_path,
            "platform": platform,
            "module": module or "unknown",
            "source": source,
            "quality_passed": False,
            "quality_avg": round(quality_score.average, 2),
            "quality_override": False,
            "rejected": True,
        })
        if output_fmt == "json":
            report = {
                "status": "rejected",
                "card_id": card_id,
                "pipeline_path": pipeline_path,
                "quality": quality_score.model_dump(),
                "card": card_data,
                "message": "Quality gate failed. Use --force to override.",
            }
            _output(report, output_fmt)
            sys.exit(2)
        else:
            print("\nCard NOT written — quality gate failed.")
            print("Use --force to write anyway (marked as quality_override).")
            sys.exit(2)

    if quality_score and not quality_score.passed and args.force:
        quality_score.quality_override = True
        if output_fmt == "text":
            print("Force-writing card with quality_override=true")

    # Attach quality score to metadata
    if quality_score:
        card_data["metadata"]["quality"] = quality_score.model_dump()

    # --- Step 5: Validate and write ---
    try:
        card = ExperienceCard(**card_data)
    except Exception as e:
        _output({"status": "error", "message": f"Card validation failed: {e}"}, output_fmt)
        sys.exit(1)

    _write_card(cards_path, card)

    _log_event(cfg, {
        "action": "govern",
        "card_id": card_id,
        "pipeline_path": pipeline_path,
        "platform": platform,
        "module": module or "unknown",
        "source": source,
        "quality_passed": quality_score.passed if quality_score else None,
        "quality_avg": round(quality_score.average, 2) if quality_score else None,
        "quality_override": quality_score.quality_override if quality_score else False,
        "rejected": False,
    })

    index_md_path = _regenerate_index_md(cfg)

    if output_fmt == "json":
        _output({
            "status": "written",
            "card_id": card_id,
            "pipeline_path": pipeline_path,
            "quality": quality_score.model_dump() if quality_score else None,
            "card": card.model_dump(),
            "index_md": str(index_md_path) if index_md_path else None,
        }, output_fmt)
    else:
        print(f"\n[{pipeline_path}] Card {card_id} written to {cards_path}")
        if index_md_path:
            print(f"INDEX.md regenerated: {index_md_path}")
        print(json.dumps(card.model_dump(), ensure_ascii=False, indent=2))


def _print_quality_report(qs: QualityScore) -> None:
    """Print a human-readable quality dimension breakdown."""
    dims = [
        ("Signal Clarity", qs.signal_clarity),
        ("Root Cause Depth", qs.root_cause_depth),
        ("Fix Portability", qs.fix_portability),
        ("Patch Digest Quality", qs.patch_digest_quality),
        ("Verification Plan", qs.verification_plan),
        ("InfoSec", qs.infosec),
    ]
    print("\n  Dimension Scores:")
    for name, score in dims:
        marker = " ← FAIL" if score < 3 else ""
        print(f"    {name:<25} {score:.1f} / 5.0{marker}")
    print(f"    {'Average':<25} {qs.average:.1f} / 5.0")
    if qs.issues:
        print("\n  Issues:")
        for issue in qs.issues:
            print(f"    - {issue}")


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

def cmd_index(args: argparse.Namespace) -> None:
    cfg = load_config(args.project_root)
    cards_path = resolve_path(cfg, "data.cards_path")
    chroma_path = resolve_path(cfg, "data.chroma_path")
    output_fmt = getattr(args, "output_format", "text")
    embedding_provider = getattr(args, "embedding_provider", None)

    if embedding_provider:
        cfg.setdefault("llm", {})["embedding_provider"] = embedding_provider
        _update_config_field(cfg, "llm.embedding_provider", embedding_provider)

    if not cards_path.exists():
        _output({"status": "error", "message": "No cards.jsonl found. Run 'govern' first."}, output_fmt)
        return

    if getattr(args, "rebuild_md", False):
        index_md_path = _regenerate_index_md(cfg)
        if index_md_path is None:
            _output({"status": "error", "message": "No cards to index."}, output_fmt)
            return
        _output(
            {"status": "ok", "index_md": str(index_md_path)} if output_fmt == "json"
            else f"INDEX.md regenerated: {index_md_path}",
            output_fmt,
        )
        return

    import chromadb

    chroma_path.mkdir(parents=True, exist_ok=True)
    chroma_client = chromadb.PersistentClient(path=str(chroma_path))
    collection = chroma_client.get_or_create_collection(
        name="defect_cards",
        metadata={"hnsw:space": "cosine"},
    )

    existing_ids = set(collection.get()["ids"])
    cards = []
    for line in cards_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        card = json.loads(line)
        cid = card["metadata"]["id"]
        if cid not in existing_ids:
            cards.append(card)

    if not cards:
        _log_event(cfg, {"action": "index", "indexed_count": 0, "total_count": len(existing_ids)})
        _output({"status": "ok", "message": "All cards already indexed.", "indexed": 0}, output_fmt)
        return

    if output_fmt == "text":
        print(f"Indexing {len(cards)} new cards...")

    for card in cards:
        text = (
            card["index_layer"]["problem_summary"]
            + " "
            + " ".join(card["index_layer"]["signals"])
        )
        embedding = get_embedding(cfg, text)

        meta_for_chroma = {
            "platform": card["metadata"]["platform"],
            "module": card["metadata"]["module"],
            "source": card["metadata"]["source"],
            "severity": card["metadata"]["severity"],
            "confidence": card["metadata"].get("confidence") or "",
        }

        collection.add(
            ids=[card["metadata"]["id"]],
            embeddings=[embedding],
            documents=[json.dumps(card, ensure_ascii=False)],
            metadatas=[meta_for_chroma],
        )
        if output_fmt == "text":
            print(f"  Indexed: {card['metadata']['id']}")

    total = len(existing_ids) + len(cards)
    _log_event(cfg, {"action": "index", "indexed_count": len(cards), "total_count": total})
    if output_fmt == "json":
        _output({"status": "ok", "indexed": len(cards), "total": total}, output_fmt)
    else:
        print(f"Done. Total cards in index: {total}")

# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def _keyword_search(cards_path: Path, query: str, platform: str | None = None,
                    top_m: int = 10) -> list[dict]:
    """Search cards by keyword overlap with signals field."""
    import re as _kw_re

    query_tokens = set(_kw_re.split(r"[\s,;./\-_|()（）、，。]+", query.lower()))
    query_tokens.discard("")
    if not query_tokens:
        return []

    scored = []
    for line in cards_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        card = json.loads(line)
        meta = card["metadata"]
        if platform and meta["platform"] != platform:
            continue
        signals = [s.lower() for s in card["index_layer"].get("signals", [])]
        if not signals:
            continue
        hit_count = sum(1 for t in query_tokens if any(t in s for s in signals))
        keyword_score = hit_count / len(query_tokens) if query_tokens else 0.0
        if keyword_score > 0:
            scored.append({
                "id": meta["id"],
                "keyword_score": round(keyword_score, 4),
                "confidence": meta.get("confidence") or "unknown",
                "platform": meta["platform"],
                "module": meta["module"],
                "problem_summary": card["index_layer"]["problem_summary"],
                "fix_strategy": card["resolution_layer"]["fix_strategy"],
                "severity": meta["severity"],
                "source": meta.get("source", ""),
                "_doc_json": json.dumps(card, ensure_ascii=False),
            })
    scored.sort(key=lambda x: x["keyword_score"], reverse=True)
    return scored[:top_m]


def cmd_search(args: argparse.Namespace) -> None:
    cfg = load_config(args.project_root)
    chroma_path = resolve_path(cfg, "data.chroma_path")
    cards_path = resolve_path(cfg, "data.cards_path")
    output_fmt = getattr(args, "output_format", "text")
    embedding_provider = getattr(args, "embedding_provider", None)

    search_cfg = cfg.get("search", {})
    use_rerank = getattr(args, "rerank", False) or search_cfg.get("rerank", False)
    use_hybrid = getattr(args, "hybrid", False) or search_cfg.get("hybrid", False)
    semantic_weight = search_cfg.get("semantic_weight", 0.7)
    keyword_weight = search_cfg.get("keyword_weight", 0.3)
    rerank_model = search_cfg.get("rerank_model", "cross-encoder/ms-marco-MiniLM-L-6-v2")

    if embedding_provider:
        cfg.setdefault("llm", {})["embedding_provider"] = embedding_provider

    if not chroma_path.exists():
        _output({"status": "error", "message": "No ChromaDB found. Run 'index' first."}, output_fmt)
        return

    import chromadb

    chroma_client = chromadb.PersistentClient(path=str(chroma_path))
    collection = chroma_client.get_or_create_collection(name="defect_cards")

    fresh_usage: dict[str, int] = {}
    if cards_path.exists():
        for _line in cards_path.read_text(encoding="utf-8").splitlines():
            if not _line.strip():
                continue
            try:
                _c = json.loads(_line)
                _cid = _c.get("metadata", {}).get("id", "")
                if _cid:
                    fresh_usage[_cid] = int(_c.get("metadata", {}).get("usage_count", 0))
            except json.JSONDecodeError:
                continue

    query_embedding = get_embedding(cfg, args.query)

    where_filter: dict = {}
    if args.platform:
        where_filter["platform"] = args.platform

    fetch_n = args.top_k * 3 if (use_rerank or use_hybrid) else args.top_k
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=fetch_n,
        where=where_filter if where_filter else None,
    )

    candidates: dict[str, dict] = {}
    if results["documents"] and results["documents"][0]:
        for doc, distance in zip(results["documents"][0], results["distances"][0]):
            card = json.loads(doc)
            meta = card["metadata"]
            card_id = meta["id"]
            similarity = 1 - distance
            usage_count = fresh_usage.get(card_id, int(meta.get("usage_count", 0)))
            candidates[card_id] = {
                "id": card_id,
                "similarity": round(similarity, 4),
                "keyword_score": 0.0,
                "usage_count": usage_count,
                "final_score": round(similarity, 4),
                "confidence": meta.get("confidence") or "unknown",
                "platform": meta["platform"],
                "module": meta["module"],
                "problem_summary": card["index_layer"]["problem_summary"],
                "fix_strategy": card["resolution_layer"]["fix_strategy"],
                "severity": meta["severity"],
                "source": meta.get("source", ""),
                "_doc_json": doc,
            }

    if use_hybrid and cards_path.exists():
        kw_results = _keyword_search(cards_path, args.query, args.platform, top_m=fetch_n)
        for kw in kw_results:
            cid = kw["id"]
            if cid in candidates:
                candidates[cid]["keyword_score"] = kw["keyword_score"]
            else:
                kw["similarity"] = 0.0
                kw["final_score"] = 0.0
                candidates[cid] = kw

    if use_hybrid:
        for c in candidates.values():
            c["final_score"] = round(
                semantic_weight * c["similarity"] + keyword_weight * c["keyword_score"], 4
            )

    for c in candidates.values():
        boost = _usage_boost(c.get("usage_count", 0))
        c["usage_boost"] = round(boost, 4)
        c["final_score"] = round(c["final_score"] * boost, 4)

    sorted_candidates = sorted(candidates.values(), key=lambda x: x["final_score"], reverse=True)

    if use_rerank and sorted_candidates:
        from llm import rerank as _rerank
        docs_for_rerank = [c.get("_doc_json", c["problem_summary"]) for c in sorted_candidates]
        reranked_indices = _rerank(args.query, docs_for_rerank, top_k=args.top_k, model_name=rerank_model)
        sorted_candidates = [sorted_candidates[i] for i in reranked_indices]
    else:
        sorted_candidates = sorted_candidates[:args.top_k]

    min_sim = getattr(args, "min_similarity", 0.0)
    if min_sim > 0:
        sorted_candidates = [c for c in sorted_candidates if c["final_score"] >= min_sim]

    formatted_results = []
    for c in sorted_candidates:
        entry = {k: v for k, v in c.items() if not k.startswith("_")}
        formatted_results.append(entry)

    if not formatted_results:
        _log_event(cfg, {
            "action": "search", "query": args.query,
            "platform": args.platform or "", "results_count": 0,
            "top_similarity": 0.0, "hit": False,
        })
        if output_fmt != "compact":
            _output({"status": "ok", "results": [], "message": "No matching cards found."}, output_fmt)
        return

    top_sim = formatted_results[0].get("similarity", 0.0)
    embed_provider = cfg.get("llm", {}).get("embedding_provider", "local")
    hit_threshold = 0.3 if embed_provider == "local" else 0.7
    search_mode = "hybrid+rerank" if (use_hybrid and use_rerank) else "hybrid" if use_hybrid else "rerank" if use_rerank else "semantic"
    _log_event(cfg, {
        "action": "search", "query": args.query,
        "platform": args.platform or "", "results_count": len(formatted_results),
        "top_similarity": round(top_sim, 4), "hit": top_sim > hit_threshold,
        "search_mode": search_mode,
    })

    if not getattr(args, "no_record", False) and top_sim > hit_threshold:
        hit_ids = [r["id"] for r in formatted_results]
        new_counts = _record_search_hits(cards_path, hit_ids)
        for pos, r in enumerate(formatted_results, 1):
            cid = r["id"]
            if cid in new_counts:
                _log_event(cfg, {
                    "action": "search_hit",
                    "card_id": cid,
                    "query": args.query,
                    "position": pos,
                    "similarity": r["similarity"],
                    "final_score": r["final_score"],
                    "usage_count_after": new_counts[cid],
                })

    if output_fmt == "json":
        _output({"status": "ok", "results": formatted_results, "search_mode": search_mode}, output_fmt)
    elif output_fmt == "compact":
        for r in formatted_results:
            print(f"[{r['id']}|{r['severity']}] {r['problem_summary']} → {r.get('fix_strategy', 'N/A')}")
    else:
        mode_label = f" [{search_mode}]" if search_mode != "semantic" else ""
        print(f"Search results ({len(formatted_results)} matches){mode_label}:\n")
        for i, r in enumerate(formatted_results, 1):
            print(f"{i}. [{r['confidence']}] {r['id']} | {r['problem_summary']}")
            score_parts = [f"Similarity: {r['similarity']:.2f}"]
            if r.get("keyword_score", 0) > 0:
                score_parts.append(f"Keyword: {r['keyword_score']:.2f}")
            if use_hybrid:
                score_parts.append(f"Final: {r['final_score']:.2f}")
            print(f"   Platform: {r['platform']} | Module: {r['module']} | {' | '.join(score_parts)}")
            if r["source"] == "ai-proactive":
                print(f"   Source: AI proactive, {r['confidence']}")
            print()

# ---------------------------------------------------------------------------
# browse
# ---------------------------------------------------------------------------

def cmd_browse(args: argparse.Namespace) -> None:
    cfg = load_config(args.project_root)
    cards_path = resolve_path(cfg, "data.cards_path")
    output_fmt = getattr(args, "output_format", "text")

    if not cards_path.exists():
        _output({"status": "error", "message": "No cards.jsonl found."}, output_fmt)
        return

    for line in cards_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        card = json.loads(line)
        if card["metadata"]["id"] == args.id:
            if output_fmt == "json":
                _output(card, output_fmt)
            else:
                _print_card_detail(card)
            return

    _output({"status": "error", "message": f"Card {args.id} not found."}, output_fmt)


def _print_card_detail(card: dict) -> None:
    """Print a card with quality info in a human-readable format."""
    meta = card["metadata"]
    idx = card["index_layer"]
    res = card["resolution_layer"]

    print(f"{'=' * 60}")
    print(f"  ID:       {meta['id']}")
    print(f"  Date:     {meta['date']}")
    print(f"  Project:  {meta['project']}")
    print(f"  Platform: {meta['platform']}")
    print(f"  Module:   {meta['module']}")
    print(f"  Source:   {meta['source']}")
    print(f"  Severity: {meta['severity']}")
    if meta.get("confidence"):
        print(f"  Confidence: {meta['confidence']}")
    print(f"{'=' * 60}")
    print(f"\n  Problem: {idx['problem_summary']}")
    print(f"  Signals: {', '.join(idx['signals'])}")
    print(f"\n  Root Cause: {res['root_cause']}")
    print(f"  Fix Strategy: {res['fix_strategy']}")
    print(f"  Patch Digest: {res['patch_digest']}")
    print(f"  Verification: {res['verification_plan']}")
    if res.get("abandoned_approaches"):
        print(f"\n  Abandoned Approaches:")
        for a in res["abandoned_approaches"]:
            print(f"    - {a}")

    quality = meta.get("quality")
    if quality:
        print(f"\n  {'─' * 40}")
        print(f"  Quality Assessment:")
        passed_str = "PASSED" if quality["passed"] else "FAILED"
        if quality.get("quality_override"):
            passed_str += " (override)"
        print(f"    Status:  {passed_str}")
        print(f"    Average: {quality['average']:.1f}")
        dims = [
            ("Signal Clarity", quality["signal_clarity"]),
            ("Root Cause Depth", quality["root_cause_depth"]),
            ("Fix Portability", quality["fix_portability"]),
            ("Patch Digest", quality["patch_digest_quality"]),
            ("Verification Plan", quality["verification_plan"]),
            ("InfoSec", quality["infosec"]),
        ]
        for name, score in dims:
            marker = " ← FAIL" if score < 3 else ""
            print(f"    {name:<25} {score:.1f}{marker}")
        if quality.get("issues"):
            print(f"\n    Issues:")
            for issue in quality["issues"]:
                print(f"      - {issue}")
    print()

# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

def _bar(value: float, max_value: float, width: int = 20,
         fill: str = "█", empty: str = "░") -> str:
    if max_value <= 0:
        return empty * width
    n = int(round((value / max_value) * width))
    n = max(0, min(width, n))
    return fill * n + empty * (width - n)


def _quality_bar(value: float, width: int = 10) -> str:
    n = int(round((value / 5.0) * width))
    n = max(0, min(width, n))
    return "▰" * n + "▱" * (width - n)


def _relative_time(iso_str: str | None) -> str:
    if not iso_str:
        return "—"
    try:
        ts = datetime.fromisoformat(iso_str)
    except ValueError:
        return iso_str[:10]
    delta = datetime.now() - ts
    secs = delta.total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    days = int(secs // 86400)
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def cmd_stats(args: argparse.Namespace) -> None:
    """Show aggregate quality + usage dashboard across all cards."""
    cfg = load_config(args.project_root)
    cards_path = resolve_path(cfg, "data.cards_path")
    output_fmt = getattr(args, "output_format", "text")

    if not cards_path.exists():
        _output({"status": "error", "message": "No cards.jsonl found."}, output_fmt)
        return

    all_cards: list[dict] = []
    for line in cards_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                all_cards.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    include_seeds = bool(getattr(args, "include_seeds", False))
    include_quick = bool(getattr(args, "include_quick", False))
    seed_count = sum(1 for c in all_cards if c.get("metadata", {}).get("seed"))
    quick_count = sum(1 for c in all_cards if c.get("metadata", {}).get("quick"))

    cards: list[dict] = []
    for c in all_cards:
        m = c.get("metadata", {})
        if m.get("seed") and not include_seeds:
            continue
        if m.get("quick") and not include_quick:
            continue
        cards.append(c)

    total = len(cards)
    with_quality = [c for c in cards if c.get("metadata", {}).get("quality")]
    passed = [c for c in with_quality if c["metadata"]["quality"]["passed"]]
    overridden = [c for c in with_quality if c["metadata"]["quality"].get("quality_override")]

    by_platform: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for c in cards:
        m = c["metadata"]
        by_platform[m["platform"]] = by_platform.get(m["platform"], 0) + 1
        by_severity[m["severity"]] = by_severity.get(m["severity"], 0) + 1
        by_source[m["source"]] = by_source.get(m["source"], 0) + 1

    avg_scores: dict[str, float] = {}
    if with_quality:
        dims = ["signal_clarity", "root_cause_depth", "fix_portability",
                "patch_digest_quality", "verification_plan", "infosec", "average"]
        for dim in dims:
            vals = [c["metadata"]["quality"][dim] for c in with_quality]
            avg_scores[dim] = sum(vals) / len(vals)

    total_hits = sum(int(c.get("metadata", {}).get("usage_count", 0)) for c in cards)
    cards_ever_hit = sum(
        1 for c in cards if int(c.get("metadata", {}).get("usage_count", 0)) > 0
    )

    hot_cards = sorted(
        cards,
        key=lambda c: (
            int(c.get("metadata", {}).get("usage_count", 0)),
            c.get("metadata", {}).get("last_hit_at") or "",
        ),
        reverse=True,
    )
    hot_cards = [c for c in hot_cards if int(c["metadata"].get("usage_count", 0)) > 0][:5]

    cold_candidates = [
        c for c in cards
        if int(c.get("metadata", {}).get("usage_count", 0)) == 0
        and (c.get("metadata", {}).get("quality") or {}).get("average", 5.0) < 4.0
    ]

    project_name = cfg.get("project", {}).get("name", "unknown")

    stats_data = {
        "project": project_name,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_cards": total,
        "all_cards_in_file": len(all_cards),
        "seed_cards_excluded": 0 if include_seeds else seed_count,
        "quick_cards_excluded": 0 if include_quick else quick_count,
        "include_seeds": include_seeds,
        "include_quick": include_quick,
        "total_hits": total_hits,
        "cards_ever_hit": cards_ever_hit,
        "with_quality_check": len(with_quality),
        "quality_passed": len(passed),
        "quality_overridden": len(overridden),
        "by_platform": by_platform,
        "by_severity": by_severity,
        "by_source": by_source,
        "avg_quality_scores": {k: round(v, 2) for k, v in avg_scores.items()},
        "hot_cards": [
            {
                "id": c["metadata"]["id"],
                "usage_count": int(c["metadata"].get("usage_count", 0)),
                "last_hit_at": c["metadata"].get("last_hit_at"),
                "platform": c["metadata"]["platform"],
                "problem_summary": c["index_layer"]["problem_summary"],
            }
            for c in hot_cards
        ],
        "cold_candidates": [
            {
                "id": c["metadata"]["id"],
                "platform": c["metadata"]["platform"],
                "module": c["metadata"].get("module", "unknown"),
                "quality_avg": (c["metadata"].get("quality") or {}).get("average"),
                "problem_summary": c["index_layer"]["problem_summary"],
            }
            for c in cold_candidates
        ],
    }

    if output_fmt == "json":
        _output(stats_data, output_fmt)
        return

    width = 64
    print()
    title = f" Defect KB Dashboard — {project_name} "
    print("╔" + "═" * (width - 2) + "╗")
    print("║" + title.center(width - 2) + "║")
    print("╚" + "═" * (width - 2) + "╝")
    avg_q = avg_scores.get("average", 0.0)
    print(
        f"  Cards: {total}  ·  Quality avg: {avg_q:.2f}  ·  "
        f"Hits: {total_hits} (covered {cards_ever_hit}/{total})"
    )
    excluded_msgs: list[str] = []
    if seed_count and not include_seeds:
        excluded_msgs.append(f"{seed_count} seed")
    if quick_count and not include_quick:
        excluded_msgs.append(f"{quick_count} quick")
    if excluded_msgs:
        print(f"  Excluded: {' + '.join(excluded_msgs)}  "
              f"(use --include-seeds / --include-quick to include)")
    print(f"  Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    print()
    print("── Severity " + "─" * (width - 12))
    sev_max = max(by_severity.values()) if by_severity else 0
    for k in ["P0", "P1", "P2"]:
        v = by_severity.get(k, 0)
        pct = (v / total * 100) if total else 0
        print(f"  {k}  {_bar(v, sev_max)} {v:>3} ({pct:.0f}%)")

    print()
    print("── Platform " + "─" * (width - 12))
    plat_max = max(by_platform.values()) if by_platform else 0
    for k, v in sorted(by_platform.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<14} {_bar(v, plat_max)} {v:>3}")

    print()
    print("── Source " + "─" * (width - 10))
    src_max = max(by_source.values()) if by_source else 0
    for k, v in sorted(by_source.items(), key=lambda kv: -kv[1])[:8]:
        print(f"  {k:<14} {_bar(v, src_max)} {v:>3}")

    if avg_scores:
        print()
        print("── Quality (avg by dimension) " + "─" * (width - 30))
        dim_labels = [
            ("signal_clarity", "Signal Clarity"),
            ("root_cause_depth", "Root Cause Depth"),
            ("fix_portability", "Fix Portability"),
            ("patch_digest_quality", "Patch Digest"),
            ("verification_plan", "Verification Plan"),
            ("infosec", "InfoSec"),
        ]
        for key, label in dim_labels:
            v = avg_scores.get(key, 0.0)
            print(f"  {label:<22} {v:.2f}  {_quality_bar(v)}")

    if hot_cards:
        print()
        print("── Top Hot Cards " + "─" * (width - 17))
        for i, c in enumerate(hot_cards, 1):
            m = c["metadata"]
            uc = int(m.get("usage_count", 0))
            last = _relative_time(m.get("last_hit_at"))
            summary = c["index_layer"]["problem_summary"]
            if len(summary) > 38:
                summary = summary[:37] + "…"
            print(f"  {i}. ★{uc:>2}  [{m['id']}] {summary}  ({last})")

    if cold_candidates:
        print()
        print(f"── Cold Candidates (never hit, Q<4) — {len(cold_candidates)} " + "─" * 5)
        for c in cold_candidates[:5]:
            m = c["metadata"]
            q_avg = (m.get("quality") or {}).get("average", 0.0)
            summary = c["index_layer"]["problem_summary"]
            if len(summary) > 40:
                summary = summary[:39] + "…"
            print(f"  • [{m['id']}] {summary}  (Q{q_avg:.1f})")
        if len(cold_candidates) > 5:
            print(f"  … and {len(cold_candidates) - 5} more.")
        print("  → Run 'cli.py browse --id <ID>' to inspect or improve.")

    print()
    print("── Quality Gate " + "─" * (width - 16))
    if with_quality:
        pass_pct = len(passed) / len(with_quality) * 100
        print(f"  Total cards          {total}")
        print(f"  With quality check   {len(with_quality)} ({len(with_quality)/total*100:.0f}%)")
        print(f"  Quality passed       {len(passed)} ({pass_pct:.0f}%)")
        print(f"  Force-overridden     {len(overridden)}")
    else:
        print(f"  Total cards          {total}")
        print("  (no quality scores recorded yet — run 'govern' to start scoring)")
    print()

# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def _load_cards(cards_path: Path) -> list[dict]:
    if not cards_path.exists():
        return []
    return [
        json.loads(line)
        for line in cards_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_events(events_path: Path) -> list[dict]:
    if not events_path.exists():
        return []
    return [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _month_key(iso_date: str) -> str:
    """Extract YYYY-MM from an ISO date or datetime string."""
    return iso_date[:7]


def _filter_by_period(items: list[dict], date_key: str, period: str) -> list[dict]:
    """Filter items by period (all / month / quarter) based on date_key field."""
    if period == "all":
        return items
    now = datetime.now()
    if period == "month":
        cutoff = now.replace(day=1)
        if cutoff.month == 1:
            cutoff = cutoff.replace(year=cutoff.year - 1, month=12)
        else:
            cutoff = cutoff.replace(month=cutoff.month - 1)
    else:  # quarter
        from datetime import timedelta
        cutoff = now - timedelta(days=90)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    return [item for item in items if (item.get(date_key, "") or "")[:10] >= cutoff_str]


def _aggregate_report_data(cfg: dict, period: str) -> dict:
    """Aggregate all report data from cards + events into a single dict for rendering."""
    root = Path(cfg["_root"])
    cards_path = resolve_path(cfg, "data.cards_path")
    events_path = root / "defect-kb-data" / "events.jsonl"

    all_cards = _load_cards(cards_path)
    all_events = _load_events(events_path)

    cutoff_str = ""
    if period != "all":
        from datetime import timedelta
        now = datetime.now()
        cutoff = now - timedelta(days=30 if period == "month" else 90)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        cards_in_period = [c for c in all_cards if (c.get("metadata", {}).get("date", "") or "") >= cutoff_str]
        events_in_period = [e for e in all_events if (e.get("ts", "") or "")[:10] >= cutoff_str]
    else:
        cards_in_period = all_cards
        events_in_period = all_events

    total_all = len(all_cards)
    new_in_period = len(cards_in_period) if period != "all" else total_all

    with_quality = [c for c in cards_in_period if c.get("metadata", {}).get("quality")]
    passed = [c for c in with_quality if c["metadata"]["quality"]["passed"]]
    overridden = [c for c in with_quality if c["metadata"]["quality"].get("quality_override")]
    pass_rate = (len(passed) / len(with_quality) * 100) if with_quality else 0
    override_rate = (len(overridden) / len(with_quality) * 100) if with_quality else 0

    by_platform: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_module: dict[str, int] = {}
    for c in cards_in_period:
        m = c["metadata"]
        by_platform[m["platform"]] = by_platform.get(m["platform"], 0) + 1
        by_severity[m["severity"]] = by_severity.get(m["severity"], 0) + 1
        by_source[m["source"]] = by_source.get(m["source"], 0) + 1
        mod = m.get("module", "unknown")
        by_module[mod] = by_module.get(mod, 0) + 1

    dim_keys = ["signal_clarity", "root_cause_depth", "fix_portability",
                "patch_digest_quality", "verification_plan", "infosec"]
    dim_labels = {
        "signal_clarity": "Signal Clarity",
        "root_cause_depth": "Root Cause Depth",
        "fix_portability": "Fix Portability",
        "patch_digest_quality": "Patch Digest Quality",
        "verification_plan": "Verification Plan",
        "infosec": "InfoSec",
    }
    avg_scores: dict[str, float] = {}
    overall_avg = 0.0
    if with_quality:
        for dim in dim_keys:
            vals = [c["metadata"]["quality"][dim] for c in with_quality]
            avg_scores[dim] = sum(vals) / len(vals)
        avg_vals = [c["metadata"]["quality"]["average"] for c in with_quality]
        overall_avg = sum(avg_vals) / len(avg_vals)

    lowest_dim = ""
    if avg_scores:
        lowest_dim = min(avg_scores, key=avg_scores.get)

    monthly_quality: dict[str, list[float]] = {}
    for c in cards_in_period:
        q = c.get("metadata", {}).get("quality")
        if q:
            mk = _month_key(c["metadata"]["date"])
            monthly_quality.setdefault(mk, []).append(q["average"])

    search_events = [e for e in events_in_period if e.get("action") == "search"]
    search_total = len(search_events)
    search_hits = sum(1 for e in search_events if e.get("hit"))
    search_hit_rate = (search_hits / search_total * 100) if search_total else 0
    avg_top_sim = (sum(e.get("top_similarity", 0) for e in search_events) / search_total) if search_total else 0
    embed_prov = cfg.get("llm", {}).get("embedding_provider", "local")
    hit_threshold = 0.3 if embed_prov == "local" else 0.7

    monthly_search: dict[str, int] = {}
    for e in search_events:
        mk = _month_key(e.get("ts", ""))
        monthly_search[mk] = monthly_search.get(mk, 0) + 1

    govern_events = [e for e in events_in_period if e.get("action") == "govern"]
    govern_total = len(govern_events)
    govern_rejected = sum(1 for e in govern_events if e.get("rejected"))
    reject_rate = (govern_rejected / govern_total * 100) if govern_total else 0

    monthly_govern: dict[str, int] = {}
    for e in govern_events:
        mk = _month_key(e.get("ts", ""))
        monthly_govern[mk] = monthly_govern.get(mk, 0) + 1

    searched_modules = {e["platform"] for e in search_events if e.get("platform")}
    card_modules = set(by_module.keys())
    modules_no_search = card_modules - searched_modules if searched_modules else set()
    searched_platforms = {e["platform"] for e in search_events if e.get("platform")}
    card_platforms = set(by_platform.keys())
    platforms_no_cards = searched_platforms - card_platforms if card_platforms else set()

    # --- KB Value metrics (Section 8) ---
    outcome_events = [e for e in events_in_period if e.get("action") == "search_outcome"]
    outcome_total = len(outcome_events)
    outcome_with_results = sum(1 for e in outcome_events if e.get("outcome") != "no_results")
    outcome_applied = sum(1 for e in outcome_events if e.get("outcome") == "applied")
    outcome_viewed = sum(1 for e in outcome_events if e.get("outcome") == "viewed")
    search_engage_rate = (outcome_with_results / outcome_total * 100) if outcome_total else 0
    search_apply_rate = (outcome_applied / outcome_total * 100) if outcome_total else 0

    fix_events = [e for e in events_in_period if e.get("action") == "fix_session"]
    fix_total = len(fix_events)
    fix_with_kb = [e for e in fix_events if e.get("kb_searched")]
    fix_kb_applied = [e for e in fix_events if e.get("kb_card_applied")]
    fix_ref_rate = (len(fix_kb_applied) / fix_total * 100) if fix_total else 0

    hyp_with_kb = [e.get("hypotheses_tried", 0) for e in fix_with_kb if e.get("hypotheses_tried") is not None]
    hyp_without_kb = [e.get("hypotheses_tried", 0) for e in fix_events if not e.get("kb_searched") and e.get("hypotheses_tried") is not None]
    avg_hyp_with = (sum(hyp_with_kb) / len(hyp_with_kb)) if hyp_with_kb else 0
    avg_hyp_without = (sum(hyp_without_kb) / len(hyp_without_kb)) if hyp_without_kb else 0
    hyp_reduction = ((avg_hyp_without - avg_hyp_with) / avg_hyp_without * 100) if avg_hyp_without > 0 else 0

    conf_events = [e for e in events_in_period if e.get("action") == "confidence_change"]
    conf_upgrades = len(conf_events)

    return {
        "project_name": cfg.get("project", {}).get("name", "unknown"),
        "period": period,
        "period_label": {"all": "全部历史", "month": "最近 30 天", "quarter": "最近 90 天"}[period],
        "now_str": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_all": total_all, "new_in_period": new_in_period,
        "with_quality_count": len(with_quality), "passed_count": len(passed),
        "overridden_count": len(overridden), "pass_rate": pass_rate, "override_rate": override_rate,
        "by_platform": by_platform, "by_severity": by_severity, "by_source": by_source, "by_module": by_module,
        "dim_keys": dim_keys, "dim_labels": dim_labels, "avg_scores": avg_scores,
        "overall_avg": overall_avg, "lowest_dim": lowest_dim,
        "monthly_quality": monthly_quality,
        "search_total": search_total, "search_hits": search_hits,
        "search_hit_rate": search_hit_rate, "avg_top_sim": avg_top_sim,
        "hit_threshold": hit_threshold, "monthly_search": monthly_search,
        "govern_total": govern_total, "govern_rejected": govern_rejected,
        "reject_rate": reject_rate, "monthly_govern": monthly_govern,
        "modules_no_search": modules_no_search, "platforms_no_cards": platforms_no_cards,
        "outcome_total": outcome_total, "outcome_with_results": outcome_with_results,
        "outcome_applied": outcome_applied, "outcome_viewed": outcome_viewed,
        "search_engage_rate": search_engage_rate, "search_apply_rate": search_apply_rate,
        "fix_total": fix_total, "fix_with_kb_count": len(fix_with_kb),
        "fix_kb_applied_count": len(fix_kb_applied), "fix_ref_rate": fix_ref_rate,
        "avg_hyp_with": avg_hyp_with, "avg_hyp_without": avg_hyp_without,
        "hyp_reduction": hyp_reduction, "conf_upgrades": conf_upgrades,
        "cards": cards_in_period,
    }


def _build_md_report(d: dict) -> str:
    """Build Markdown report text from aggregated data."""
    lines: list[str] = []
    lines.append("# 缺陷知识库质量报告")
    lines.append(f"> 项目: {d['project_name']} | 报告周期: {d['period_label']} | 生成时间: {d['now_str']}")
    lines.append("")

    lines.append("## 1. 知识库概览")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| 总卡片数 | {d['total_all']} |")
    if d["period"] != "all":
        lines.append(f"| 本期新增 | {d['new_in_period']} |")
    lines.append(f"| 有质量评分 | {d['with_quality_count']} |")
    lines.append(f"| 质量通过率 | {d['pass_rate']:.1f}% ({d['passed_count']}/{d['with_quality_count']}) |")
    lines.append(f"| 覆写率 | {d['override_rate']:.1f}% ({d['overridden_count']}/{d['with_quality_count']}) |")
    lines.append("")

    lines.append("## 2. 卡片分布")
    lines.append("")
    for title, data, sort_key in [
        ("按平台", d["by_platform"], lambda x: x[0]),
        ("按来源", d["by_source"], lambda x: x[0]),
        ("按模块", d["by_module"], lambda x: -x[1]),
    ]:
        lines.append(f"### {title}")
        lines.append("")
        lines.append(f"| {title[1:]} | 数量 |")
        lines.append("|------|------|")
        for k, v in sorted(data.items(), key=sort_key):
            lines.append(f"| {k} | {v} |")
        lines.append("")
    lines.append("### 按严重度")
    lines.append("")
    lines.append("| 严重度 | 数量 |")
    lines.append("|--------|------|")
    for sev in ["P0", "P1", "P2"]:
        lines.append(f"| {sev} | {d['by_severity'].get(sev, 0)} |")
    lines.append("")

    lines.append("## 3. 质量评分分析")
    lines.append("")
    if d["avg_scores"]:
        lines.append("### 6 维度平均分")
        lines.append("")
        lines.append("| 维度 | 平均分 |")
        lines.append("|------|--------|")
        for dim in d["dim_keys"]:
            if dim in d["avg_scores"]:
                lines.append(f"| {d['dim_labels'].get(dim, dim)} | {d['avg_scores'][dim]:.2f} |")
        lines.append(f"| Overall Average | {d['overall_avg']:.2f} |")
        lines.append("")
        if d["lowest_dim"]:
            ld = d["lowest_dim"]
            lines.append(f"**最低维度**: {d['dim_labels'].get(ld, ld)} ({d['avg_scores'][ld]:.2f})")
            lines.append("  — 建议在后续卡片写入时加强该维度的信息完整性。")
            lines.append("")
        if d["monthly_quality"]:
            lines.append("### 质量趋势（按月）")
            lines.append("")
            lines.append("| 月份 | 卡片数 | 平均分 |")
            lines.append("|------|--------|--------|")
            for mk in sorted(d["monthly_quality"].keys()):
                vals = d["monthly_quality"][mk]
                lines.append(f"| {mk} | {len(vals)} | {sum(vals)/len(vals):.2f} |")
            lines.append("")
    else:
        lines.append("暂无质量评分数据。")
        lines.append("")

    lines.append("## 4. 检索效果")
    lines.append("")
    if d["search_total"] > 0:
        lines.append("| 指标 | 值 |")
        lines.append("|------|-----|")
        lines.append(f"| 总检索次数 | {d['search_total']} |")
        lines.append(f"| 命中次数 (similarity > {d['hit_threshold']}) | {d['search_hits']} |")
        lines.append(f"| 命中率 | {d['search_hit_rate']:.1f}% |")
        lines.append(f"| 平均 top similarity | {d['avg_top_sim']:.4f} |")
        lines.append("")
        if d["monthly_search"]:
            lines.append("### 按月检索量趋势")
            lines.append("")
            lines.append("| 月份 | 检索次数 |")
            lines.append("|------|----------|")
            for mk in sorted(d["monthly_search"].keys()):
                lines.append(f"| {mk} | {d['monthly_search'][mk]} |")
            lines.append("")
    else:
        lines.append("暂无检索事件数据。运行 `search` 命令后将自动记录。")
        lines.append("")

    lines.append("## 5. 沉淀效率")
    lines.append("")
    if d["govern_total"] > 0:
        lines.append("| 指标 | 值 |")
        lines.append("|------|-----|")
        lines.append(f"| govern 调用次数 | {d['govern_total']} |")
        lines.append(f"| 门禁拒绝次数 | {d['govern_rejected']} |")
        lines.append(f"| 拒绝率 | {d['reject_rate']:.1f}% |")
        lines.append("")
        if d["monthly_govern"]:
            lines.append("### 按月沉淀量趋势")
            lines.append("")
            lines.append("| 月份 | 沉淀次数 |")
            lines.append("|------|----------|")
            for mk in sorted(d["monthly_govern"].keys()):
                lines.append(f"| {mk} | {d['monthly_govern'][mk]} |")
            lines.append("")
    else:
        lines.append("暂无沉淀事件数据。运行 `govern` 命令后将自动记录。")
        lines.append("")

    lines.append("## 6. 覆盖缺口分析")
    lines.append("")
    if d["modules_no_search"]:
        lines.append("### 有卡片但 0 检索的模块（可能无人使用）")
        lines.append("")
        for m in sorted(d["modules_no_search"]):
            lines.append(f"- {m}")
        lines.append("")
    if d["platforms_no_cards"]:
        lines.append("### 有检索但 0 卡片的平台（知识缺口）")
        lines.append("")
        for p in sorted(d["platforms_no_cards"]):
            lines.append(f"- {p}")
        lines.append("")
    if not d["modules_no_search"] and not d["platforms_no_cards"]:
        lines.append("暂无明显覆盖缺口（数据量不足或覆盖均匀）。")
        lines.append("")

    lines.append("## 7. 建议")
    lines.append("")
    suggestions: list[str] = []
    if d["total_all"] < 10:
        suggestions.append("知识库卡片数量较少，建议通过 `govern` 命令批量导入存量缺陷经验，加速知识积累。")
    if d["search_total"] == 0:
        suggestions.append("尚未产生检索记录，建议在 Bug 修复前养成先查知识库的习惯，提升修复效率。")
    if d["search_total"] > 0 and d["search_hit_rate"] < 50:
        suggestions.append(f"检索命中率偏低 ({d['search_hit_rate']:.1f}%)，建议补充更多卡片或优化卡片的 signals 字段。")
    ld = d["lowest_dim"]
    if ld and d["avg_scores"].get(ld, 5) < 3.5:
        suggestions.append(f"质量维度 **{d['dim_labels'].get(ld, ld)}** 偏低，写入卡片时请加强该维度描述。")
    if d["govern_total"] > 0 and d["reject_rate"] > 30:
        suggestions.append(f"质量门禁拒绝率较高 ({d['reject_rate']:.1f}%)，建议使用 `--auto-retry` 参数自动改进低质量卡片。")
    if not suggestions:
        suggestions.append("当前知识库运转良好，继续保持！")
    for s in suggestions:
        lines.append(f"- {s}")
    lines.append("")

    lines.append("## 8. 知识库价值")
    lines.append("")
    if d["outcome_total"] > 0 or d["fix_total"] > 0:
        lines.append("| 指标 | 值 |")
        lines.append("|------|-----|")
        if d["outcome_total"] > 0:
            lines.append(f"| 检索参与率 | {d['search_engage_rate']:.1f}% ({d['outcome_with_results']}/{d['outcome_total']}) |")
            lines.append(f"| 检索应用率 | {d['search_apply_rate']:.1f}% ({d['outcome_applied']}/{d['outcome_total']}) |")
        if d["fix_total"] > 0:
            lines.append(f"| 修复参考率 | {d['fix_ref_rate']:.1f}% ({d['fix_kb_applied_count']}/{d['fix_total']}) |")
            if d["fix_with_kb_count"] > 0:
                lines.append(f"| 平均排查假设数（有 KB） | {d['avg_hyp_with']:.1f} |")
            hyp_without_count = d["fix_total"] - d["fix_with_kb_count"]
            if hyp_without_count > 0:
                lines.append(f"| 平均排查假设数（无 KB） | {d['avg_hyp_without']:.1f} |")
            if d["avg_hyp_without"] > 0 and d["fix_with_kb_count"] > 0:
                lines.append(f"| 假设减少率 | {d['hyp_reduction']:.1f}% |")
        if d["conf_upgrades"] > 0:
            lines.append(f"| 置信度升级数 | {d['conf_upgrades']} |")
        lines.append("")
    else:
        lines.append("暂无知识库价值数据。使用 `search-defect-kb` 和 `post-fix-hook` 后将自动采集。")
        lines.append("")

    lines.append("## 9. ROI 摘要")
    lines.append("")
    if d["fix_total"] > 0:
        lines.append(f"本期共 {d['fix_total']} 次 Bug 修复，其中 {d['fix_kb_applied_count']} 次参考了知识库（{d['fix_ref_rate']:.1f}%）。")
        if d["avg_hyp_without"] > 0 and d["fix_with_kb_count"] > 0:
            lines.append(f"参考知识库的修复平均经历 {d['avg_hyp_with']:.1f} 个失败假设，未参考的平均 {d['avg_hyp_without']:.1f} 个，")
            lines.append(f"知识库帮助减少了约 {d['hyp_reduction']:.1f}% 的无效排查尝试。")
        lines.append("")
        if d["outcome_total"] > 0:
            lines.append(f"检索 {d['outcome_total']} 次，{d['outcome_applied']} 次结果被实际应用（应用率 {d['search_apply_rate']:.1f}%）。")
            lines.append("")
    elif d["outcome_total"] > 0:
        lines.append(f"检索 {d['outcome_total']} 次，{d['outcome_applied']} 次结果被实际应用（应用率 {d['search_apply_rate']:.1f}%）。")
        lines.append("")
    else:
        lines.append("暂无 ROI 数据。随着知识库使用积累，此处将展示投入产出分析。")
        lines.append("")

    return "\n".join(lines)


_CHARTJS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"


def _get_chartjs_inline(cfg: dict) -> str:
    """Return Chart.js source for inline embedding, with local cache fallback."""
    root = Path(cfg["_root"])
    cache_dir = root / "defect-kb-data" / ".cache"
    cache_file = cache_dir / "chart.umd.min.js"

    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")

    try:
        req = urllib.request.Request(_CHARTJS_CDN, headers={"User-Agent": "defect-kb-cli"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            js_text = resp.read().decode("utf-8")
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(js_text, encoding="utf-8")
        return js_text
    except Exception:
        return ""


def _build_html_report(d: dict, chartjs_inline: str = "") -> str:
    """Build a self-contained HTML dashboard with Chart.js visualizations."""
    if chartjs_inline:
        chartjs_tag = f"<script>{chartjs_inline}</script>"
    else:
        chartjs_tag = '<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>'
    platform_labels = json.dumps(sorted(d["by_platform"].keys()), ensure_ascii=False)
    platform_values = json.dumps([d["by_platform"][k] for k in sorted(d["by_platform"].keys())])
    severity_values = json.dumps([d["by_severity"].get(s, 0) for s in ["P0", "P1", "P2"]])
    source_labels = json.dumps(sorted(d["by_source"].keys()), ensure_ascii=False)
    source_values = json.dumps([d["by_source"][k] for k in sorted(d["by_source"].keys())])
    module_labels = json.dumps([k for k, _ in sorted(d["by_module"].items(), key=lambda x: -x[1])], ensure_ascii=False)
    module_values = json.dumps([v for _, v in sorted(d["by_module"].items(), key=lambda x: -x[1])])

    radar_labels = json.dumps([d["dim_labels"].get(k, k) for k in d["dim_keys"]], ensure_ascii=False)
    radar_values = json.dumps([round(d["avg_scores"].get(k, 0), 2) for k in d["dim_keys"]])

    mq_months = sorted(d["monthly_quality"].keys())
    mq_labels = json.dumps(mq_months, ensure_ascii=False)
    mq_values = json.dumps([round(sum(d["monthly_quality"][m]) / len(d["monthly_quality"][m]), 2) for m in mq_months])

    ms_months = sorted(d["monthly_search"].keys())
    ms_labels = json.dumps(ms_months, ensure_ascii=False)
    ms_values = json.dumps([d["monthly_search"][m] for m in ms_months])

    mg_months = sorted(d["monthly_govern"].keys())
    mg_labels = json.dumps(mg_months, ensure_ascii=False)
    mg_values = json.dumps([d["monthly_govern"][m] for m in mg_months])

    gap_rows = ""
    for m in sorted(d["modules_no_search"]):
        gap_rows += f'<tr><td>{m}</td><td>有卡片无检索</td></tr>'
    for p in sorted(d["platforms_no_cards"]):
        gap_rows += f'<tr><td>{p}</td><td>有检索无卡片</td></tr>'

    cards_json = json.dumps(d.get("cards", []), ensure_ascii=False)

    suggestions = []
    if d["total_all"] < 10:
        suggestions.append("知识库卡片数量较少，建议通过 govern 命令批量导入存量缺陷经验。")
    if d["search_total"] == 0:
        suggestions.append("尚未产生检索记录，建议在 Bug 修复前先查知识库。")
    if d["search_total"] > 0 and d["search_hit_rate"] < 50:
        suggestions.append(f"检索命中率偏低 ({d['search_hit_rate']:.1f}%)，建议补充更多卡片或优化 signals 字段。")
    if not suggestions:
        suggestions.append("当前知识库运转良好，继续保持！")
    suggestion_html = "".join(f'<div class="suggestion">{s}</div>' for s in suggestions)

    roi_text = ""
    if d["fix_total"] > 0:
        roi_text = f"本期共 {d['fix_total']} 次 Bug 修复，其中 {d['fix_kb_applied_count']} 次参考了知识库（{d['fix_ref_rate']:.1f}%）。"
        if d["avg_hyp_without"] > 0 and d["fix_with_kb_count"] > 0:
            roi_text += f"<br>参考知识库的修复平均经历 {d['avg_hyp_with']:.1f} 个失败假设，未参考的平均 {d['avg_hyp_without']:.1f} 个，知识库帮助减少了约 {d['hyp_reduction']:.1f}% 的无效排查尝试。"
    elif d["outcome_total"] > 0:
        roi_text = f"检索 {d['outcome_total']} 次，{d['outcome_applied']} 次结果被实际应用（应用率 {d['search_apply_rate']:.1f}%）。"
    else:
        roi_text = "暂无 ROI 数据。随着知识库使用积累，此处将展示投入产出分析。"

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>缺陷知识库报告 — {d["project_name"]}</title>
{chartjs_tag}
<style>
  :root {{ --primary: #FF3D60; --accent: #00C5CA; --bg: #F6F6F6; --card: #fff;
           --text: #111114; --muted: #8E8E8E; --border: #EEEEEE; --success: #22C55E;
           --warn: #F59E0B; --danger: #EF4444; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif;
          background: var(--bg); color: var(--text); line-height: 1.6; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 24px 20px; }}
  header {{ text-align: center; margin-bottom: 32px; }}
  header h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 8px; }}
  header .meta {{ color: var(--muted); font-size: 14px; }}
  .section-title {{ font-size: 18px; font-weight: 600; margin: 32px 0 16px; padding-left: 12px;
                    border-left: 4px solid var(--primary); }}
  .metrics-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; }}
  .metric-card {{ background: var(--card); border-radius: 12px; padding: 20px; text-align: center;
                  box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .metric-card .value {{ font-size: 32px; font-weight: 700; color: var(--primary); }}
  .metric-card .label {{ font-size: 13px; color: var(--muted); margin-top: 4px; }}
  .metric-card .sub {{ font-size: 12px; color: var(--muted); }}
  .metric-card.accent .value {{ color: var(--accent); }}
  .metric-card.success .value {{ color: var(--success); }}
  .charts-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 20px; margin-top: 16px; }}
  .chart-card {{ background: var(--card); border-radius: 12px; padding: 20px;
                 box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .chart-card h3 {{ font-size: 15px; font-weight: 600; margin-bottom: 12px; }}
  .chart-card canvas {{ max-height: 280px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid var(--border); font-size: 14px; }}
  th {{ font-weight: 600; color: var(--muted); font-size: 13px; }}
  .suggestion {{ background: #EAF8F5; border-radius: 8px; padding: 12px 16px; margin-bottom: 8px;
                 font-size: 14px; border-left: 4px solid var(--accent); }}
  .roi-card {{ background: linear-gradient(135deg, var(--primary) 0%, #FF6B8A 100%); color: #fff;
               border-radius: 12px; padding: 24px; margin-top: 16px; font-size: 15px; line-height: 1.8; }}
  .gauge-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-top: 16px; }}
  .gauge-card {{ background: var(--card); border-radius: 12px; padding: 20px; text-align: center;
                 box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .gauge-card canvas {{ max-height: 160px; }}
  .gauge-card .gauge-label {{ font-size: 13px; color: var(--muted); margin-top: 8px; }}

  /* Card detail section */
  .filter-bar {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; margin-bottom: 16px; }}
  .filter-bar select {{ padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px;
                         font-size: 14px; background: var(--card); color: var(--text); cursor: pointer;
                         min-width: 140px; }}
  .filter-bar .filter-count {{ font-size: 13px; color: var(--muted); margin-left: auto; }}
  .defect-card {{ background: var(--card); border-radius: 12px; margin-bottom: 12px;
                   box-shadow: 0 1px 3px rgba(0,0,0,0.08); overflow: hidden; }}
  .defect-card-header {{ display: flex; align-items: flex-start; gap: 10px; padding: 16px 20px;
                          cursor: pointer; user-select: none; }}
  .defect-card-header:hover {{ background: rgba(0,0,0,0.015); }}
  .defect-card-header .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
                                 font-size: 12px; font-weight: 600; flex-shrink: 0; }}
  .badge-p0 {{ background: var(--danger); color: #fff; }}
  .badge-p1 {{ background: var(--warn); color: #fff; }}
  .badge-p2 {{ background: #94A3B8; color: #fff; }}
  .defect-card-header .platform-tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
                                        font-size: 12px; background: #EAF8F5; color: var(--accent);
                                        font-weight: 500; flex-shrink: 0; }}
  .defect-card-header .card-id {{ font-size: 12px; color: var(--muted); flex-shrink: 0; white-space: nowrap; }}
  .defect-card-header .card-summary {{ flex: 1; font-size: 14px; font-weight: 500; line-height: 1.5; }}
  .defect-card-header .toggle-icon {{ font-size: 18px; color: var(--muted); flex-shrink: 0;
                                       transition: transform 0.2s; }}
  .defect-card-header.open .toggle-icon {{ transform: rotate(180deg); }}
  .defect-card-body {{ display: none; padding: 0 20px 20px; border-top: 1px solid var(--border); }}
  .defect-card-body.open {{ display: block; }}
  .defect-field {{ margin-top: 16px; }}
  .defect-field-label {{ font-size: 12px; font-weight: 600; color: var(--primary); text-transform: uppercase;
                          letter-spacing: 0.5px; margin-bottom: 6px; }}
  .defect-field-content {{ font-size: 14px; line-height: 1.7; color: var(--text); white-space: pre-wrap; }}
  .defect-field-content ul {{ padding-left: 20px; margin: 4px 0; }}
  .defect-field-content li {{ margin-bottom: 4px; }}
  .signal-tags {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .signal-tag {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px;
                  background: var(--border); color: var(--text); }}
  .file-list {{ font-size: 13px; color: var(--muted); font-family: "SF Mono", Menlo, monospace; }}
  .quality-inline {{ display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }}
  .quality-inline .q-score {{ font-weight: 600; }}
  .quality-inline .q-pass {{ color: var(--success); }}
  .quality-inline .q-fail {{ color: var(--danger); }}
</style>
</head>
<body>
<div class="container">

<header>
  <h1>缺陷知识库报告</h1>
  <div class="meta">{d["project_name"]} | {d["period_label"]} | {d["now_str"]}</div>
</header>

<!-- Section 1: Overview Metric Cards -->
<div class="section-title">知识库概览</div>
<div class="metrics-grid">
  <div class="metric-card"><div class="value">{d["total_all"]}</div><div class="label">总卡片数</div></div>
  <div class="metric-card success"><div class="value">{d["pass_rate"]:.0f}%</div><div class="label">质量通过率</div><div class="sub">{d["passed_count"]}/{d["with_quality_count"]}</div></div>
  <div class="metric-card"><div class="value">{d["override_rate"]:.0f}%</div><div class="label">覆写率</div><div class="sub">{d["overridden_count"]}/{d["with_quality_count"]}</div></div>
  <div class="metric-card accent"><div class="value">{d["overall_avg"]:.1f}</div><div class="label">质量均分</div><div class="sub">满分 5.0</div></div>
</div>

<!-- Section 2: Distribution Charts -->
<div class="section-title">卡片分布</div>
<div class="charts-grid">
  <div class="chart-card"><h3>按平台</h3><canvas id="chartPlatform"></canvas></div>
  <div class="chart-card"><h3>按严重度</h3><canvas id="chartSeverity"></canvas></div>
  <div class="chart-card"><h3>按来源</h3><canvas id="chartSource"></canvas></div>
  <div class="chart-card"><h3>按模块</h3><canvas id="chartModule"></canvas></div>
</div>

<!-- Section 3: Quality Radar + Trend -->
<div class="section-title">质量评分分析</div>
<div class="charts-grid">
  <div class="chart-card"><h3>6 维度雷达图</h3><canvas id="chartRadar"></canvas></div>
  <div class="chart-card"><h3>月度质量趋势</h3><canvas id="chartQualityTrend"></canvas></div>
</div>

<!-- Section 4 & 5: Search & Govern -->
<div class="section-title">检索与沉淀</div>
<div class="metrics-grid">
  <div class="metric-card"><div class="value">{d["search_total"]}</div><div class="label">检索次数</div></div>
  <div class="metric-card"><div class="value">{d["search_hit_rate"]:.0f}%</div><div class="label">检索命中率</div></div>
  <div class="metric-card"><div class="value">{d["govern_total"]}</div><div class="label">沉淀次数</div></div>
  <div class="metric-card"><div class="value">{d["reject_rate"]:.0f}%</div><div class="label">门禁拒绝率</div></div>
</div>
<div class="charts-grid">
  <div class="chart-card"><h3>月度检索量</h3><canvas id="chartSearchTrend"></canvas></div>
  <div class="chart-card"><h3>月度沉淀量</h3><canvas id="chartGovernTrend"></canvas></div>
</div>

<!-- Section 6: Coverage Gaps -->
<div class="section-title">覆盖缺口</div>
<div class="chart-card">
  {"<table><tr><th>名称</th><th>类型</th></tr>" + gap_rows + "</table>" if gap_rows else "<p style='color:var(--muted);padding:12px'>暂无明显覆盖缺口</p>"}
</div>

<!-- Section 7: Suggestions -->
<div class="section-title">建议</div>
{suggestion_html}

<!-- Section 8: KB Value -->
<div class="section-title">知识库价值</div>
<div class="gauge-row">
  <div class="gauge-card"><canvas id="gaugeApply"></canvas><div class="gauge-label">检索应用率</div></div>
  <div class="gauge-card"><canvas id="gaugeFixRef"></canvas><div class="gauge-label">修复参考率</div></div>
  <div class="gauge-card"><canvas id="gaugeReduction"></canvas><div class="gauge-label">假设减少率</div></div>
</div>
<div class="charts-grid" style="margin-top:16px">
  <div class="chart-card"><h3>排查假设数对比（有 KB vs 无 KB）</h3><canvas id="chartHypotheses"></canvas></div>
  <div class="chart-card">
    <h3>价值明细</h3>
    <table>
      <tr><th>指标</th><th>值</th></tr>
      <tr><td>检索反馈事件</td><td>{d["outcome_total"]}</td></tr>
      <tr><td>结果被应用</td><td>{d["outcome_applied"]}</td></tr>
      <tr><td>结果被查看</td><td>{d["outcome_viewed"]}</td></tr>
      <tr><td>修复会话总数</td><td>{d["fix_total"]}</td></tr>
      <tr><td>使用 KB 的修复</td><td>{d["fix_with_kb_count"]}</td></tr>
      <tr><td>置信度升级数</td><td>{d["conf_upgrades"]}</td></tr>
    </table>
  </div>
</div>

<!-- Section 9: ROI -->
<div class="section-title">ROI 摘要</div>
<div class="roi-card">{roi_text}</div>

<!-- Section 10: Card Knowledge Detail -->
<div class="section-title">卡片知识详情</div>
<div class="filter-bar">
  <select id="filterPlatform" onchange="filterCards()">
    <option value="">全部平台</option>
  </select>
  <select id="filterModule" onchange="filterCards()">
    <option value="">全部模块</option>
  </select>
  <select id="filterSeverity" onchange="filterCards()">
    <option value="">全部严重度</option>
    <option value="P0">P0</option>
    <option value="P1">P1</option>
    <option value="P2">P2</option>
  </select>
  <span class="filter-count" id="cardCount"></span>
</div>
<div id="cardList"></div>

</div><!-- .container -->

<script>
const PRIMARY = '#FF3D60';
const ACCENT = '#00C5CA';
const SEV_COLORS = ['#EF4444', '#F59E0B', '#94A3B8'];
const PALETTE = [PRIMARY, ACCENT, '#6366F1', '#F59E0B', '#22C55E', '#8B5CF6', '#EC4899', '#14B8A6', '#F97316'];

function barChart(id, labels, data, horizontal) {{
  new Chart(document.getElementById(id), {{
    type: 'bar',
    data: {{ labels: labels, datasets: [{{ data: data, backgroundColor: PALETTE.slice(0, data.length), borderRadius: 6 }}] }},
    options: {{ indexAxis: horizontal ? 'y' : 'x', plugins: {{ legend: {{ display: false }} }},
               scales: {{ x: {{ beginAtZero: true }}, y: {{ beginAtZero: true }} }} }}
  }});
}}

barChart('chartPlatform', {platform_labels}, {platform_values}, true);
barChart('chartSource', {source_labels}, {source_values}, true);
barChart('chartModule', {module_labels}, {module_values}, true);

new Chart(document.getElementById('chartSeverity'), {{
  type: 'doughnut',
  data: {{ labels: ['P0', 'P1', 'P2'], datasets: [{{ data: {severity_values}, backgroundColor: SEV_COLORS }}] }},
  options: {{ plugins: {{ legend: {{ position: 'bottom' }} }} }}
}});

new Chart(document.getElementById('chartRadar'), {{
  type: 'radar',
  data: {{ labels: {radar_labels},
           datasets: [{{ label: '平均分', data: {radar_values}, fill: true,
                        backgroundColor: 'rgba(255,61,96,0.15)', borderColor: PRIMARY, pointBackgroundColor: PRIMARY }}] }},
  options: {{ scales: {{ r: {{ min: 0, max: 5, ticks: {{ stepSize: 1 }} }} }},
             plugins: {{ legend: {{ display: false }} }} }}
}});

new Chart(document.getElementById('chartQualityTrend'), {{
  type: 'line',
  data: {{ labels: {mq_labels}, datasets: [{{ label: '月度均分', data: {mq_values}, borderColor: PRIMARY,
           backgroundColor: 'rgba(255,61,96,0.1)', fill: true, tension: 0.3 }}] }},
  options: {{ scales: {{ y: {{ min: 0, max: 5 }} }}, plugins: {{ legend: {{ display: false }} }} }}
}});

barChart('chartSearchTrend', {ms_labels}, {ms_values}, false);
barChart('chartGovernTrend', {mg_labels}, {mg_values}, false);

function gaugeChart(id, value, color) {{
  const capped = Math.min(Math.max(value, 0), 100);
  new Chart(document.getElementById(id), {{
    type: 'doughnut',
    data: {{ datasets: [{{ data: [capped, 100 - capped], backgroundColor: [color, '#EEEEEE'], borderWidth: 0 }}] }},
    options: {{ cutout: '75%', rotation: -90, circumference: 180, plugins: {{
      legend: {{ display: false }},
      tooltip: {{ enabled: false }}
    }} }}
  }});
  const ctx = document.getElementById(id).getContext('2d');
  const cx = ctx.canvas.width / 2, cy = ctx.canvas.height * 0.62;
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.font = 'bold 24px -apple-system, sans-serif'; ctx.fillStyle = color;
  ctx.fillText(value.toFixed(0) + '%', cx, cy);
}}

gaugeChart('gaugeApply', {d["search_apply_rate"]:.1f}, PRIMARY);
gaugeChart('gaugeFixRef', {d["fix_ref_rate"]:.1f}, ACCENT);
gaugeChart('gaugeReduction', {d["hyp_reduction"]:.1f}, '#22C55E');

new Chart(document.getElementById('chartHypotheses'), {{
  type: 'bar',
  data: {{ labels: ['有 KB', '无 KB'],
           datasets: [{{ label: '平均假设数', data: [{d["avg_hyp_with"]:.1f}, {d["avg_hyp_without"]:.1f}],
                        backgroundColor: [ACCENT, '#94A3B8'], borderRadius: 6 }}] }},
  options: {{ plugins: {{ legend: {{ display: false }} }}, scales: {{ y: {{ beginAtZero: true }} }} }}
}});

/* --- Card Knowledge Detail --- */
const CARDS = {cards_json};

(function initCardSection() {{
  const platforms = [...new Set(CARDS.map(c => c.metadata.platform))].sort();
  const modules = [...new Set(CARDS.map(c => c.metadata.module || 'unknown'))].sort();
  const selPlatform = document.getElementById('filterPlatform');
  const selModule = document.getElementById('filterModule');
  platforms.forEach(p => {{ const o = document.createElement('option'); o.value = p; o.textContent = p; selPlatform.appendChild(o); }});
  modules.forEach(m => {{ const o = document.createElement('option'); o.value = m; o.textContent = m; selModule.appendChild(o); }});
  renderCards(CARDS);
}})();

function esc(s) {{ const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }}

function renderCards(cards) {{
  const container = document.getElementById('cardList');
  const countEl = document.getElementById('cardCount');
  countEl.textContent = cards.length + ' / ' + CARDS.length + ' 张卡片';
  if (cards.length === 0) {{
    container.innerHTML = '<div style="text-align:center;color:var(--muted);padding:40px 0;">无匹配卡片</div>';
    return;
  }}
  container.innerHTML = cards.map((c, i) => {{
    const m = c.metadata;
    const idx = c.index_layer;
    const r = c.resolution_layer;
    const q = m.quality;
    const sevClass = m.severity === 'P0' ? 'badge-p0' : m.severity === 'P1' ? 'badge-p1' : 'badge-p2';
    const signalHtml = (idx.signals || []).map(s => '<span class="signal-tag">' + esc(s) + '</span>').join('');
    const filesHtml = (m.related_files || []).length > 0
      ? m.related_files.map(f => '<div>' + esc(f) + '</div>').join('')
      : '<span style="color:var(--muted)">无</span>';
    const abandonedHtml = (r.abandoned_approaches || []).length > 0
      ? '<ul>' + r.abandoned_approaches.map(a => '<li>' + esc(a) + '</li>').join('') + '</ul>'
      : '<span style="color:var(--muted)">无</span>';
    const qHtml = q
      ? '<span class="quality-inline"><span class="q-score">' + q.average.toFixed(1) + '</span>/5.0 '
        + (q.passed ? '<span class="q-pass">通过</span>' : '<span class="q-fail">未通过</span>') + '</span>'
      : '<span style="color:var(--muted)">未评分</span>';
    const refsHtml = (m.github_refs || []).concat(m.issue_refs || []);
    const refsStr = refsHtml.length > 0 ? refsHtml.map(r => esc(r)).join(', ') : '';
    return '<div class="defect-card" data-platform="' + esc(m.platform) + '" data-module="' + esc(m.module || 'unknown') + '" data-severity="' + esc(m.severity) + '">'
      + '<div class="defect-card-header" onclick="toggleCard(' + i + ')" id="cardHeader' + i + '">'
      + '<span class="badge ' + sevClass + '">' + esc(m.severity) + '</span>'
      + '<span class="platform-tag">' + esc(m.platform) + '</span>'
      + '<span class="card-id">' + esc(m.id) + '</span>'
      + '<span class="card-summary">' + esc(idx.problem_summary) + '</span>'
      + '<span class="toggle-icon">\u25BC</span>'
      + '</div>'
      + '<div class="defect-card-body" id="cardBody' + i + '">'
      + '<div class="defect-field"><div class="defect-field-label">\u6839\u56e0\u5206\u6790</div><div class="defect-field-content">' + esc(r.root_cause) + '</div></div>'
      + '<div class="defect-field"><div class="defect-field-label">\u4fee\u590d\u7b56\u7565</div><div class="defect-field-content">' + esc(r.fix_strategy) + '</div></div>'
      + '<div class="defect-field"><div class="defect-field-label">\u8865\u4e01\u6458\u8981</div><div class="defect-field-content">' + esc(r.patch_digest) + '</div></div>'
      + '<div class="defect-field"><div class="defect-field-label">\u9a8c\u8bc1\u8ba1\u5212</div><div class="defect-field-content">' + esc(r.verification_plan) + '</div></div>'
      + '<div class="defect-field"><div class="defect-field-label">\u5df2\u653e\u5f03\u65b9\u6848</div><div class="defect-field-content">' + abandonedHtml + '</div></div>'
      + '<div class="defect-field"><div class="defect-field-label">\u4fe1\u53f7\u8bcd</div><div class="defect-field-content"><div class="signal-tags">' + signalHtml + '</div></div></div>'
      + '<div class="defect-field"><div class="defect-field-label">\u5173\u8054\u6587\u4ef6</div><div class="defect-field-content file-list">' + filesHtml + '</div></div>'
      + (refsStr ? '<div class="defect-field"><div class="defect-field-label">\u5173\u8054 Issue</div><div class="defect-field-content">' + refsStr + '</div></div>' : '')
      + '<div class="defect-field"><div class="defect-field-label">\u8d28\u91cf\u8bc4\u5206</div><div class="defect-field-content">' + qHtml + '</div></div>'
      + '<div class="defect-field" style="display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--muted)">'
      + '<span>\u6765\u6e90: ' + esc(m.source) + '</span>'
      + '<span>\u65e5\u671f: ' + esc(m.date) + '</span>'
      + (m.confidence ? '<span>\u7f6e\u4fe1\u5ea6: ' + esc(m.confidence) + '</span>' : '')
      + '</div>'
      + '</div></div>';
  }}).join('');
}}

function toggleCard(i) {{
  const header = document.getElementById('cardHeader' + i);
  const body = document.getElementById('cardBody' + i);
  const isOpen = body.classList.contains('open');
  body.classList.toggle('open');
  header.classList.toggle('open');
}}

function filterCards() {{
  const platform = document.getElementById('filterPlatform').value;
  const module = document.getElementById('filterModule').value;
  const severity = document.getElementById('filterSeverity').value;
  const filtered = CARDS.filter(c => {{
    if (platform && c.metadata.platform !== platform) return false;
    if (module && (c.metadata.module || 'unknown') !== module) return false;
    if (severity && c.metadata.severity !== severity) return false;
    return true;
  }});
  renderCards(filtered);
}}
</script>
</body>
</html>'''


def cmd_report(args: argparse.Namespace) -> None:
    """Generate a quality report (Markdown or HTML)."""
    cfg = load_config(args.project_root)
    root = Path(cfg["_root"])
    period = getattr(args, "period", "all")
    fmt = getattr(args, "format", "md")

    d = _aggregate_report_data(cfg, period)

    if fmt == "html":
        chartjs_inline = _get_chartjs_inline(cfg)
        report_text = _build_html_report(d, chartjs_inline)
        ext = ".html"
    else:
        report_text = _build_md_report(d)
        ext = ".md"

    if args.output:
        report_path = Path(args.output)
    else:
        reports_dir = root / "defect-kb-data" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"report-{datetime.now().strftime('%Y%m%d')}{ext}"

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")
    print(f"Report generated: {report_path}")

    if fmt == "html" and not getattr(args, "no_open", False):
        url = report_path.resolve().as_uri()
        webbrowser.open(url)
        print(f"Opened in browser: {url}")


# ---------------------------------------------------------------------------
# quick / upgrade — fast capture path with optional later promotion
# ---------------------------------------------------------------------------

def _rewrite_cards_jsonl(cards_path: Path, cards: list[dict]) -> None:
    """Atomically rewrite cards.jsonl from a list of card dicts. Preserves order."""
    tmp = cards_path.with_suffix(cards_path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for c in cards:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    os.replace(tmp, cards_path)


def _local_quick_card(raw_text: str, severity: str) -> dict:
    """Build a minimal-valid Experience Card from a one-liner WITHOUT calling any LLM.

    Used when LLM is unavailable, --no-llm is passed, or as a fallback. The card is
    schema-valid (signals >= 5) but resolution layers contain TODO placeholders, since
    the user explicitly chose speed over depth.
    """
    import re as _qre

    summary = raw_text.strip().splitlines()[0][:200] if raw_text.strip() else "Quick note (TODO)"
    tokens = [
        t for t in _qre.split(r"[\s,;./\-_|()（）、，。：:`'\"]+", raw_text.lower())
        if len(t) > 2
    ]
    seen: set[str] = set()
    keywords: list[str] = []
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        keywords.append(t)
        if len(keywords) >= 8:
            break
    i = 0
    while len(keywords) < 5:
        keywords.append(f"todo-{i}")
        i += 1

    return {
        "index_layer": {
            "problem_summary": summary,
            "signals": keywords,
        },
        "resolution_layer": {
            "root_cause": "TODO: investigate",
            "fix_strategy": "TODO: design fix",
            "patch_digest": "TODO: code change summary",
            "verification_plan": "TODO: how to verify",
            "abandoned_approaches": [],
        },
        "metadata": {
            "severity": severity,
            "related_files": [],
            "github_refs": [],
        },
    }


def cmd_quick(args: argparse.Namespace) -> None:
    """5-second capture: turn a one-liner into a minimal-valid card.

    Skips the 6-dimension quality gate. Marks metadata.quick=true so the card can be
    found later via 'cli.py upgrade --id <ID>' for proper standardization.
    """
    cfg = load_config(args.project_root)
    cards_path = resolve_path(cfg, "data.cards_path")
    output_fmt = getattr(args, "output_format", "text")
    today = datetime.now().strftime("%Y%m%d")

    raw_text = args.note
    platform = args.platform or "unknown"
    module = args.module or "unknown"
    severity = args.severity or "P2"

    if args.json_input:
        try:
            card_data = parse_llm_json(args.json_input)
        except LLMParseError as e:
            _output({"status": "error", "message": str(e)}, output_fmt)
            sys.exit(1)
    elif args.no_llm:
        card_data = _local_quick_card(raw_text, severity)
    else:
        try:
            tmpl = _load_prompt("standardize_quick.txt")
            prompt = tmpl.format(
                project_name=cfg["project"]["name"],
                platform=platform, module=module, raw_text=raw_text,
            )
            raw = call_llm(cfg, prompt)
            card_data = parse_llm_json(raw)
        except Exception as e:
            if output_fmt == "text":
                print(f"LLM unavailable ({type(e).__name__}: {e}); falling back to local minimal card.")
            card_data = _local_quick_card(raw_text, severity)

    card_id = _next_id(cards_path, today)
    md = card_data.setdefault("metadata", {})
    md.update({
        "id": card_id,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "project": cfg["project"]["name"],
        "platform": platform,
        "module": module,
        "source": md.get("source", "manual"),
        "severity": md.get("severity", severity),
        "quick": True,
        "confidence": md.get("confidence", "hypothesis"),
    })

    try:
        card = ExperienceCard(**card_data)
    except Exception as e:
        _output({"status": "error", "message": f"Card validation failed: {e}"}, output_fmt)
        sys.exit(1)

    _write_card(cards_path, card)
    _log_event(cfg, {
        "action": "quick", "card_id": card_id,
        "platform": platform, "module": module, "severity": severity,
    })
    index_md_path = _regenerate_index_md(cfg)

    if output_fmt == "json":
        _output({
            "status": "written",
            "card_id": card_id,
            "card": card.model_dump(),
            "index_md": str(index_md_path) if index_md_path else None,
        }, output_fmt)
    else:
        print(f"Quick note saved as {card_id} (severity={severity}, platform={platform}).")
        print(f"  Summary: {card.index_layer.problem_summary}")
        print(f"  → Run 'cli.py upgrade --id {card_id}' later to flesh out and pass quality gate.")
        if index_md_path:
            print(f"  INDEX.md regenerated: {index_md_path}")


def cmd_upgrade(args: argparse.Namespace) -> None:
    """Promote a 'quick' card by re-running standardize + quality gate.

    Three input modes (mirrors govern):
      • default: use the existing card body as raw_text, run LLM standardize
      • --input <text>: replace raw_text with new content, run LLM standardize
      • --json <json>:  Agent already pre-standardized the card, skip LLM call

    Preserves id / date / project / usage_count / last_hit_at; flips quick=false and
    stamps upgraded_at. Quality gate runs unless --skip-quality.
    """
    cfg = load_config(args.project_root)
    cards_path = resolve_path(cfg, "data.cards_path")
    output_fmt = getattr(args, "output_format", "text")

    if not cards_path.exists():
        _output({"status": "error", "message": "No cards.jsonl found."}, output_fmt)
        sys.exit(1)

    cards: list[dict] = []
    target_idx = -1
    for line in cards_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        if c.get("metadata", {}).get("id") == args.id:
            target_idx = len(cards)
        cards.append(c)

    if target_idx < 0:
        _output({"status": "error", "message": f"Card {args.id} not found."}, output_fmt)
        sys.exit(1)

    target = cards[target_idx]
    if not target["metadata"].get("quick") and output_fmt == "text":
        print(f"Note: {args.id} is not marked quick — will be re-standardized anyway.")

    if args.json_input:
        try:
            new_data = parse_llm_json(args.json_input)
        except LLMParseError as e:
            _output({"status": "error", "message": str(e)}, output_fmt)
            sys.exit(1)
        ran_llm = False
    else:
        if args.input:
            raw_text = args.input
        else:
            raw_text = (
                f"{target['index_layer']['problem_summary']}\n"
                f"signals: {', '.join(target['index_layer'].get('signals', []))}\n"
                f"root_cause: {target['resolution_layer'].get('root_cause', '')}\n"
                f"fix_strategy: {target['resolution_layer'].get('fix_strategy', '')}\n"
                f"patch_digest: {target['resolution_layer'].get('patch_digest', '')}\n"
                f"verification: {target['resolution_layer'].get('verification_plan', '')}\n"
            )
        platform = target["metadata"].get("platform", "unknown")
        module = target["metadata"].get("module", "unknown")
        tmpl = _load_prompt("standardize.txt")
        prompt = tmpl.format(
            project_name=cfg["project"]["name"],
            platform=platform, module=module, raw_text=raw_text,
        )
        try:
            raw = call_llm(cfg, prompt)
            new_data = parse_llm_json(raw)
            ran_llm = True
        except Exception as e:
            _output({
                "status": "error",
                "message": f"LLM unavailable ({type(e).__name__}: {e}). "
                           f"Use --json to upgrade manually with Agent-pre-standardized content.",
            }, output_fmt)
            sys.exit(1)

    quality_score = None
    if not args.skip_quality:
        try:
            qc_data = _run_quality_check(cfg, new_data)
            quality_score = _build_quality_score(qc_data, override=args.force)
            if not quality_score.passed and not args.force:
                if output_fmt == "json":
                    _output({
                        "status": "rejected",
                        "card_id": args.id,
                        "quality": quality_score.model_dump(),
                        "message": "Quality gate failed. Use --force to override.",
                    }, output_fmt)
                else:
                    print(f"Quality gate FAILED for {args.id} (avg {quality_score.average:.1f}). "
                          f"Use --force or --skip-quality.")
                    _print_quality_report(quality_score)
                sys.exit(2)
        except Exception as e:
            if output_fmt == "text":
                print(f"WARNING: quality check skipped due to error: {e}")

    preserved = dict(target["metadata"])
    incoming_meta = new_data.get("metadata") or {}
    for safe_key in ("severity", "defect_category", "framework_info",
                     "related_files", "github_refs", "issue_refs"):
        if safe_key in incoming_meta and incoming_meta[safe_key] not in (None, "", []):
            preserved[safe_key] = incoming_meta[safe_key]
    preserved["quick"] = False
    preserved["upgraded_at"] = datetime.now().isoformat(timespec="seconds")
    if quality_score:
        if not quality_score.passed and args.force:
            quality_score.quality_override = True
        preserved["quality"] = quality_score.model_dump()
    new_data["metadata"] = preserved

    try:
        card = ExperienceCard(**new_data)
    except Exception as e:
        _output({"status": "error", "message": f"Validation failed: {e}"}, output_fmt)
        sys.exit(1)

    cards[target_idx] = card.model_dump()
    _rewrite_cards_jsonl(cards_path, cards)
    _log_event(cfg, {
        "action": "upgrade",
        "card_id": args.id,
        "ran_llm": ran_llm,
        "quality_avg": round(quality_score.average, 2) if quality_score else None,
        "quality_passed": quality_score.passed if quality_score else None,
    })
    index_md_path = _regenerate_index_md(cfg)

    if output_fmt == "json":
        _output({
            "status": "upgraded",
            "card_id": args.id,
            "card": card.model_dump(),
            "quality": quality_score.model_dump() if quality_score else None,
            "index_md": str(index_md_path) if index_md_path else None,
        }, output_fmt)
    else:
        print(f"Upgraded {args.id} (quick=false, upgraded_at stamped).")
        if quality_score:
            print(f"  Quality: {quality_score.average:.1f} / 5.0 "
                  f"({'PASSED' if quality_score.passed else 'OVERRIDDEN' if args.force else 'FAILED'})")
        if index_md_path:
            print(f"  INDEX.md regenerated: {index_md_path}")


# ---------------------------------------------------------------------------
# log-event
# ---------------------------------------------------------------------------

def cmd_log_event(args: argparse.Namespace) -> None:
    """Log a custom event (search_outcome / fix_session / confidence_change) to events.jsonl."""
    cfg = load_config(args.project_root)
    event = json.loads(args.data)
    event["action"] = args.action_type
    _log_event(cfg, event)
    print(f"Event logged: {args.action_type}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="defect-kb",
        description="Project-agnostic defect knowledge base CLI",
    )
    parser.add_argument("--project-root", help="Project root (default: auto-detect)")
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init", help="Initialize defect-kb.yaml for a project")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing config")
    p_init.add_argument("--template", choices=list(_PROJECT_TEMPLATES.keys()),
                        help="Use a project type template (default: auto-detected)")
    p_init.add_argument("--install-skills", action="store_true",
                        help="Copy skill files into the project .cursor/skills/")
    p_init.add_argument("--no-preview", action="store_true",
                        help="Skip preview, write defect-kb.yaml directly")
    p_init.add_argument("--confirm", action="store_true",
                        help="Confirm and apply the preview file (defect-kb-init-preview.md)")
    p_init.add_argument(
        "--import-seeds", nargs="?", const="", default=None, metavar="PLATFORMS",
        help=(
            "Import bundled seed cards. "
            "Pass 'all' for every seed, or 'ios,backend' for specific platforms; "
            "omit value to auto-select based on detected project platforms. "
            "'common' platform is always included. Mutually exclusive with --skip-seeds."
        ),
    )
    p_init.add_argument(
        "--skip-seeds", action="store_true",
        help="Explicitly skip seed-card import (overrides --import-seeds).",
    )

    # govern
    p_gov = sub.add_parser("govern", help="Standardize raw text into an Experience Card")
    p_gov_input = p_gov.add_mutually_exclusive_group(required=True)
    p_gov_input.add_argument("--input", help="Raw defect text (uses LLM to standardize)")
    p_gov_input.add_argument("--json", dest="json_input",
                             help="Pre-standardized card JSON (skips LLM standardization)")
    p_gov.add_argument("--platform", default="", help="Platform (e.g. ios, backend)")
    p_gov.add_argument("--module", default="", help="Module name")
    p_gov.add_argument("--source", default="manual",
                       choices=["pitfalls", "github-issue", "yunxiao-issue", "gitlab-issue",
                                "git-history", "code-comment",
                                "agent-transcript", "manual", "ai-proactive"])
    p_gov.add_argument("--confidence", choices=["confirmed", "likely", "hypothesis"],
                       help="Confidence level (for ai-proactive source)")
    p_gov.add_argument("--discovery-method",
                       choices=["code-review", "business-rule-audit", "brainstorm-edge-case"],
                       help="Discovery method (for ai-proactive source)")
    p_gov.add_argument("--force", action="store_true",
                       help="Write card even if quality check fails (marks quality_override)")
    p_gov.add_argument("--auto-retry", action="store_true",
                       help=f"Auto-improve and retry on quality failure (max {_MAX_RETRY} retries)")
    p_gov.add_argument("--quality-json", dest="quality_json",
                       help="Pre-evaluated quality score JSON (skips internal LLM quality check)")
    p_gov.add_argument("--skip-quality", action="store_true",
                       help="Skip quality check entirely")
    p_gov.add_argument("--output-format", choices=["text", "json"], default="text",
                       help="Output format (default: text)")

    # index
    p_index = sub.add_parser("index", help="Index un-indexed cards into ChromaDB")
    p_index.add_argument("--embedding-provider", choices=["openai", "local"],
                         help="Embedding provider (default: from config or local)")
    p_index.add_argument("--rebuild-md", action="store_true",
                         help="Rebuild defect-kb-data/INDEX.md from cards.jsonl and exit")
    p_index.add_argument("--output-format", choices=["text", "json"], default="text")

    # search
    p_search = sub.add_parser("search", help="Semantic search the knowledge base")
    p_search.add_argument("--query", required=True, help="Search query")
    p_search.add_argument("--platform", help="Filter by platform")
    p_search.add_argument("--top-k", type=int, default=5, help="Number of results")
    p_search.add_argument("--embedding-provider", choices=["openai", "local"],
                          help="Embedding provider (default: from config or local)")
    p_search.add_argument("--min-similarity", type=float, default=0.0,
                          help="Minimum similarity threshold (filter out low relevance)")
    p_search.add_argument("--rerank", action="store_true",
                          help="Enable cross-encoder reranking for better precision")
    p_search.add_argument("--hybrid", action="store_true",
                          help="Enable hybrid search (keyword + semantic)")
    p_search.add_argument("--no-record", action="store_true",
                          help="Do not increment usage_count or write search_hit events (read-only mode)")
    p_search.add_argument("--output-format", choices=["text", "json", "compact"], default="text")

    # browse
    p_browse = sub.add_parser("browse", help="View a card by ID")
    p_browse.add_argument("--id", required=True, help="Card ID (e.g. DEF-20260318-001)")
    p_browse.add_argument("--output-format", choices=["text", "json"], default="text")

    # stats
    p_stats = sub.add_parser("stats", help="Show quality statistics across all cards")
    p_stats.add_argument("--include-seeds", action="store_true",
                         help="Include bundled seed cards (metadata.seed=true) in the dashboard")
    p_stats.add_argument("--include-quick", action="store_true",
                         help="Include unupgraded quick cards (metadata.quick=true) in the dashboard")
    p_stats.add_argument("--output-format", choices=["text", "json"], default="text")

    # report
    p_report = sub.add_parser("report", help="Generate a quality report (Markdown or HTML)")
    p_report.add_argument("--period", choices=["all", "month", "quarter"], default="all",
                          help="Report period: all (default), month (30d), quarter (90d)")
    p_report.add_argument("--format", choices=["md", "html"], default="md",
                          help="Output format: md (default Markdown), html (visual dashboard)")
    p_report.add_argument("--output", help="Output file path (default: auto-generated)")
    p_report.add_argument("--no-open", action="store_true",
                          help="Do not auto-open HTML report in browser")

    # quick — 5-second capture, no quality gate
    p_quick = sub.add_parser(
        "quick",
        help="5-second capture: turn a one-liner into a minimal-valid card (skips quality gate)",
    )
    p_quick.add_argument("note", help="One-liner about the bug/pitfall (positional)")
    p_quick.add_argument("--platform", default="", help="Platform (e.g. ios, backend)")
    p_quick.add_argument("--module", default="", help="Module name")
    p_quick.add_argument("--severity", choices=["P0", "P1", "P2"], default="P2",
                         help="Severity (default: P2 — quick notes are usually low-priority placeholders)")
    p_quick.add_argument("--json", dest="json_input",
                         help="Pre-standardized card JSON (skips LLM, Agent path)")
    p_quick.add_argument("--no-llm", action="store_true",
                         help="Skip LLM entirely; build a local minimal card with TODO placeholders")
    p_quick.add_argument("--output-format", choices=["text", "json"], default="text")

    # upgrade — promote a quick card to a full card via standardize + quality gate
    p_upgrade = sub.add_parser(
        "upgrade",
        help="Promote a 'quick' card to a full card (re-standardize + quality gate)",
    )
    p_upgrade.add_argument("--id", required=True, help="Card ID to upgrade (e.g. DEF-20260318-001)")
    p_upgrade.add_argument("--input", help="New raw text describing the defect (overrides existing card body)")
    p_upgrade.add_argument("--json", dest="json_input",
                           help="Agent-pre-standardized JSON (skips LLM call)")
    p_upgrade.add_argument("--force", action="store_true",
                           help="Write upgraded card even if quality gate fails (marks quality_override)")
    p_upgrade.add_argument("--skip-quality", action="store_true",
                           help="Skip the 6-dimension quality gate")
    p_upgrade.add_argument("--output-format", choices=["text", "json"], default="text")

    # log-event
    p_log = sub.add_parser("log-event", help="Log a custom event to events.jsonl")
    p_log.add_argument("--action-type", required=True,
                       choices=["search_outcome", "fix_session", "confidence_change"],
                       help="Event type")
    p_log.add_argument("--data", required=True, help="JSON event payload")

    args = parser.parse_args()
    cmd_map = {
        "init": cmd_init,
        "govern": cmd_govern,
        "index": cmd_index,
        "search": cmd_search,
        "browse": cmd_browse,
        "stats": cmd_stats,
        "report": cmd_report,
        "quick": cmd_quick,
        "upgrade": cmd_upgrade,
        "log-event": cmd_log_event,
    }
    cmd_map[args.command](args)


if __name__ == "__main__":
    main()

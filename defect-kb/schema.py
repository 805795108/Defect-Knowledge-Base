"""Experience Card schema — project-agnostic Pydantic models."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class IndexLayer(BaseModel):
    problem_summary: str = Field(
        ..., description="Generalized problem description without repo-specific names"
    )
    signals: list[str] = Field(
        ...,
        min_length=5,
        max_length=12,
        description="High-signal keywords covering: error type, symptom, trigger condition, affected component",
    )


class ResolutionLayer(BaseModel):
    root_cause: str
    fix_strategy: str = Field(
        ..., description="Abstract fix approach, decoupled from specific code paths"
    )
    patch_digest: str = Field(
        ..., description="Key code change summary"
    )
    verification_plan: str = Field(
        ..., description="Actionable steps to verify the fix"
    )
    abandoned_approaches: list[str] = Field(
        default_factory=list,
        description="Approaches tried and rejected, with reasons",
    )


class QualityScore(BaseModel):
    """6-dimension quality assessment persisted with each card."""

    signal_clarity: float = Field(..., ge=1, le=5)
    root_cause_depth: float = Field(..., ge=1, le=5)
    fix_portability: float = Field(..., ge=1, le=5)
    patch_digest_quality: float = Field(..., ge=1, le=5)
    verification_plan: float = Field(..., ge=1, le=5)
    infosec: float = Field(..., ge=1, le=5)
    average: float = Field(..., ge=1, le=5)
    passed: bool
    quality_override: bool = Field(
        default=False, description="True when user force-writes despite failing quality gate"
    )
    issues: list[str] = Field(
        default_factory=list, description="Specific improvement suggestions from quality check"
    )


class Metadata(BaseModel):
    id: str = Field(..., pattern=r"^DEF-\d{8}-\d{3}$")
    date: str
    project: str
    platform: str
    module: str
    source: Literal[
        "pitfalls",
        "github-issue",
        "yunxiao-issue",
        "gitlab-issue",
        "git-history",
        "code-comment",
        "agent-transcript",
        "manual",
        "ai-proactive",
        "static-analysis",
    ]
    severity: Literal["P0", "P1", "P2"]
    related_files: list[str] = Field(default_factory=list)
    github_refs: list[str] = Field(default_factory=list)
    issue_refs: list[str] = Field(
        default_factory=list,
        description="Platform-agnostic issue/workitem references (URLs or IDs)",
    )
    confidence: Optional[Literal["confirmed", "likely", "hypothesis"]] = None
    discovery_method: Optional[
        Literal[
            "code-review",
            "business-rule-audit",
            "brainstorm-edge-case",
            "pmd",
            "checkstyle",
            "spotbugs",
            "swiftlint",
            "eslint",
            "ktlint",
        ]
    ] = None
    defect_category: Optional[
        Literal[
            "ai-hallucination",
            "ai-antipattern",
            "ai-security",
            "ai-edge-case",
            "framework-pitfall",
            "framework-deprecation",
            "team-pattern",
        ]
    ] = None
    framework_info: Optional[dict] = Field(
        default=None,
        description="Framework/library context: {name, version_constraint, deprecated_api}",
    )
    quality: Optional[QualityScore] = Field(
        default=None, description="Quality gate assessment scores"
    )
    usage_count: int = Field(
        default=0,
        ge=0,
        description="Times this card was returned in search top-k results (auto-maintained)",
    )
    last_hit_at: Optional[str] = Field(
        default=None,
        description="ISO timestamp of the most recent search hit (auto-maintained)",
    )
    seed: bool = Field(
        default=False,
        description="True for seed cards bundled with the skill (imported via init --import-seeds). "
        "Surfaced separately in INDEX.md / stats dashboard so they don't dilute project-specific signal.",
    )
    quick: bool = Field(
        default=False,
        description="True for cards captured via 'cli.py quick' — minimal one-liner notes that "
        "skip the 6-dimension quality gate. Can be promoted via 'cli.py upgrade --id'.",
    )
    upgraded_at: Optional[str] = Field(
        default=None,
        description="ISO timestamp of when a 'quick' card was promoted to a full card. "
        "When set, 'quick' remains true (history-preserving) but the card is treated as full-grade.",
    )


class ExperienceCard(BaseModel):
    index_layer: IndexLayer
    resolution_layer: ResolutionLayer
    metadata: Metadata

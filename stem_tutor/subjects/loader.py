from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class TaxonomyEntryModel(BaseModel):
    category: str
    short_desc: str
    cues: list[str] = Field(default_factory=list)


class PromptTemplatesModel(BaseModel):
    system_role: str = "你是一个精确的{subject_name}辅导 JSON API。所有输出请使用中文（简体中文）。数学表达式请用 $...$ 包裹行内公式，用 $$...$$ 包裹独立公式。"
    verification_role: str = "你是一位{subject_name}阅卷老师。请判断学生的解题步骤是否正确。返回 JSON 格式。"
    verification_extra: str = ""
    final_answer_role: str = "你是一位{subject_name}阅卷老师。请判断学生的最终答案是否正确。"
    final_answer_extra: str = "请先自己求解，然后与学生答案对比。对精确值要求严格。请用中文输出 explanation 字段。"
    diagnosis_extra: str = "请诊断学生错误的根本原因。请用中文输出 root_cause_hypothesis 和 supporting_evidence 字段。"
    feedback_extra: str = "请为学生撰写简洁的学习反馈。请用中文输出所有字段。"
    review_problem_extra: str = "请生成 1-3 道类似的复习练习题，并说明出题理由。请用中文输出 problem_text 和 rationale 字段。"
    review_problem_all_correct_extra: str = "该学生在本道题中表现优秀，所有步骤均正确。请生成 1-3 道与原题主题相关的进阶练习题，难度由易到难。difficulty_label 请分别使用 easy、medium、hard。请用中文输出 problem_text 和 rationale 字段。"


class SympyDerivativePattern(BaseModel):
    pattern: str
    replacement: str


class SympyPostprocessModel(BaseModel):
    strip_prefixes: list[str] = Field(default_factory=list)
    derivative_patterns: list[SympyDerivativePattern] = Field(default_factory=list)


class RuleConditionModel(BaseModel):
    type: str
    value: str


class RuleAdjustmentModel(BaseModel):
    conditions: list[RuleConditionModel]
    label: str
    evidence: str
    violated_principles: list[str] = Field(default_factory=list)


class MockReviewProblemModel(BaseModel):
    problem_text: str
    related_weakness_code: str
    rationale: str
    difficulty_label: str


class MockReferenceSolutionModel(BaseModel):
    reference_text: str
    key_assertions: list[str] = Field(default_factory=list)


class MockDataModel(BaseModel):
    reference_solution: MockReferenceSolutionModel
    review_problems: list[MockReviewProblemModel] = Field(default_factory=list)


class SubjectConfigModel(BaseModel):
    subject_id: str
    display_name: str
    display_name_en: str = ""
    error_taxonomy: dict[str, TaxonomyEntryModel]
    topic_keywords: dict[str, list[str]] = Field(default_factory=dict)
    prompts: PromptTemplatesModel = Field(default_factory=PromptTemplatesModel)
    sympy_postprocess: SympyPostprocessModel = Field(default_factory=SympyPostprocessModel)
    rule_adjustments: list[RuleAdjustmentModel] = Field(default_factory=list)
    mock: MockDataModel
    budget_overrides: dict[str, Any] | None = None


class SubjectRegistry:
    _registry: dict[str, SubjectConfigModel] = {}
    _subjects_dir: Path | None = None

    @classmethod
    def initialize(cls, subjects_dir: Path | str | None = None) -> None:
        if cls._registry:
            return
        if subjects_dir is None:
            subjects_dir = Path(__file__).parent
        cls._subjects_dir = Path(subjects_dir)
        for yaml_file in cls._subjects_dir.glob("*.yaml"):
            if yaml_file.name.startswith("_"):
                continue
            config = cls._load_yaml(yaml_file)
            if config:
                cls._registry[config.subject_id] = config

    @classmethod
    def _load_yaml(cls, path: Path) -> SubjectConfigModel | None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                return None
            return SubjectConfigModel(**data)
        except Exception:
            return None

    @classmethod
    def get(cls, subject_id: str) -> SubjectConfigModel | None:
        if not cls._registry:
            cls.initialize()
        return cls._registry.get(subject_id)

    @classmethod
    def list_ids(cls) -> list[str]:
        if not cls._registry:
            cls.initialize()
        return sorted(cls._registry.keys())

    @classmethod
    def get_raw(cls, subject_id: str) -> dict[str, Any] | None:
        config = cls.get(subject_id)
        if config is None:
            return None
        return config.model_dump()

    @classmethod
    def reload(cls) -> None:
        """Clear cache and reload all YAML files."""
        cls._registry = {}
        cls.initialize()

    @classmethod
    def subjects_dir(cls) -> Path:
        """Return the subjects directory path."""
        if cls._subjects_dir is None:
            cls.initialize()
        return cls._subjects_dir or Path(__file__).parent

from __future__ import annotations

import json as _json
import threading
from contextlib import contextmanager

from stem_tutor.subjects.context import get_subject_context

_MATH_FORMAT_HINT = (
    "输出中涉及数学表达式时，请用 $...$ 包裹行内公式，用 $$...$$ 包裹独立公式。"
    "例如：$f'(x) = 2x$，$$\\int_0^1 x^2 dx = \\frac{1}{3}$$\n"
)

_active_subject: threading.local = threading.local()


def set_active_subject(subject_id: str) -> None:
    _active_subject.value = subject_id


def _current_subject_id() -> str:
    return getattr(_active_subject, "value", None) or "calculus"


@contextmanager
def active_subject_scope(subject_id: str):
    """Context manager that sets the active subject for the current thread
    and restores the previous value on exit. Prevents threading.local bleed
    between async coroutines that share a thread.
    """
    previous = getattr(_active_subject, "value", None)
    _active_subject.value = subject_id
    try:
        yield
    finally:
        if previous is None:
            try:
                del _active_subject.value
            except AttributeError:
                pass
        else:
            _active_subject.value = previous


def _get_prompts() -> dict[str, str]:
    try:
        ctx = get_subject_context(_current_subject_id())
        return ctx.prompts
    except Exception:
        return _fallback_prompts()


def _fallback_prompts() -> dict[str, str]:
    return {
        "system_role": "你是一个精确的{subject_name}辅导 JSON API。所有输出请使用中文（简体中文）。数学表达式请用 $...$ 包裹行内公式，用 $$...$$ 包裹独立公式。",
        "verification_role": "你是一位{subject_name}阅卷老师。请判断学生的解题步骤是否正确。返回 JSON 格式。",
        "verification_extra": (
            "1. 请结合上下文判断：如果当前步骤是中间过渡形式，且下一步完成了该步骤的推导，则当前步骤应判为正确\n"
            "2. 不要孤立地判断单个步骤的完整性，要考虑整个解题过程的连贯性\n"
            "3. 如果当前步骤是上一步的自然延续（如等式链的下一环），且数学上正确，应判为正确\n"
            "4. 跳步容忍（最常见的宽容场景）：学生可能省略某些中间计算步骤（如直接写出积分值、跳过化简、删去等于 1 的常数因子、合并积分系数）。即使当前步骤的首个表达式与上一步的最后一个表达式在表面上不同（因系数合并、=1 因子删除、常规积分计算被吃掉），只要满足以下任一条件，请判为 correct："
            "(a) 最终答案验证结果: CORRECT；或 (b) 当前步骤的最终结论在数学上正确（与上一步数学上等价或最终结果正确）。"
            "【优先级】规则 4 优先于规则 6/7（针对跳步场景）：如果跳过的内容是常规计算（积分值、代数化简、常数因子合并），即使等式链表面不连贯，仍按规则 4 判 correct。规则 6/7 只针对「非跳步引起的不连贯」（如漏写系数、错写公式）生效。"
            "【关键判断 vs 常规计算】"
            "关键判断（不可省略）：积分上下限（直接改变结果）、变号、绝对值、关键等价变换（如换元 u=x²）。"
            "常规计算（可省略）：∫sinφ dφ = 1、系数 1 化简、合并同类项、简单代数恒等变换。"
            "【示例】题目求三重积分 ∫₀^a ρ⁴ dρ · ∫₀^{π/2} sinφ dφ · ∫₀^{2π} dθ："
            "学生写「= 2π ∫₀^a ρ⁴ dρ · ∫₀^{π/2} sinφ dφ = 2π ∫₀^a ρ⁴ dρ = (2π/5) a⁵」。"
            "中间省略了 ∫₀^{π/2} sinφ dφ = 1 的显式计算（结果 = 1），同时把 ∫₀^{2π} dθ = 2π 直接代入。"
            "最终答案与参考一致 → S3 判 correct。省略的是等于 1 的常数因子和简单积分值，属常规计算便利省略（等式链表面不连贯，但数学正确）。"
            "【注意】仍然不可省略的：积分上下限、变号、绝对值、关键等价变换（换元等）。\n"
            "5. OCR/字形错误容忍：如果学生表达式中存在疑似 OCR 误识别的字形混淆（常见混淆对：z/2, o/0, l/1, B/8, q/9, n/m 等），满足以下任一条件时，请判为 correct，并在 evidence 中注明「疑似 OCR 误识别（X ↔ Y）」："
            "(a) 最终答案验证结果: CORRECT；或 (b) 后续步骤未受该混淆影响（后续步骤的数学推导与「假定当前步骤正确」一致）。"
            "【特别说明】「步骤内部矛盾」不构成排除条件：如果当前步骤因字形混淆出现「输入写错、输出却恰好正确」的内部矛盾（例如 S1 输入 R=zxy 但 S1 输出散度=z²+x²+y² 是正确值），且该矛盾未传播到后续步骤（S2/S3 使用了正确的散度），仍按本规则判为 correct。这是 OCR 误识别的典型表现——学生在下一步操作中使用了正确值，但没回头修正第一步的表达式。"
            "【示例】题目 R=2xy+y²z：学生 S1 写「R = zxy + y²z」（OCR 误读 2→z），同一行散度写「z² + x² + y²」（正确）。S2、S3 正确使用该散度，最终答案 2π/5 a⁵ 正确 → S1 判 correct，evidence 注明「疑似 OCR 误识别（z ↔ 2）」。"
            "注意：仅当字形混淆是唯一可识别的问题时才适用；如果当前步骤还有其他实质错误（符号错误、关键判断错误），按正常规则判为 incorrect_math\n"
            "6. 跨步连贯性检查（仅在规则 4/5 不适用时生效）：请验证当前步骤的首个表达式是否与上一步的最后一个表达式在数学上等价，包括所有系数和常数。"
            "例如，若上一步以 \"3∫...\" 结尾，当前步骤不能直接跳到 \"B(...)\" 而丢掉系数 3（除非满足规则 4 的跳步条件）\n"
            "7. 如果当前步骤与上一步不连贯（如丢失系数、漏掉常数、表达式突变），应判为 incorrect_math（除非满足规则 4 的跳步条件）\n"
            "8. 请用中文输出 evidence 字段，说明判断依据\n"
            "9. 工具结果优先：如果 execute_python 工具已对当前步骤进行了验证（如 sympy 化简、数值积分、jacobian 验证、数值代入等），**工具的数值结果应当作为权威依据**。不要基于对数学符号的字面解读来推翻工具的计算结果，除非你能指出工具结果与学生表达式之间的具体矛盾。\n"
            "10. 必须有具体错误才 flag：仅当你能指出**具体的、可验证的**错误时（如：具体的符号错误、具体的系数错误、具体的积分上下限错误、具体的等式等价性问题），才能判为 incorrect_math。「看起来不对」「感觉像重复」「可能有问题」等模糊判断**不构成**实质错误，不足以 flag。如果你的判断只是「我对这段数学的理解跟学生写法不一致」，但说不出具体哪里错——**默认判 correct**。\n"
            "【示例 1】学生 S3 写「∭(z²+x²+y²) dxdydz = ∭ρ²·ρ² sinφ dρdθdφ = ∭ρ⁴ sinφ dρdθdφ」。如果 LLM 想 flag「被积函数重复」，但说不出「哪个 ρ² 是错的、应为什么」，那这个判断就是模糊的，不构成实质错误 → 判 correct。\n"
            "【示例 2】学生写「x²+y² = r²，体积元 = r sinθ dr dθ dφ」，后续 r³sinθ 计算正确 → 不要 flag「r² × r sinθ 像是 r³ 错」——这是极坐标标准写法 → 判 correct。"
        ),
        "final_answer_role": "你是一位{subject_name}阅卷老师。请判断学生的最终答案是否正确。",
        "final_answer_extra": "请先自己求解，然后与学生答案对比。对精确值要求严格。请用中文输出 explanation 字段。",
        "diagnosis_extra": "请诊断学生错误的根本原因，使用一个错误类型代码。请用中文输出 root_cause_hypothesis 和 supporting_evidence 字段。",
        "feedback_extra": "请为学生撰写简洁的学习反馈。请用中文输出所有字段。",
        "review_problem_extra": "请生成 1-3 道类似的复习练习题，并说明出题理由。请用中文输出 problem_text 和 rationale 字段。",
        "review_problem_all_correct_extra": "该学生在本道题中表现优秀，所有步骤均正确。请生成 1-3 道与原题主题相关的进阶练习题，难度由易到难。difficulty_label 请分别使用 easy、medium、hard。请用中文输出 problem_text 和 rationale 字段。",
    }


def _subject_name() -> str:
    try:
        ctx = get_subject_context(_current_subject_id())
        return ctx.display_name
    except Exception:
        return "STEM"


def _inject_template(template: str, **kwargs: str) -> str:
    result = template.replace("{subject_name}", _subject_name())
    for key, value in kwargs.items():
        result = result.replace("{" + key + "}", value)
    return result


def verification_prompt(
    problem_text: str,
    reference_solution: str,
    step_text: str,
    step_id: str = "",
    total_steps: int = 0,
    full_solution: str = "",
    prev_text: str = "",
    next_text: str = "",
    assertions: list[str] | None = None,
    final_answer_status: str = "",
    reference_answer_hint: str = "",
    computation_hints: str = "",
) -> str:
    prompts = _get_prompts()
    context_parts = [
        _inject_template(prompts["verification_role"]) + "\n",
        f"题目: {problem_text}\n",
        f"参考解答: {reference_solution}\n",
    ]

    if full_solution:
        context_parts.append(f"\n【学生完整解题过程】\n{full_solution}\n")

    context_parts.append(f"\n【当前验证步骤】")
    if step_id and total_steps:
        context_parts.append(f"第 {step_id} 步（共 {total_steps} 步）\n")
    context_parts.append(f"当前步骤内容: {step_text}\n")
    if prev_text:
        context_parts.append(f"上一步: {prev_text}\n")
    else:
        context_parts.append("上一步: （第一步）\n")
    if next_text:
        context_parts.append(f"下一步: {next_text}\n")
    else:
        context_parts.append("下一步: （最后一步）\n")

    if assertions:
        context_parts.append(f"\n参考解答关键断言: {', '.join(assertions)}\n")

    if final_answer_status:
        context_parts.append(f"\n最终答案验证结果: {final_answer_status}\n")

    verification_extra = prompts.get("verification_extra", "")
    if verification_extra:
        context_parts.append(f"\n【验证要求】\n{verification_extra}\n")

    if reference_answer_hint:
        context_parts.append(f"\n【参考答案校验】\n{reference_answer_hint}\n")

    if computation_hints:
        context_parts.append(f"\n【预计算结果】\n{computation_hints}\n")

    context_parts.append(_MATH_FORMAT_HINT)

    return "".join(context_parts)


def final_answer_verification_prompt(problem_text: str, student_answer: str) -> str:
    prompts = _get_prompts()
    return (
        _inject_template(prompts["final_answer_role"]) + "\n"
        "返回 JSON 格式: {\"is_correct\": true/false, \"correct_answer\": \"...\", \"explanation\": \"...\"}\n"
        f"题目: {problem_text}\n"
        f"学生答案: {student_answer}\n"
        + _inject_template(prompts["final_answer_extra"]) + "\n"
    ) + _MATH_FORMAT_HINT


def diagnosis_prompt(step_text: str, evidence: str, taxonomy_codes: list[str],
                      problem_text: str = "", reference_solution: str = "") -> str:
    prompts = _get_prompts()
    codes = ", ".join(taxonomy_codes)
    parts = [
        "请诊断学生错误的根本原因，使用一个错误类型代码。\n",
        f"可选代码: {codes}\n",
    ]
    if problem_text:
        parts.append(f"题目: {problem_text}\n")
    if reference_solution:
        parts.append(f"参考解答: {reference_solution}\n")
    parts.extend([
        f"学生步骤: {step_text}\n",
        f"错误证据: {evidence}\n",
        _inject_template(prompts["diagnosis_extra"]) + "\n",
    ])
    return "".join(parts) + _MATH_FORMAT_HINT


def feedback_prompt(first_error_step: str | None, cause: str | None, concepts: list[str], problem_text: str = "") -> str:
    prompts = _get_prompts()
    parts = [
        "请为学生撰写简洁的学习反馈。\n",
        f"学科: {_subject_name()}\n",
    ]
    if problem_text:
        parts.append(f"原题: {problem_text}\n")
    parts.append(f"首个错误步骤: {first_error_step}\n")
    parts.append(f"可能原因: {cause}\n")
    parts.append(f"需复习概念: {', '.join(concepts)}\n")
    parts.append(_inject_template(prompts["feedback_extra"]) + "\n")
    return "".join(parts) + _MATH_FORMAT_HINT


def review_problem_prompt(weakness_codes: list[str], topic_tags: list[str], all_correct: bool = False, problem_text: str = "") -> str:
    prompts = _get_prompts()
    if all_correct and not weakness_codes:
        topics = ", ".join(topic_tags) if topic_tags else _subject_name()
        parts = [
            _inject_template(prompts["review_problem_all_correct_extra"]) + "\n",
            f"学科: {_subject_name()}\n",
            f"原题: {problem_text}\n" if problem_text else "",
            f"相关主题: {topics}\n",
            "请用中文输出 problem_text 和 rationale 字段。\n",
        ]
        return "".join(parts) + _MATH_FORMAT_HINT
    parts = [
        "请生成 1-3 道类似的复习练习题，并说明出题的理由。\n",
        f"学科: {_subject_name()}\n",
        f"原题: {problem_text}\n" if problem_text else "",
        f"薄弱知识点: {', '.join(weakness_codes)}\n",
        f"相关主题: {', '.join(topic_tags)}\n",
        _inject_template(prompts["review_problem_extra"]) + "\n",
    ]
    return "".join(parts) + _MATH_FORMAT_HINT


def report_prompt(
    aggregated_data: dict,
    time_range: dict,
    total_runs: int,
    taxonomy_summary: dict[str, str],
) -> str:
    parts: list[str] = []

    parts.append(
        "你是 STEM 错误诊断系统的学习报告分析师。\n\n"

        "## 你的专业背景\n"
        "- 精通微积分、线性代数、力学、电磁学、量子物理、热学、光学、相对论等 STEM 学科的教学法与常见学生错误模式\n"
        "- 擅长从错误记录中识别系统性知识盲区，而非孤立地看待每次错误\n"
        "- 擅长纵向追踪学习者的进步轨迹，发现被忽视的积极信号\n"
        "- 你的分析风格：数据驱动、具体明确、建设性强，避免笼统的建议\n\n"

        "## 任务说明\n\n"
        "学生通过系统提交解题过程，系统会逐步验证每个步骤并诊断错误类型。"
        "现在你需要基于一段时间内的所有诊断记录，生成一份深度、个性化、可操作的学习报告。\n\n"

        "报告需涵盖以下五个分析维度：\n\n"
        "1. **错误模式识别** — 从多次诊断中发现反复出现的错误类型和规律，"
        "判断哪些是偶然失误，哪些是系统性问题。当同一错误在 3 次以上诊断中出现时，"
        "应标记为反复性错误（recurring: true）\n\n"
        "2. **知识盲区定位** — 将错误映射到具体的知识维度（如符号操作、"
        "计算过程、概念理解、逻辑推理等），结合科目维度，精确定位薄弱环节。"
        "使用掌握度矩阵中的数值量化盲区严重程度\n\n"
        "3. **错误根因演变** — 追踪错误根因随时间的变化轨迹，识别改善趋势"
        "和新出现的错误模式。用趋势标签（improving/worsening/stable/shifting）"
        "标注每个时间段的变化方向\n\n"
        "4. **进步信号检测** — 主动发现学生的积极变化，包括某类错误的消失、"
        "错误严重程度的降低、新掌握的知识点等。进步信号无论大小都值得关注\n\n"
        "5. **改进建议** — 基于以上分析，给出有优先级、有时间线的具体行动方案。"
        "每条建议必须可执行，不要出现'多加练习'这类笼统建议\n\n"
    )

    parts.append(
        "## 分析原则\n\n"
        "- **具体胜于笼统**：不要说'注意符号操作'，而要说'在链式法则求导时，"
        "你习惯性地遗漏内层函数的导数因子，这在你的 8 次诊断中出现了 5 次'\n"
        "- **纵向对比胜于横截面**：关注同一学生随时间的进步和退步，"
        "而非孤立地评判某次诊断的结果\n"
        "- **建设性优先**：先肯定进步，再指出不足；先分析原因，再给出建议\n"
        "- **区分模式与噪音**：只出现 1 次的错误可能是偶然，反复出现 3 次以上的才构成'模式'\n"
        "- **语气鼓励但诚实**：不要回避严重问题，但也不要制造焦虑；"
        "用'建议重点加强'而非'你很薄弱'这样的措辞\n\n"
    )

    parts.append("## 诊断统计数据\n\n")
    parts.append(
        f"- 统计时间范围：{time_range.get('start', '?')} 至 {time_range.get('end', '?')}"
        f"（共 {time_range.get('days', '?')} 天）\n"
        f"- 总诊断次数：{total_runs}\n\n"
    )

    if taxonomy_summary:
        parts.append("### 错误类型编码对照表\n\n")
        for code, desc in taxonomy_summary.items():
            parts.append(f"- `{code}`: {desc}\n")
        parts.append("\n")

    error_frequency = aggregated_data.get("error_frequency", [])
    if error_frequency:
        parts.append("### 错误类型频率\n\n")
        for ef in error_frequency:
            parts.append(
                f"- **{ef.get('category', '?')}**（{ef.get('error_code', '?')}）："
                f"出现 {ef.get('count', 0)} 次，涉及 {ef.get('runs_involved', 0)} 次诊断\n"
            )
        parts.append("\n")

    radar_data = aggregated_data.get("radar_data", {})
    if radar_data:
        parts.append("### 各科目错误类型分布\n\n```json\n")
        parts.append(_json.dumps(radar_data, ensure_ascii=False, indent=2))
        parts.append("\n```\n\n")

    heatmap_data = aggregated_data.get("heatmap_data", {})
    if heatmap_data:
        parts.append(        "### 知识盲区矩阵（值 0=未掌握, 1=完全掌握；按 run 粒度扣除已理解的诊断）\n\n")
        parts.append(f"维度：{_json.dumps(heatmap_data.get('skills', []), ensure_ascii=False)}\n")
        parts.append(f"科目：{_json.dumps(heatmap_data.get('subjects', []), ensure_ascii=False)}\n")
        matrix = heatmap_data.get("matrix", [])
        parts.append(f"矩阵：\n```json\n{_json.dumps(matrix, ensure_ascii=False, indent=2)}\n```\n\n")

    error_evolution = aggregated_data.get("error_evolution", [])
    if error_evolution:
        parts.append("### 错误根因时间演变\n\n")
        for ev in error_evolution:
            dist = ev.get("distribution", {})
            parts.append(
                f"- **{ev.get('period', '?')}**："
                f"{_json.dumps(dist, ensure_ascii=False)}\n"
            )
        parts.append("\n")

    improvement_signals = aggregated_data.get("improvement_signals", [])
    if improvement_signals:
        parts.append("### 已检测到的进步信号\n\n")
        for sig in improvement_signals:
            parts.append(f"- [{sig.get('type', '?')}] {sig.get('description', '')}\n")
        parts.append("\n")

    mastery_summary = aggregated_data.get("mastery_summary")
    if mastery_summary:
        parts.append("### 学生自我评估数据（掌握度标记）\n\n")
        mc = mastery_summary.get("mastered_count", 0)
        tt = mastery_summary.get("total_error_types", 0)
        parts.append(f"总体掌握率：{mc}/{tt}\n\n")
        mastered_items = mastery_summary.get("mastered_items", [])
        if mastered_items:
            parts.append("以下错误类型学生已标记为「已掌握」（可降低分析优先级）：\n")
            for item in mastered_items:
                parts.append(f"- {item.get('error_code', '?')}（出现过 {item.get('total_encounters', 0)} 次）\n")
            parts.append("\n")
        learning_items = mastery_summary.get("learning_items", [])
        if learning_items:
            parts.append("以下错误类型学生仍标记为「学习中」（应重点关注）：\n")
            for item in learning_items:
                parts.append(f"- {item.get('error_code', '?')}（出现过 {item.get('total_encounters', 0)} 次，最近出现 {item.get('last_seen', '未知')[:10]}）\n")
            parts.append("\n")

    error_examples = aggregated_data.get("error_examples", {})
    if error_examples:
        parts.append("### 代表性错误实例\n\n")
        parts.append("以下是各错误类型在近期诊断中的具体实例，请在分析中引用这些真实案例：\n\n")
        for ec, examples in error_examples.items():
            tax_desc = taxonomy_summary.get(ec, "")
            parts.append(f"**{ec}**" + (f"（{tax_desc}）" if tax_desc else "") + "\n")
            for ex in examples:
                parts.append(f"- 日期：{ex.get('date', '?')}，学科：{ex.get('subject', '?')}\n")
                if ex.get("student_step"):
                    parts.append(f"  学生步骤：{ex['student_step']}\n")
                if ex.get("analysis"):
                    parts.append(f"  根因分析：{ex['analysis']}\n")
                if ex.get("evidence"):
                    parts.append(f"  支持证据：{ex['evidence']}\n")
            parts.append("\n")

    resolved_summary = aggregated_data.get("resolved_summary")
    if resolved_summary and resolved_summary.get("by_error_code"):
        parts.append("### 学生主动复习的错误类型\n\n")
        parts.append("以下错误类型学生在历史分析中主动标记为「已理解」（说明学生有意识在弥补这些薄弱点）。\n")
        parts.append("在制定薄弱环节诊断和行动建议时，将此类错误视为「学生正在主动管理」的方向，与「未管理」的薄弱环节区分对待：\n\n")
        for ec, info in resolved_summary["by_error_code"].items():
            subj_str = ", ".join(info.get("subjects", [])) or "未知学科"
            parts.append(f"- {ec}：复习过 {info.get('count', 0)} 次，涉及学科 {subj_str}\n")
        parts.append("\n")

    parts.append(
        "## 输出格式\n\n"
        "请严格输出以下 JSON 结构（不要输出 JSON 以外的任何内容）：\n\n"
        "```json\n"
        "{\n"
        '  "sections": [\n'
        "    {\n"
        '      "type": "error_patterns",\n'
        '      "title": "错误模式识别",\n'
        '      "icon": "🔍",\n'
        '      "summary": "1-3 句总体概述，概括最重要的发现",\n'
        '      "items": [\n'
        "        {\n"
        '          "name": "错误类型名称（中文）",\n'
        '          "error_code": "ERROR_CODE",\n'
        '          "count": 15,\n'
        '          "runs_involved": 8,\n'
        '          "recurring": true,\n'
        '          "analysis": "具体分析，引用数据，解释为什么这是系统性问题而非偶然错误",\n'
        '          "related_subjects": ["<根据学生本次错误涉及的实际学科填写，例：力学、量子物理>"]\n'
        "        }\n"
        "      ]\n"
        "    },\n"
        "    {\n"
        '      "type": "blind_spots",\n'
        '      "title": "知识盲区定位",\n'
        '      "icon": "🎯",\n'
        '      "summary": "1-3 句概述",\n'
        '      "items": [\n'
        "        {\n"
        '          "name": "知识点名称",\n'
        '          "severity": "high",\n'
        '          "subject": "<根据实际学科填写，例：电磁学、热学>",\n'
        '          "mastery": 0.35,\n'
        '          "analysis": "具体说明这个盲区的表现和影响"\n'
        "        }\n"
        "      ]\n"
        "    },\n"
        "    {\n"
        '      "type": "error_evolution",\n'
        '      "title": "错误根因演变",\n'
        '      "icon": "📈",\n'
        '      "summary": "1-3 句总体趋势描述",\n'
        '      "timeline": [\n'
        "        {\n"
        '          "period": "时间段标签",\n'
        '          "trend": "improving",\n'
        '          "description": "这个时期错误模式的具体变化"\n'
        "        }\n"
        "      ]\n"
        "    },\n"
        "    {\n"
        '      "type": "improvements",\n'
        '      "title": "进步信号",\n'
        '      "icon": "✅",\n'
        '      "summary": "1-3 句积极总结",\n'
        '      "items": [\n'
        "        {\n"
        '          "description": "具体进步内容",\n'
        '          "evidence": "支撑这个判断的数据依据",\n'
        '          "significance": "high"\n'
        "        }\n"
        "      ]\n"
        "    },\n"
        "    {\n"
        '      "type": "action_plan",\n'
        '      "title": "改进建议",\n'
        '      "icon": "🚀",\n'
        '      "summary": "1-2 句总体建议方向",\n'
        '      "priorities": [\n'
        "        {\n"
        '          "level": "优先",\n'
        '          "focus": "改进方向",\n'
        '          "reason": "为什么这个方向最重要",\n'
        '          "actions": [\n'
        '            "具体可执行的行动1",\n'
        '            "具体可执行的行动2"\n'
        "          ],\n"
        '          "related_errors": ["ERROR_CODE_1"]\n'
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n\n"

        "## 格式约束\n\n"
        "- 所有文字内容使用中文（简体中文）\n"
        '- severity 只能是 "high"、"medium"、"low"\n'
        '- trend 只能是 "improving"、"worsening"、"stable"、"shifting"\n'
        '- significance 只能是 "high"、"medium"、"low"\n'
        '- level 只能是 "优先"、"建议"、"保持"\n'
        "- recurring 为 true 表示该错误在 3 次以上诊断中出现\n"
        "- mastery 为 0.0-1.0 的浮点数，来自掌握度矩阵\n"
        "- actions 每条建议必须是具体可执行的，不要出现'多加练习'这类笼统建议\n"
        "- 数学公式使用 LaTeX 格式：行内 $...$，独立公式 $$...$$\n"
    )

    parts.append(_MATH_FORMAT_HINT)

    return "".join(parts)

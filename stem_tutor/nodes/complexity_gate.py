from __future__ import annotations

import json
import logging
from enum import Enum

from stem_tutor.providers.base import LLMProvider

logger = logging.getLogger(__name__)


class ProblemComplexity(str, Enum):
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


_COMPLEXITY_CLASSIFICATION_PROMPT = (
    "你是一个STEM题目复杂度分析器。根据题目文本，判断该题的求解复杂度。\n"
    "\n"
    "## 学科覆盖\n"
    "题目可能来自以下任一学科：微积分、线性代数、力学、电磁学、光学、量子物理、相对论、热学。\n"
    "你需要自动识别学科，并基于该学科的典型求解路径进行判断。\n"
    "\n"
    "## 复杂度定义\n"
    "\n"
    "### simple（简单）\n"
    "满足以下特征之一：\n"
    "- **单步运算**：只需套用一个公式或法则即可得到结果\n"
    "  - 微积分：基本幂函数求导/积分、简单极限（直接代入）、基本化简\n"
    "  - 线性代数：2×2/3×3 行列式计算、简单矩阵乘法、简单向量运算\n"
    "  - 力学：单一力的分解、匀变速直线运动单公式应用\n"
    "  - 电磁学：点电荷库仑力求值、简单电场/电势计算\n"
    "  - 光学：单透镜成像公式直接代入\n"
    "  - 量子：归一化常系数波函数、简单算符对易判定\n"
    "  - 相对论：单步洛伦兹因子计算、时间膨胀/长度收缩直接套公式\n"
    "  - 热学：理想气体状态方程直接代入求值\n"
    "- **表达式简短**：题目中涉及的数学表达式通常不超过 1-2 个\n"
    "- **无需多步推导或工具验证**：心算或笔算一步可验证\n"
    "\n"
    "### moderate（中等）\n"
    "满足以下特征之一：\n"
    "- **多步运算但路径明确**：需要 2-4 步推导，每步有标准方法\n"
    "  - 微积分：换元积分、分部积分、含参极限、简单ODE（分离变量）\n"
    "  - 线性代数：高斯消元求秩/解方程组、特征值计算（3阶以下）\n"
    "  - 力学：多体受力分析+牛顿定律、能量守恒+动量守恒组合\n"
    "  - 电磁学：安培环路定律求对称磁场、RC/RL电路暂态\n"
    "  - 光学：薄膜干涉光程差计算、多缝衍射\n"
    "  - 量子：一维势阱能级与波函数、升降算符应用\n"
    "  - 相对论：多参考系变换、速度叠加链\n"
    "  - 热学：多方过程计算、卡诺循环效率\n"
    "- **需要变量替换或分情况讨论**，但不涉及创造性构造\n"
    "- **中等长度表达式**：通常 2-4 个关键表达式\n"
    "\n"
    "### complex（复杂）\n"
    "满足以下特征之一：\n"
    "- **需要创造性求解或多方法验证**：\n"
    "  - 微积分：多重积分、广义积分收敛性、级数展开、含参积分、高阶ODE\n"
    "  - 线性代数：高维空间证明、谱分解、Jordan标准形、大矩阵运算\n"
    "  - 力学：刚体转动+角动量守恒、拉格朗日/哈密顿力学\n"
    "  - 电磁学：麦克斯韦方程组应用、边界值问题、矢量势\n"
    "  - 光学：多层膜干涉、衍射积分计算\n"
    "  - 量子：三维势阱、氢原子径向方程、微扰论、散射\n"
    "  - 相对论：广义相对论度规、四维矢量变换、张量运算\n"
    "  - 热学：统计力学配分函数、系综理论、相变理论\n"
    "- **需要数值方法或符号计算工具才能可靠验证**\n"
    "- **表达式多且嵌套深**：5 个以上关键表达式或深层嵌套分数/积分\n"
    "- **开放性或证明题**：结论不确定，需探索多种路径\n"
    "\n"
    "## 输出格式\n"
    '严格输出以下 JSON，不要输出任何其他内容：\n'
    '{{"complexity": "simple" 或 "moderate" 或 "complex", "subject": "学科名称或unknown", "reason": "一句话理由"}}\n'
    "\n"
    "## 题目\n"
    "{problem_text}"
)


def classify_complexity(
    problem_text: str,
    provider: LLMProvider,
) -> ProblemComplexity:
    prompt = _COMPLEXITY_CLASSIFICATION_PROMPT.format(problem_text=problem_text)
    schema_hint = (
        '{"complexity": "simple|moderate|complex", '
        '"subject": "string", "reason": "string"}'
    )
    defaults = {
        "complexity": "moderate",
        "subject": "unknown",
        "reason": "classification fallback",
    }

    try:
        raw = provider.invoke_structured(prompt, schema_hint, defaults)
        complexity_str = str(raw.get("complexity", "moderate")).strip().lower()
        subject = str(raw.get("subject", "unknown"))
        reason = str(raw.get("reason", ""))

        if complexity_str in ("simple", "moderate", "complex"):
            result = ProblemComplexity(complexity_str)
        else:
            logger.warning(f"[complexity_gate] Unexpected complexity value: {complexity_str}, defaulting to moderate")
            result = ProblemComplexity.MODERATE

        logger.info(
            f"[complexity_gate] classified as {result.value}, "
            f"subject={subject}, reason={reason[:80]}"
        )
        return result

    except Exception as e:
        logger.warning(f"[complexity_gate] Classification failed: {e}, defaulting to moderate")
        return ProblemComplexity.MODERATE

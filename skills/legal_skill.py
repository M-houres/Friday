"""
法务审查 Skill —— 上传合同/协议，AI自动识别风险条款并生成审查报告

全链路: 文档解析 → 条款提取 → 并行风险审查 → 合规检查 → 报告生成 → 交付

与PPT Skill完全互换：只需 import 此文件，框架自动识别触发词("合同|法务|审查|合规")
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.tools.skill import FridaySkill, tool, skill
from src.models.router import model_router
from src.models.base import Message
from src.artifacts.service import artifact_service

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).parent.parent / "static" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@skill(
    name="法务审查",
    trigger="合同|法务|审查|合规|协议|法律|风险|条款|审合同|查合同",
    description="上传合同或协议文本，AI自动识别风险条款、合规问题，生成专业法务审查报告。支持中英文合同。",
    version="1.0.0",
    icon="⚖️",
)
class LegalReviewSkill(FridaySkill):
    workflow = [
        {"id": "parse_doc", "tool": "parse_document", "name": "文档解析", "dependencies": []},
        {"id": "extract", "tool": "extract_clauses", "name": "条款提取", "dependencies": ["parse_doc"]},
        {"id": "review", "tool": "review_all_clauses", "name": "并行风险审查", "dependencies": ["extract"]},
        {"id": "compliance", "tool": "check_compliance", "name": "合规检查", "dependencies": ["review"]},
        {"id": "report", "tool": "generate_report", "name": "生成审查报告", "dependencies": ["compliance"]},
        {"id": "deliver", "tool": "deliver_report", "name": "交付报告", "dependencies": ["report"]},
    ]

    output = {
        "format": "markdown_html",
        "sections": ["基本信息", "风险摘要", "逐条审查", "合规检查", "修改建议", "结论"],
        "severity_levels": ["🔴 高风险", "🟡 中风险", "🟢 低风险", "ℹ️ 提示"],
        "jurisdiction": "中国法",
    }

    @tool(
        name="parse_document",
        description="解析用户提交的合同文档文本，提取基本信息：合同类型、签约方、日期、金额等",
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "用户输入的合同文本或描述"},
                "context": {"type": "object", "description": "额外上下文"},
            },
            "required": ["task"],
        },
    )
    async def parse_document(self, task: str, context: Optional[dict] = None) -> dict:
        logger.info(f"[法务] 解析文档: {task[:80]}")

        messages = [
            Message(
                role="system",
                content="""你是专业法务文档解析器。提取合同关键信息为JSON。

输出格式:
{
  "contract_type": "合同类型(买卖/租赁/劳动/服务/NDA/合资等)",
  "parties": [
    {"name": "甲方名称", "role": "甲方角色", "type": "company|individual"},
    {"name": "乙方名称", "role": "乙方角色", "type": "company|individual"}
  ],
  "effective_date": "生效日期或null",
  "expiry_date": "到期日期或null",
  "total_value": "合同金额或null",
  "governing_law": "适用法律",
  "language": "zh|en",
  "word_count_approx": 1000
}""",
            ),
            Message(role="user", content=f"解析以下合同内容:\n{task[:8000]}"),
        ]

        response = await model_router.chat(messages=messages, temperature=0.2, max_tokens=2048)
        try:
            doc_info = self._parse_json(response.content)
        except Exception:
            doc_info = {
                "contract_type": "通用合同",
                "parties": [{"name": "甲方", "role": ""}, {"name": "乙方", "role": ""}],
                "language": "zh",
            }

        return {"success": True, "data": doc_info, "tokens": response.tokens_used}

    @tool(
        name="extract_clauses",
        description="从合同中提取关键条款，分为：核心条款、风险条款、一般条款",
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "原始合同文本"},
                "context": {"type": "object", "description": "文档解析结果"},
            },
            "required": ["task"],
        },
        depends_on=["parse_document"],
    )
    async def extract_clauses(self, task: str, context: Optional[dict] = None) -> dict:
        logger.info(f"[法务] 提取条款")

        messages = [
            Message(
                role="system",
                content="""你是合同条款分析专家。提取合同中的关键条款。

输出JSON数组，每个条款一个对象:
[
  {
    "clause_id": "C01",
    "clause_type": "payment|termination|liability|ip|confidentiality|non_compete|dispute|force_majeure|warranty|general",
    "title": "条款标题",
    "original_text": "原文(截取关键部分,200字内)",
    "risk_category": "high|medium|low|info",
    "risk_summary": "一句话风险提示"
  },
  ...
]

关注重点:
- 付款条件是否明确
- 违约责任是否对等
- 知识产权归属
- 保密义务范围和期限
- 争议解决条款
- 终止条件和后果""",
            ),
            Message(role="user", content=f"合同内容:\n{task[:8000]}"),
        ]

        response = await model_router.chat(messages=messages, temperature=0.3, max_tokens=4096)

        try:
            clauses = self._parse_json(response.content)
            if isinstance(clauses, dict):
                clauses = clauses.get("clauses", clauses.get("items", [clauses]))
            if not isinstance(clauses, list):
                clauses = [clauses]
        except Exception:
            clauses = [
                {"clause_id": "C01", "clause_type": "general", "title": "待审查条款",
                 "original_text": task[:200], "risk_category": "medium",
                 "risk_summary": "待专业审查"},
            ]

        high_risk = [c for c in clauses if c.get("risk_category") == "high"]
        logger.info(f"[法务] 提取 {len(clauses)} 条, 高风险 {len(high_risk)} 条")

        return {"success": True, "data": clauses, "high_risk_count": len(high_risk)}

    @tool(
        name="review_all_clauses",
        description="并行审查所有条款，逐条分析法律风险、给出具体修改建议和风险评分",
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "原始任务"},
                "context": {"type": "object", "description": "条款提取结果"},
            },
            "required": ["task"],
        },
        depends_on=["extract_clauses"],
        is_expensive=True,
    )
    async def review_all_clauses(self, task: str, context: Optional[dict] = None) -> dict:
        clauses = context.get("extract_clauses", {}).get("data", []) if context else []

        if not clauses:
            return {"success": False, "error": "未提取到条款", "data": []}

        logger.info(f"[法务] 并行审查 {len(clauses)} 条")

        async def review_single_clause(clause: dict) -> dict:
            clause_text = clause.get("original_text", "")
            clause_type = clause.get("clause_type", "general")
            clause_title = clause.get("title", "未命名条款")
            risk_cat = clause.get("risk_category", "medium")

            messages = [
                Message(
                    role="system",
                    content="""你是资深法律顾问。审查合同条款并提供专业意见。

输出JSON:
{
  "clause_id": "原始ID",
  "risk_score": 0-100,
  "risk_level": "high|medium|low|info",
  "analysis": "法律风险分析(200字)",
  "suggested_revision": "修改建议文本(可选)",
  "legal_basis": "法律依据(如民法典第X条)",
  "negotiation_priority": "high|medium|low",
  "is_standard": true/false
}""",
                ),
                Message(
                    role="user",
                    content=f"条款类型: {clause_type}\n条款: {clause_title}\n原文: {clause_text}\n初步风险: {risk_cat}",
                ),
            ]

            try:
                response = await model_router.chat(messages=messages, temperature=0.3, max_tokens=2048)
                review = self._parse_json(response.content)
                review["clause_id"] = clause.get("clause_id", "")
                review["clause_title"] = clause_title
                review["_tokens"] = response.tokens_used
                return review
            except Exception as e:
                return {
                    "clause_id": clause.get("clause_id", ""),
                    "clause_title": clause_title,
                    "risk_score": 50,
                    "risk_level": "medium",
                    "analysis": f"无法完成深度审查: {str(e)[:100]}",
                    "negotiation_priority": "medium",
                }

        # === 并行审查 ===
        tasks = [review_single_clause(c) for c in clauses]
        reviews = await asyncio.gather(*tasks)

        high = [r for r in reviews if r.get("risk_level") == "high"]
        medium = [r for r in reviews if r.get("risk_level") == "medium"]
        total_tokens = sum(r.get("_tokens", 0) for r in reviews)

        return {
            "success": True,
            "data": reviews,
            "stats": {"total": len(reviews), "high": len(high), "medium": len(medium), "low": len(reviews) - len(high) - len(medium)},
            "total_tokens": total_tokens,
        }

    @tool(
        name="check_compliance",
        description="检查合同整体合规性：必备条款完整性、法律合规问题、签约方资质关注点",
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "原始任务"},
                "context": {"type": "object", "description": "审查结果"},
            },
            "required": ["task"],
        },
        depends_on=["review_all_clauses"],
    )
    async def check_compliance(self, task: str, context: Optional[dict] = None) -> dict:
        reviews = context.get("review_all_clauses", {}).get("data", []) if context else []
        doc_info = context.get("parse_document", {}).get("data", {}) if context else {}

        review_summary = json.dumps(
            [{"title": r.get("clause_title", ""), "risk": r.get("risk_level", ""), "score": r.get("risk_score", 0)}
             for r in reviews[:20]],
            ensure_ascii=False,
        )

        messages = [
            Message(
                role="system",
                content="""你是合规专家。对合同进行整体合规检查。

输出JSON:
{
  "overall_risk_score": 0-100,
  "overall_risk_level": "high|medium|low",
  "missing_clauses": ["缺失的必要条款"],
  "compliance_issues": [
    {"issue": "合规问题", "severity": "high|medium|low", "recommendation": "建议"}
  ],
  "party_risk_flags": ["签约方风险提示"],
  "regulatory_concerns": ["监管关注点"],
  "summary": "总体合规评估(150字)"
}""",
            ),
            Message(
                role="user",
                content=f"合同类型: {doc_info.get('contract_type', '未识别')}\n适用法律: {doc_info.get('governing_law', '中国法')}\n条款审查摘要:\n{review_summary}",
            ),
        ]

        response = await model_router.chat(messages=messages, temperature=0.3, max_tokens=2048)

        try:
            compliance = self._parse_json(response.content)
        except Exception:
            compliance = {
                "overall_risk_score": 50,
                "overall_risk_level": "medium",
                "compliance_issues": [],
                "summary": "合规检查完成",
            }

        return {"success": True, "data": compliance, "tokens": response.tokens_used}

    @tool(
        name="generate_report",
        description="生成完整的法务审查报告(Markdown格式)：风险摘要、逐条审查、合规意见、修改建议",
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "原始任务"},
                "context": {"type": "object", "description": "全部前置结果"},
            },
            "required": ["task"],
        },
        depends_on=["check_compliance"],
    )
    async def generate_report(self, task: str, context: Optional[dict] = None) -> dict:
        doc = context.get("parse_document", {}).get("data", {}) if context else {}
        clauses = context.get("extract_clauses", {}).get("data", []) if context else {}
        reviews = context.get("review_all_clauses", {}).get("data", []) if context else {}
        compliance = context.get("check_compliance", {}).get("data", {}) if context else {}
        review_stats = context.get("review_all_clauses", {}).get("stats", {}) if context else {}

        severity_icon = {"high": "🔴", "medium": "🟡", "low": "🟢", "info": "ℹ️"}
        contract_type = doc.get("contract_type", "未识别")
        parties = doc.get("parties", [])
        party_names = " 与 ".join(p.get("name", "?") for p in parties[:2])

        report = f"""# ⚖️ 法务审查报告

---

## 📋 基本信息

| 项目 | 内容 |
|------|------|
| **合同类型** | {contract_type} |
| **签约方** | {party_names} |
| **适用法律** | {doc.get('governing_law', '中国法')} |
| **审查日期** | {datetime.now().strftime('%Y-%m-%d %H:%M')} |
| **审查引擎** | 星期五 AI Agent v1.0 |

---

## 🔴 风险摘要

| 风险等级 | 数量 |
|----------|------|
| 🔴 高风险 | {review_stats.get('high', 0)} 条 |
| 🟡 中风险 | {review_stats.get('medium', 0)} 条 |
| 🟢 低风险 | {review_stats.get('low', 0)} 条 |

**综合风险评分**: {compliance.get('overall_risk_score', 50)}/100 ({compliance.get('overall_risk_level', 'medium').upper()})

> {compliance.get('summary', '待评估')}

---

## 📝 逐条审查

"""

        for i, review in enumerate(reviews, 1):
            icon = severity_icon.get(review.get("risk_level", "medium"), "🟡")
            report += f"""### {i}. {icon} {review.get('clause_title', '未命名条款')}

**风险评分**: {review.get('risk_score', 50)}/100 | **谈判优先级**: {review.get('negotiation_priority', 'medium').upper()}

**分析**: {review.get('analysis', '无详细分析')}

"""

            if review.get("suggested_revision"):
                report += f"""**修改建议**:
> {review.get('suggested_revision', '')}

"""

            if review.get("legal_basis"):
                report += f"**法律依据**: {review.get('legal_basis', '')}\n\n"

            if review.get("is_standard") is True:
                report += "📌 *此为行业标准条款*\n\n"

            report += "---\n\n"

        report += f"""## ✅ 合规检查

"""

        for issue in compliance.get("compliance_issues", []):
            icon = severity_icon.get(issue.get("severity", "medium"), "🟡")
            report += f"- {icon} **{issue.get('issue', '')}**: {issue.get('recommendation', '')}\n"

        missing = compliance.get("missing_clauses", [])
        if missing:
            report += f"\n### ⚠️ 缺失必要条款\n\n"
            for m in missing:
                report += f"- {m}\n"

        party_flags = compliance.get("party_risk_flags", [])
        if party_flags:
            report += f"\n### ⚠️ 签约方风险提示\n\n"
            for flag in party_flags:
                report += f"- {flag}\n"

        regulatory = compliance.get("regulatory_concerns", [])
        if regulatory:
            report += f"\n### 📜 监管关注\n\n"
            for r in regulatory:
                report += f"- {r}\n"

        report += f"""
---

## 📊 结论与建议

**总体评估**: {compliance.get('overall_risk_level', 'medium').upper()} 风险合同

{compliance.get('summary', '')}

### 签约建议

"""

        overall = review_stats.get("high", 0)
        if overall >= 3:
            report += "🔴 **强烈建议重新谈判** —— 本合同存在多项重大风险条款，签约前务必修改或澄清。\n"
        elif overall >= 1:
            report += "🟡 **建议审慎签约** —— 存在个别风险条款，建议与对方协商修改后签约。\n"
        else:
            report += "🟢 **可以签约** —— 未发现重大风险条款，注意履行过程中的合规义务。\n"

        report += f"""
---

*本报告由AI自动生成，仅供参考。重大合同请咨询专业律师。*

*生成引擎: 星期五 (Friday) AI Agent Framework v1.0*
"""

        import uuid
        report_id = uuid.uuid4().hex[:8]
        filename = f"legal_review_{report_id}.md"
        filepath = OUTPUT_DIR / filename
        filepath.write_text(report, encoding="utf-8")
        workflow_id = (context or {}).get("_workflow_id", "adhoc")
        owner_user_id = (context or {}).get("_user_id", "default")
        artifact = artifact_service.create_from_file(
            workflow_id=workflow_id,
            filename=filename,
            source_path=str(filepath),
            owner_user_id=owner_user_id,
            content_type="text/markdown",
        )

        return {
            "success": True,
            "data": {
                "report": report,
                "filename": artifact["filename"],
                "filepath": str(filepath),
                "artifact_id": artifact["artifact_id"],
                "download_url": artifact["download_url"],
                "contract_type": contract_type,
                "risk_level": compliance.get("overall_risk_level", "medium"),
                "risk_score": compliance.get("overall_risk_score", 50),
            },
        }

    @tool(
        name="deliver_report",
        description="生成最终交付物摘要：包含报告下载链接和审查要点总结",
        parameters={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "原始任务"},
                "context": {"type": "object", "description": "报告生成结果"},
            },
            "required": ["task"],
        },
        depends_on=["generate_report"],
    )
    async def deliver_report(self, task: str, context: Optional[dict] = None) -> dict:
        report_data = context.get("generate_report", {}).get("data", {}) if context else {}

        download_url = report_data.get("download_url", "")
        risk_level = report_data.get("risk_level", "medium")
        risk_score = report_data.get("risk_score", 50)
        contract_type = report_data.get("contract_type", "合同")

        level_text = {"high": "高风险 ⚠️", "medium": "中风险 ⚡", "low": "低风险 ✅"}
        level_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}

        return {
            "success": True,
            "data": {
                "download_url": download_url,
                "filename": report_data.get("filename", "report.md"),
                "contract_type": contract_type,
                "risk_level": risk_level,
                "risk_score": risk_score,
                "summary": f"{level_icon.get(risk_level, '')} {contract_type}审查完成\n综合风险评分: {risk_score}/100\n风险等级: {level_text.get(risk_level, risk_level)}\n\n点击下载查看完整审查报告。",
                "generated_at": datetime.now().isoformat(),
            },
        }

    @staticmethod
    def _parse_json(content: str) -> dict | list:
        content = content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return json.loads(content)

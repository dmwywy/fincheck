# 返回格式模块
# src/schemas.py
from pydantic import BaseModel, Field

class Evidence(BaseModel):
    chunk_id: str = Field(description="必须来自本次输入，不允许模型自行编造")
    quote: str = Field(default="", max_length=200, description="原文片段，需在输入文本中真实存在")

class ItemMap(BaseModel):
    raw_header: str
    canonical_item: str          # 只能填标准清单里的 code；清单外的值由程序判为无效
    confidence: float = Field(ge=0, le=1)

class MatchItemOut(BaseModel):
    """科目归一化：模型只做「原文科目名 → 标准科目编号」的语义映射。
    本任务不含 evidence：科目在财报中的位置（page/chunk_id/raw_text）由程序
    从数字经济A 的解析记录确定性回填，见 src/pipeline._attach_evidence。
    依据 interfaces.md §5.3 禁则3「模型不生成页码」——让模型复述 chunk_id 无信息增益，
    反而增加"抄错导致误杀正确映射"的路径。"""
    ok: bool = True
    items: list[ItemMap] = []
    reason: str = ""

class ClaimMatch(BaseModel):
    claim_id: str
    item_code: str               # 只能填 fields.yaml 里的 code
    period: str = ""             # 如 2025（时期）/ 2025-12-31（时点）；与项目期间编码一致
    confidence: float = Field(ge=0, le=1)
    reason: str = ""

class MatchClaimOut(BaseModel):
    """研报引用匹配：模型只判断「这句话在说哪个科目、哪个期间」，不判断数值对错。
    本任务不含 evidence，理由与 match_item 同源：一批最多 40 条 claim，
    单个 evidence 字段无法覆盖；且引用位置（研报文件名+章节+原句）本就是程序输入，
    让模型复述没有信息增益。位置由 pipeline._attach_claim_evidence 确定性回填。
    每条 claim 的语义依据由 ClaimMatch.reason 承载。"""
    ok: bool = True
    matches: list[ClaimMatch] = []

class ExplainOut(BaseModel):
    ok: bool
    cause: str                   # 只能从 candidates 里选
    explanation: str = ""
    confidence: float = Field(ge=0, le=1)
    evidence: Evidence

# 报告文字：单独一个类，因为要额外做"数字白名单"校验
class ReportTextOut(BaseModel):
    ok: bool
    text: str


class AuditFinding(BaseModel):
    """语义审计的一条结论：模型指出"这句话在什么地方不自洽"。

    ★ 允许模型写 problem_type / expected_value 这类**自由文本**，因为"比率正负号写反"
      「预测年份指向已结束年度」这类问题无法被确定性规则穷举——必须给模型足够空间表达。
      但它**不产出用于计算的数值**：expected_value 只是提示文案，程序绝不据此改写任何
      数字，也不会把它升级成"异常"。命中的条目一律落「待人工复核（模型语义审计提示）」。
    """
    claim_id: str
    problem_type: str = Field(default="", max_length=60)
    cause: str = Field(default="", max_length=32)
    severity: str = Field(default="MED", max_length=8)
    evidence_quote: str = Field(default="", max_length=300)
    expected_value: str = Field(default="", max_length=120)
    reason: str = Field(default="", max_length=300)
    confidence: float = Field(default=0.0, ge=0, le=1)


class AuditOut(BaseModel):
    ok: bool = True
    findings: list[AuditFinding] = []


SCHEMA_BY_TASK = {
    "match_item":      MatchItemOut,
    "match_claim":     MatchClaimOut,
    "explain_anomaly": ExplainOut,
    "gen_commentary":  ReportTextOut,   # 研报式评价：允许引用程序喂的确定数字，白名单校验
    "audit_claims":    AuditOut,        # 语义审计：不限错误类型的自由审计，结论转人工复核
}

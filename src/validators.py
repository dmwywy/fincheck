# 返回校验模块
# src/validators.py
import re
from decimal import Decimal, InvalidOperation

# gen_commentary 输出允许出现的小节名（与 prompts/gen_commentary.txt 的五小节一致）
COMMENTARY_SECTIONS = ("【核心观点】", "【收入分析】", "【盈利能力】",
                       "【现金流】", "【风险提示】")
# 必含小节：【核心观点】是报告开篇结论，必须由模型生成；缺失即拦截并触发修复重试
REQUIRED_COMMENTARY_SECTIONS = ("【核心观点】",)

def check_enum_whitelist(obj, allowed: dict) -> list[str]:
    """枚举白名单：canonical_item / item_code / cause 只能在候选集合里"""
    errs = []
    if isinstance(obj, dict) and "items" in obj:
        for it in obj["items"]:
            if it.get("canonical_item") and it["canonical_item"] not in allowed["item_codes"]:
                errs.append(f"canonical_item 越界：{it['canonical_item']}")
    if isinstance(obj, dict) and "matches" in obj:
        for m in obj["matches"]:
            # 空字符串 = 模型判"无法判断"，属合法，不判越界
            if m.get("item_code") and m["item_code"] not in allowed["item_codes"]:
                errs.append(f"item_code 越界：{m.get('item_code')}")
    if isinstance(obj, dict) and "cause" in obj:
        if obj.get("cause") and obj["cause"] not in allowed["causes"]:
            errs.append(f"cause 越界：{obj['cause']}")
    return errs

def _canon_num(s: str) -> str | None:
    """把数字串归一化为**数值等价**的标准写法（去逗号、去尾零与小数点），
    使 95.0 与 95、174140000000.00 与 174140000000 视为同一数字。"""
    clean = s.replace(",", "")
    if not clean:
        return None
    try:
        d = Decimal(clean)
    except InvalidOperation:
        return clean
    return format(d.normalize(), "f")


def check_no_amounts(text: str, allowed_numbers: set[str]) -> list[str]:
    """★ 报告文字里出现的每个数字，都必须在程序给定的白名单里（数值等价比较）。

    两个放宽点，都是"写法变体"而非新数值：
     1. 数值等价：95.0 与 95 视为同一个数；
     2. 正负号解耦：白名单里的 -1.20 同时接受正文写成"同比-1.20%"或"同比下降1.20%"
        （正号不在数字里、负号可能被写在词后），避免因写法差异误拦；
        代价是正文把正负号写反不会被拦下——这属于表述错误而非编造数字。
    """
    allowed_canon = set()
    for a in allowed_numbers:
        c = _canon_num(a)
        if c is None:
            continue
        allowed_canon.add(c)
        try:                       # 同名数值的绝对值写法（见放宽点 2）
            allowed_canon.add(format(abs(Decimal(c)), "f"))
        except InvalidOperation:
            pass
    errs = []
    for num in re.findall(r"[+-]?\d[\d,]*\.?\d*", text):
        if _canon_num(num) not in allowed_canon:
            errs.append(f"报告中出现未经程序确认的数字：{num}")
    return errs

def check_commentary_sections(text: str) -> list[str]:
    """gen_commentary 输出的小节名必须落在允许集合内，且必含【核心观点】。

    1. 越界小节名：模型自造了未定义的小节，直接拦；
    2. 必含小节：核心观点是报告开篇结论，必须由模型生成，缺失即拦截并触发修复重试；
       其余小节不强制齐备，避免因少写一节把整条评价判废（缺节由人工复核兜底）。

    本节只做「小节名是否出现」的文本判定，不涉及任何数值运算；
    核心观点里的数字仍由 check_no_amounts 的白名单单独把关。
    """
    errs = []
    names = re.findall(r"【[^】]{1,20}】", text or "")
    for name in names:
        if name not in COMMENTARY_SECTIONS:
            errs.append(f"报告出现未定义的小节名：{name}")
    for need in REQUIRED_COMMENTARY_SECTIONS:
        if need not in names:
            errs.append(f"缺少必写小节：{need}")
    return errs


def check_evidence(ev: dict, chunks: dict[str, str]) -> tuple[bool, str]:
    """证据校验：chunk_id 必须在本次输入内，quote 必须真实存在于该片段"""
    cid, quote = ev.get("chunk_id"), ev.get("quote", "")
    if not cid:
        return False, "evidence.chunk_id 为空"
    if cid not in chunks:
        return False, f"chunk_id 不在本次输入中（疑似编造）：{cid}"
    if quote and quote not in chunks[cid]:
        return False, "原文片段在该片段中不存在（疑似编造）"
    return True, ""
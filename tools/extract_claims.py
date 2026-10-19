# tools/extract_claims.py
"""研报 docx → 句子级 claims（供 match_claim 任务使用）。

职责边界：
  本程序只做"把研报切成可核查的句子/表行"这件确定性的事——切句、**定位**、抽出数字。
  **不判断这句话对不对**，也不把它对应到哪个财务科目（那由模型做，见 src/pipeline.run_match_claim）。
  数值比较由规则侧完成。

定位口径（position）：
  - page：**推定页码**（正文口径）。docx 里没有渲染后的页码，只能按显式分页符
    （`<w:br w:type="page"/>`）与分节符（`<w:sectPr>` 的 `<w:type>` 非 continuous 即
    强制换页；缺省也按 nextPage 处理）推定。首块区（报头/标题）记 page=0，
    第一次强制分页之后进入 page=1（=正文首页）。★ 与原 PDF 排版可能有 ±1 页出入，
    报告里一律标注为"推定页码"。
  - section：章节名（核心观点 / 风险提示 …）。
  - para_no：**章节内段号**——章节标题记 0，其后每个非空段落 +1。
  - row_no / caption：表格行的行号（1 基）与该表标题。

用法：
    python tools/extract_claims.py --docx <研报.docx> --tag 2024 --out 研报claims_2024
"""
import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 章节标题（作为 soft 分节，便于报告里定位）
SECTION_RE = re.compile(r"^(核心观点|盈利预测与投资建议|投资建议|风险提示|盈利预测)$")
# 表标题（如「表1：盈利预测调整表」）
CAPTION_RE = re.compile(r"^表\s*\d+")
# 行标签里的括号单位（如「净利润(千万元)」「营业收入（百万元）」「每股收益（元）」）
UNIT_LABEL_RE = re.compile(r"[（(][^）)]*元[）)]")

# 财务数字：金额、百分比、倍数、吨
FIG_RE = re.compile(
    r"-?\d[\d,]*(?:\.\d+)?\s*(?:亿元|万元|百万元|万吨|元|%|pcts|pct|倍|吨)")
# 断句：中文句末标点；分号也断开（研报里分号常并列多个独立数据）
SENT_SPLIT_RE = re.compile(r"(?<=[。！？；])")
YEAR_TOKEN_RE = re.compile(r"20\d{2}")
# 明显不是"财报数据引用"的句子：报头、署名、行情、页脚来源等。
# ★ 刻意不收「评级/买入/增持/优于大市/中性」：研报"盈利预测与投资建议"段的结尾常带
#   "维持'优于大市'评级"，按旧口径整段丢弃，会把该段里的预测数字一起漏掉。
NOISE_RE = re.compile(r"证券研究报告|分析师|联系人|收盘价|最高价|成交额|@|\.com|资料来源")

# 按文档顺序切块：表格块优先，避免单元格里的 <w:p> 被单独匹配成段落
BLOCK_RE = re.compile(r"(<w:tbl>.*?</w:tbl>)|(<w:p[ >].*?</w:p>)", re.S)
TR_RE = re.compile(r"<w:tr[ >].*?</w:tr>", re.S)
TC_RE = re.compile(r"<w:tc>.*?</w:tc>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
SECTPR_RE = re.compile(r"<w:sectPr\b.*?</w:sectPr>", re.S)
CONTINUOUS_RE = re.compile(r'<w:type\s+w:val="continuous"', re.I)
PAGE_BR_RE = re.compile(r'<w:br\s+w:type="page"')


def _text(frag: str) -> str:
    return TAG_RE.sub("", frag).strip()


def _forced_page_breaks(frag: str) -> int:
    """块内强制分页次数：显式分页符 + 非 continuous 的分节符（缺省按 nextPage）。"""
    n = len(PAGE_BR_RE.findall(frag))
    for s in SECTPR_RE.findall(frag):
        if not CONTINUOUS_RE.search(s):
            n += 1
    return n


def _table_rows(frag: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in TR_RE.findall(frag):
        cells = [_text(tc) for tc in TC_RE.findall(tr)]
        if any(cells):
            rows.append(cells)
    return rows


def read_blocks(path: Path) -> list[tuple[str, object, int]]:
    """按文档顺序返回 [(kind, payload, page)]；kind: 'p'(段落文本) / 't'(表格行列表)。

    空段落在旧实现里被 read_docx 丢掉（不进 paras，故 `para` 索引不受影响）；
    这里同样不入块，但它的**分页贡献照常计入**——否则页码会算少。
    """
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")
    blocks: list[tuple[str, object, int]] = []
    page = 0
    for m in BLOCK_RE.finditer(xml):
        frag = m.group(0)
        if m.group(1):                       # 表格块
            rows = _table_rows(frag)
            if rows:
                blocks.append(("t", rows, page))
        else:                                # 段落块
            t = _text(frag)
            if t:
                blocks.append(("p", t, page))
        page += _forced_page_breaks(frag)    # ★ 先给本块赋页码，再推进
    return blocks


def extract(path: Path, tag: str, tables_as_claims: bool = True) -> list[dict]:
    blocks = read_blocks(path)
    claims: list[dict] = []
    src_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    section, para_no = "", None
    n = 0                                    # 句子级全局计数器（TEXT claim_id 用）
    pi = ti = 0                              # 段落索引 / 表格索引（与原实现语义一致）
    prev_para: str | None = None             # 上一段落文本（供表标题回溯）

    for kind, payload, page in blocks:
        if kind == "t":
            rows: list[list[str]] = payload
            first = rows[0][0] if rows[0] else ""
            caption = (first if CAPTION_RE.match(first)
                       else prev_para if prev_para and CAPTION_RE.match(prev_para)
                       else None)
            for ri, cells in enumerate(rows):
                line = " | ".join(cells)
                figs = FIG_RE.findall(line)
                # 年份表头行（如「盈利预测和财务指标 | 2024 | 2025 | 2026E…」）：只有年份、
                # 没有带单位的数值。单列一类，供"研报内一致性"检测器读列头预测年份。
                if len(figs) < 2 and len(YEAR_TOKEN_RE.findall(line)) >= 3:
                    claims.append({
                        "claim_id": f"r{tag}_t{ti:02d}_{ri:02d}",
                        "source": path.name, "source_hash": src_hash,
                        "section": f"表{ti + 1}", "para": None, "para_no": None,
                        "page": page, "row_no": ri + 1, "caption": caption,
                        "kind": "TABLE_HEADER", "sentence": line,
                        "figures": YEAR_TOKEN_RE.findall(line),
                    })
                    continue
                if len(figs) < 2:
                    # 行标签自带单位、行内数值却是无单位裸数字（如「净利润(千万元) |
                    # 74,734 | 86,228 | …」）：数值无可比对象，但**单位口径**必须与财报
                    # 一致。单独成一类，只供单位一致性检测器（RC04）读取，不喂给模型。
                    label = cells[0] if cells else ""
                    if UNIT_LABEL_RE.search(label):
                        claims.append({
                            "claim_id": f"r{tag}_t{ti:02d}_{ri:02d}",
                            "source": path.name, "source_hash": src_hash,
                            "section": f"表{ti + 1}", "para": None, "para_no": None,
                            "page": page, "row_no": ri + 1, "caption": caption,
                            "kind": "TABLE_LABEL", "sentence": line, "figures": [],
                        })
                    continue        # 至少两个数字才值得核查，滤掉表头与说明行
                n += 1
                claims.append({
                    "claim_id": f"r{tag}_t{ti:02d}_{ri:02d}",
                    "source": path.name, "source_hash": src_hash,
                    "section": f"表{ti + 1}", "para": None, "para_no": None,
                    "page": page, "row_no": ri + 1, "caption": caption,
                    "kind": "TABLE_ROW", "sentence": line, "figures": figs,
                })
            ti += 1
            prev_para = None
            continue

        para: str = payload
        cur_pi = pi
        pi += 1
        prev_para = para
        if SECTION_RE.match(para):
            section, para_no = para, 0
            continue
        if section:
            para_no = (para_no or 0) + 1     # 章节内每个非空段落 +1（标题已记 0）
        if NOISE_RE.search(para):
            continue
        for sent in SENT_SPLIT_RE.split(para):
            sent = sent.strip()
            if len(sent) < 8:
                continue
            figs = FIG_RE.findall(sent)
            if not figs:
                continue
            n += 1
            claims.append({
                "claim_id": f"r{tag}_p{cur_pi:02d}_{n:03d}",
                "source": path.name, "source_hash": src_hash,
                "section": section, "para": cur_pi, "para_no": para_no,
                "page": page, "kind": "TEXT", "sentence": sent, "figures": figs,
            })
    return claims


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docx", required=True)
    ap.add_argument("--tag", required=True, help="期间标签，如 2024")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    path = Path(args.docx)
    claims = extract(path, args.tag)

    out_dir = ROOT / "data" / "demo"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{args.out}.json").write_text(
        json.dumps(claims, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _n(kind: str) -> int:
        return sum(1 for c in claims if c["kind"] == kind)

    print(f"抽取 {len(claims)} 条 claim（正文 {_n('TEXT')} / 表行 {_n('TABLE_ROW')}"
          f" / 表头 {_n('TABLE_HEADER')} / 行标签 {_n('TABLE_LABEL')}）"
          f" -> data/demo/{args.out}.json")


if __name__ == "__main__":
    main()

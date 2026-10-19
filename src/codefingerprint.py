# src/codefingerprint.py
"""代码指纹：判断某次运行的产物是否由**当前**源码产出。

为什么需要它：网页端在同进程内 `import src.pipeline / src.research_check`，
模块一旦进了 sys.modules 就常驻内存。代码改了但服务没重启时，新上传的材料
仍由**旧模块**计算，产物与源码版本不一致——使用者会以为"改了没用"。

用指纹把这件事变成**可见且可自愈**的：
  - pipeline 每次跑完把源码指纹写进 runs/<run_id>/code_fingerprint.json；
  - 报告/网页读取产物前比对指纹，不一致就用当前代码重算确定性部分并给出告警。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_FILENAME = "code_fingerprint.json"


def _source_files() -> list[Path]:
    """参与指纹的源文件：src/*.py。按文件名排序，保证同一份代码算出同一指纹。"""
    return sorted((ROOT / "src").glob("*.py"))


def fingerprint_files() -> list[str]:
    """参与指纹的文件名列表（供报告列出"指纹覆盖范围"）。"""
    return [p.name for p in _source_files()]


def source_fingerprint() -> str:
    """当前 src/*.py 的内容指纹（sha256）。文件名与内容一并入哈希，防止改名绕过。"""
    h = hashlib.sha256()
    for p in _source_files():
        h.update(p.name.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def fingerprint_path(run_id: str) -> Path:
    return ROOT / "runs" / run_id / _FILENAME


def write_fingerprint(run_id: str) -> Path:
    """把当前源码指纹写入本次运行目录。"""
    p = fingerprint_path(run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"fingerprint": source_fingerprint(), "files": fingerprint_files()},
        ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return p


def read_fingerprint(run_id: str) -> str | None:
    """读本次运行记录的源码指纹；没有该文件（旧产物）返回 None。"""
    p = fingerprint_path(run_id)
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("fingerprint")
    except (OSError, json.JSONDecodeError):
        return None


def is_stale(run_id: str) -> bool:
    """产物是否由**非当前**源码产出。无指纹记录（旧产物）时无法判断，返回 False。"""
    stored = read_fingerprint(run_id)
    if stored is None:
        return False
    return stored != source_fingerprint()

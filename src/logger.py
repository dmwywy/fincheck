# src/logger.py
"""统一的 JSONL 事件记录。一行一个事件，便于机器比对与追溯。
注意：绝不写入凭据、绝不写入财报正文。"""
import json, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def log_event(run_id: str, event: str, **fields) -> None:
    p = ROOT / "runs" / run_id / "logs.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"), "event": event}
    rec.update(fields)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
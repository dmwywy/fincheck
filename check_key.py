# check_key.py  诊断：Python 实际读到的是哪个 Key、来自哪里
import os
from pathlib import Path
from dotenv import load_dotenv

def mask(k: str) -> str:
    return f"{k[:6]}...{k[-4:]}（长度 {len(k)}）" if k else "(空)"

ROOT = Path(__file__).resolve().parent
env_file = ROOT / ".env"

print("① .env 路径  :", env_file)
print("   是否存在  :", env_file.exists())

file_val = None
if env_file.exists():
    for i, line in enumerate(env_file.read_text(encoding="utf-8-sig").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        if k.strip() == "DEEPSEEK_API_KEY" and v.strip():
            file_val = v.strip().strip('"').strip("'")
            print(f"② .env 第 {i} 行 :", mask(file_val))
            if '"' in v or "'" in v:
                print("   ⚠ 值里带了引号，Key 会变成无效字符串")
            if v != v.strip():
                print("   ⚠ 值前后有空格")
            break
if file_val is None:
    print("② .env 中没读到 DEEPSEEK_API_KEY")
    print("   ⚠ 若你确实写了，多半是文件带了 BOM（用记事本另存为「UTF-8」而非「UTF-8 带 BOM」）")

sys_before = os.environ.get("DEEPSEEK_API_KEY", "")
print("③ 系统环境变量 :", mask(sys_before), "（非空时会盖过 .env，因为 load_dotenv 默认不覆盖）")

load_dotenv(env_file, override=True)
effective = os.getenv("DEEPSEEK_API_KEY", "")
print("④ 代码实际使用 :", mask(effective))

if effective and effective == file_val:
    print("⑤ 结论 : 生效值来自 .env，与文件一致 → Key 本身无效（见下）")
elif sys_before and sys_before != file_val:
    print("⑤ 结论 : 被【系统环境变量】覆盖了！清掉它，或改用 override=True")
else:
    print("⑤ 结论 : 生效值与 .env 不一致，请检查 .env 是否保存成功")
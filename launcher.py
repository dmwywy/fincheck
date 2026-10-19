# -*- coding: utf-8 -*-
"""交互式启动器：环境自检 → 选择命令行 / 网页 → 检查 DeepSeek 配置 → 输入材料文件夹 → 运行。

三种入口等价：
    双击仓库根目录的 `run.bat`（调用本脚本）
    或在项目根目录执行：python launcher.py
    或双击 `fincheck-launcher.exe`（PyInstaller 打包的外壳；exe 只做引导，核查仍用旁边的源码跑）

被冻结成 exe 时（sys.frozen），本进程内部没有 Python 解释器，因此必须自行定位一个
可用的解释器；本机完全没有 Python 时可自动下载官方内嵌运行时到 `.runtime/`。

环境诊断（把整屏输出发给他人即可定位问题）：
    python launcher.py --diagnose     （fincheck-launcher.exe --diagnose 亦可）
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
# exe 与源码版都以「文件所在目录」为项目根：exe 需与 fincheck/ src/ app.py 放在一起
ROOT = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent

REQ = ROOT / "requirements.txt"
ENV_FILE = ROOT / ".env"
RUNTIME = ROOT / ".runtime"          # 自动下载的内嵌 Python 落在这里
RUN_ID = "reproduce"
# 启动标记 + 崩溃堆栈落盘：控制台完全没有输出时，这是唯一线索。
# 有"启动"行 = Python 跑起来了（问题在输出）；文件为空/不存在 = 根本没轮到 Python
# （多半被安全软件静默拦截或 onefile 解包失败）。
LOG_FILE = ROOT / "launcher_run.log"

# 依赖导入名（requirements.txt 里对应的包：openai / pydantic / PyYAML /
# pdfplumber / reportlab / openpyxl / pandas / streamlit）
NEEDED = ["openai", "pydantic", "yaml", "pdfplumber",
          "reportlab", "openpyxl", "pandas", "streamlit"]

PY_MIN = (3, 10)
PY_WARN_MAX = (3, 13)                # 3.13+ 部分依赖可能没有预编译轮子，只提醒不阻断
EMBED_URL = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
GETPIP_URL = "https://bootstrap.pypa.io/get-pip.py"
DOWNLOAD_PAGE = "https://www.python.org/downloads/windows/"

# 材料文件夹里可识别的后缀（与 src/ingest.py 的 SUPPORTED 保持一致）
MATERIAL_SUFFIXES = (".pdf", ".docx")

# 启动前必须与 launcher 同目录存在的关键路径：缺任何一项都说明「运行包不完整 / exe 放错位置」，
# 继续跑只会得到一堆找不到文件或 import 失败，因此提前拦下并给出明确处理办法。
TREE_REQUIRED = [
    ("requirements.txt", "依赖清单"),
    ("app.py", "网页界面入口"),
    ("fincheck/__main__.py", "命令行入口"),
    ("src", "核心代码目录"),
    ("configs/demo.yaml", "默认运行配置"),
]
TREE_OPTIONAL = [("data/demo", "自带 demo 数据（回车跑 demo 时才需要）")]

PY: list[str] = []                   # 解析出的解释器命令，如 ["python"] 或 ["C:/.../python.exe"]


def line(text: str = "") -> None:
    print(text, flush=True)


def _log(text: str, reset: bool = False) -> None:
    """把启动标记 / 崩溃堆栈写进 launcher_run.log（写不进去也不影响主流程）。"""
    try:
        with LOG_FILE.open("w" if reset else "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        pass


def step(title: str) -> None:
    line()
    line("=" * 56)
    line(title)
    line("=" * 56)


def ask(prompt: str, default: str = "") -> str:
    """读取输入；管道输入到末尾（EOF）时回落到默认值，便于无人值守测试。"""
    try:
        got = input(prompt).strip()
    except EOFError:
        got = ""
    return got or default


def clean_path(raw: str) -> str:
    """去掉拖拽文件夹带来的成对引号与末尾斜杠。"""
    return raw.strip().strip('"').strip("'").rstrip("\\/")


def mask(key: str) -> str:
    return key[:6] + "***" + key[-3:] if len(key) > 12 else "***"


def _run(cmd: list[str], **kw) -> int:
    # 先把自身缓冲刷出去，否则子进程输出会插到提示语前面（管道/重定向时尤其明显）
    sys.stdout.flush()
    return subprocess.call(cmd, cwd=ROOT, **kw)


def _usable(cmd: list[str]) -> bool:
    """解释器是否可用且版本达标。"""
    try:
        probe = subprocess.run(
            cmd + ["-c", f"import sys;assert sys.version_info[:2]>={PY_MIN}"],
            capture_output=True, timeout=60)
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _version(cmd: list[str]) -> str:
    try:
        probe = subprocess.run(cmd + ["-c", "import sys;print(sys.version.split()[0])"],
                               capture_output=True, text=True, timeout=60)
        return probe.stdout.strip() or "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


def _ver_tuple(cmd: list[str]) -> tuple[int, int, int] | None:
    """取解释器的版本三元组；失败返回 None。"""
    try:
        probe = subprocess.run(
            cmd + ["-c", "import sys;print('%d.%d.%d' % sys.version_info[:3])"],
            capture_output=True, text=True, timeout=60)
        if probe.returncode != 0:
            return None
        nums = tuple(int(p) for p in probe.stdout.strip().split(".")[:3])
        return nums if len(nums) == 3 else None          # type: ignore[return-value]
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _missing(cmd: list[str], mods: list[str]) -> list[str]:
    """逐个 import 探测，返回无法导入的模块名。"""
    absent = []
    for mod in mods:
        probe = subprocess.run(cmd + ["-c", f"import {mod}"], capture_output=True)
        if probe.returncode != 0:
            absent.append(mod)
    return absent


def _free_port(start: int, tries: int = 20) -> int:
    """从 start 起找一个可绑定的本地端口（8501 被占用时自动后移）。

    注意：Windows 上 SO_REUSEADDR 允许绑定已被占用的地址，会让探测失效，
    因此这里不设该选项，只依赖 bind 是否抛 OSError 来判断。
    """
    import socket
    for port in range(start, start + tries):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def _net_ok(host: str, port: int = 443, timeout: float = 5.0) -> bool:
    """TCP 可达性探测（只建连接、不下载、不调模型），用于暴露网络/代理问题。"""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def count_materials(folder: Path) -> int:
    """统计文件夹（含子目录）里的 PDF / DOCX 数量。"""
    return sum(1 for p in folder.rglob("*")
               if p.is_file() and p.suffix.lower() in MATERIAL_SUFFIXES)


# ---------------------------------------------------------------- 0. 定位 / 下载解释器

def find_python() -> list[str] | None:
    """按 内置运行时 → PATH → py 启动器 → 常见安装路径 → 项目 .venv 的顺序探测。"""
    candidates: list[list[str]] = []
    embedded = RUNTIME / "python.exe"
    if embedded.exists():
        candidates.append([str(embedded)])
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            candidates.append([found])
    if shutil.which("py"):
        candidates.append(["py", "-3"])
    for pattern in (r"C:\Python3*\python.exe",
                    r"C:\Program Files\Python3*\python.exe",
                    os.path.join(os.environ.get("LOCALAPPDATA", r"C:\Dummy"),
                                 "Programs", "Python", "Python3*", "python.exe")):
        for hit in sorted(glob.glob(pattern), reverse=True):
            candidates.append([hit])
    for parent in (ROOT, ROOT.parent):
        for rel in (Path(".venv") / "Scripts" / "python.exe", Path("venv") / "Scripts" / "python.exe"):
            p = parent / rel
            if p.exists():
                candidates.append([str(p)])

    for cmd in candidates:
        if _usable(cmd):
            return cmd
    return None


def download_runtime() -> list[str] | None:
    """下载官方内嵌 Python 到 `.runtime/`，并装好 pip（无需管理员权限、不改系统环境）。"""
    RUNTIME.mkdir(parents=True, exist_ok=True)
    zip_path = RUNTIME / "python-embed.zip"
    line(f"  正在下载官方内嵌 Python 运行时（约 11 MB）……")
    try:
        urllib.request.urlretrieve(EMBED_URL, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(RUNTIME)
        zip_path.unlink()
    except Exception as e:                                   # noqa: BLE001
        line(f"  [!!] 下载或解压失败：{type(e).__name__}: {e}")
        return None

    # 内嵌版默认不给 pip：打开 ._pth 里的 import site，再跑 get-pip.py
    for pth in RUNTIME.glob("python*._pth"):
        text = pth.read_text(encoding="utf-8")
        pth.write_text(text.replace("#import site", "import site"), encoding="utf-8")
    getpip = RUNTIME / "get-pip.py"
    line("  正在安装 pip……")
    try:
        urllib.request.urlretrieve(GETPIP_URL, getpip)
        if _run([str(RUNTIME / "python.exe"), str(getpip)]) != 0:
            line("  [!!] pip 安装失败")
            return None
    except Exception as e:                                   # noqa: BLE001
        line(f"  [!!] pip 下载或安装失败：{type(e).__name__}: {e}")
        return None
    finally:
        getpip.unlink(missing_ok=True)

    cmd = [str(RUNTIME / "python.exe")]
    return cmd if _usable(cmd) else None


# ---------------------------------------------------------------- 1. 环境自检

def check_tree() -> bool:
    """运行包完整性：关键文件必须与 launcher 同目录。

    exe 与源码模式都以「文件所在目录」为项目根，把 fincheck-launcher.exe 单独拷走、
    或只解压出部分文件，都会让后续步骤全线失败；这里提前拦下并说明处理办法。
    """
    step("[1/4] 环境自检 · 运行包完整性")
    missing = [f"{p}（{why}）" for p, why in TREE_REQUIRED if not (ROOT / p).exists()]
    if not missing:
        line(f"  [OK] 关键文件齐备（根目录：{ROOT}）")
        for p, why in TREE_OPTIONAL:
            if not (ROOT / p).exists():
                line(f"  [注意] 缺少 {p}（{why}）；自备材料文件夹时可忽略。")
        return True

    line(f"  [!!] 运行包不完整，缺少以下关键文件/目录（当前根目录：{ROOT}）")
    for item in missing:
        line(f"        - {item}")
    if FROZEN:
        line("  [处理] fincheck-launcher.exe 必须与 fincheck/、src/、app.py、requirements.txt 位于")
        line("         同一个项目根目录；请整包拷贝，不要把 exe 单独拷出来运行。")
    else:
        line("  [处理] 请在项目根目录（含 fincheck/、src/、app.py 的那一层）运行 launcher.py。")
    return False


def check_folder(raw: str) -> str | None:
    """材料文件夹预检：不存在 / 不是目录 / 没有可识别文件时返回 None。"""
    path = Path(clean_path(raw))
    if not path.exists():
        line(f"  [!!] 文件夹不存在：{path}")
        return None
    if not path.is_dir():
        line(f"  [!!] 这个路径不是文件夹：{path}")
        return None
    count = count_materials(path)
    if count == 0:
        line(f"  [!!] 文件夹里没有 .pdf / .docx 文件：{path}")
        line("       支持多层子目录；请确认路径，或直接回车改用仓库自带 demo 数据。")
        return None
    line(f"  [OK] 找到 {count} 份 PDF / DOCX 材料")
    return str(path)


def check_python() -> list[str] | None:
    step("[1/4] 环境自检 · Python 解释器")
    cmd = find_python()
    if cmd:
        line(f"  [OK] 已找到 Python {_version(cmd)}（{cmd[0]}）")
        ver = _ver_tuple(cmd)
        if ver and ver[:2] >= PY_WARN_MAX:
            line(f"  [注意] Python {ver[0]}.{ver[1]} 较新，部分依赖可能没有预编译轮子；"
                 "若下一步安装失败，请改用 3.11（如 3.11.9）。")
        return cmd

    line("  [!!] 本机未检测到可用的 Python（需 3.10+）。")
    line("  1) 自动下载官方内嵌运行时到项目 .runtime/（约 11 MB，不改系统环境）")
    line("  2) 打开官方下载页，自己安装（记得勾选 “Add Python to PATH”）")
    if ask("  请输入 1 或 2（默认 1）：", "1") == "2":
        print(f"  下载页：{DOWNLOAD_PAGE}")
        if sys.platform.startswith("win"):
            try:
                os.startfile(DOWNLOAD_PAGE)                 # noqa: S606
            except OSError:
                pass
        return None
    cmd = download_runtime()
    if cmd:
        line(f"  [OK] 运行时就绪：{cmd[0]}")
    return cmd


def check_deps(cmd: list[str]) -> bool:
    step("[1/4] 环境自检 · 依赖包")
    line(f"  Python  {_version(cmd)}  ({cmd[0]})")
    missing = _missing(cmd, NEEDED)
    if not missing:
        line("  [OK] 依赖齐备，无需安装")
        return True

    line(f"  缺少依赖：{', '.join(missing)}")
    if not REQ.exists():
        line(f"  [!!] 找不到依赖清单 {REQ.name}，无法自动安装。请检查运行包是否完整。")
        return False

    line("  正在自动安装：python -m pip install -r requirements.txt")
    rc = _run(cmd + ["-m", "pip", "install", "-r", str(REQ)])
    if rc != 0:
        line(f"  [!!] 自动安装失败（退出码 {rc}）。")
        if not _net_ok("pypi.org"):
            line("  [诊断] 探测 pypi.org:443 不通 —— 大概率是网络 / 代理 / 白名单问题。")
        line("         可手工执行：python -m pip install -r requirements.txt 查看具体报错。")
        return False

    # ★ 安装后必须复验：pip 可能"部分成功"，或包装上了但 import 仍失败（版本/轮子问题）
    still = _missing(cmd, NEEDED)
    if still:
        line(f"  [!!] 安装后仍无法导入：{', '.join(still)}")
        line("         常见原因：Python 版本过新导致无可用轮子，或网络中断装到一半。")
        line("         可手工执行：python -m pip install -r requirements.txt 查看具体报错。")
        return False
    line("  [OK] 依赖安装完成并复验通过")
    return True


# ---------------------------------------------------------------- 2. 运行方式

def choose_mode() -> str:
    step("[2/4] 选择运行方式")
    line("  1) 命令行运行（跑完直接生成 PDF / Excel / 网页三端报告）")
    line("  2) 网页运行（启动浏览器界面，可上传文件、在线填 Key）")
    return ask("  请输入 1 或 2（默认 1）：", "1")


# ---------------------------------------------------------------- 3. DeepSeek 配置

def read_key() -> str:
    """按 .env → 环境变量的顺序读取 Key；不打印明文。"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            name, sep, value = raw.partition("=")
            if sep and name.strip() == "DEEPSEEK_API_KEY":
                return value.strip().strip('"').strip("'")
    return ""


def write_key(key: str) -> None:
    """把 Key 写入 .env（保留其它行）；.env 已在 .gitignore，不入库。"""
    lines = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
    out, replaced = [], False
    for raw in lines:
        if raw.partition("=")[0].strip() == "DEEPSEEK_API_KEY":
            out.append(f"DEEPSEEK_API_KEY={key}")
            replaced = True
        else:
            out.append(raw)
    if not replaced:
        out.append(f"DEEPSEEK_API_KEY={key}")
    ENV_FILE.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    line(f"  已写入 {ENV_FILE.name}（该文件已加入 .gitignore，不会提交）")


def ensure_key() -> bool:
    """返回是否为在线模式（True=在线、有 Key；False=离线）。"""
    step("[3/4] 检查 DeepSeek 配置")
    key = read_key()
    if key:
        line(f"  [OK] 已检测到 DEEPSEEK_API_KEY（{mask(key)}）")
        line("  1) 在线模式（真实调用 DeepSeek API）")
        line("  2) 离线模式（只读本地缓存，不联网，评审复现推荐）")
        return ask("  请输入 1 或 2（默认 1）：", "1") != "2"

    line("  未检测到 DEEPSEEK_API_KEY。")
    line("  1) 现在输入 Key（在线模式：真实调用 DeepSeek API）")
    line("  2) 跳过（离线模式：只读本地缓存，不联网、不需要 Key）")
    choice = ask("  请输入 1 或 2（默认 2）：", "2")

    if choice == "1":
        key = ask("  请输入 DeepSeek API Key（形如 sk-...）：")
        if key:
            write_key(key)
            line("  [OK] 已配置 Key，使用【在线模式】")
            return True
        line("  [!!] 未输入有效 Key，自动转为【离线模式】")
    else:
        line("  已选择跳过，使用【离线模式】")
    return False


# ---------------------------------------------------------------- 4. 运行

def run_cli(online: bool) -> int:
    folder = ""
    while True:
        raw = ask("  请输入材料文件夹路径（直接回车 = 使用仓库自带 demo 数据）：")
        if not raw.strip():
            break
        got = check_folder(raw)
        if got is not None:
            folder = got
            break
        line("  请重新输入，或直接回车改用仓库自带 demo 数据。")
    line()

    cmd = PY + ["-m", "fincheck"]
    if folder:
        line(f"  材料文件夹：{folder}")
        cmd += ["ingest", "--from", folder, "--run-id", RUN_ID]
    else:
        line("  未输入路径，使用仓库自带 demo 数据")
        cmd += ["run", "--config", "configs/demo.yaml", "--run-id", RUN_ID]
    if not online:
        cmd.append("--offline")
    line(f"  运行模式：{'在线' if online else '离线'}")
    line()

    rc = _run(cmd)
    if rc != 0:
        line("  [!!] 流程执行失败，请按上面的提示处理后重跑。")
        return rc

    base = ROOT / "runs" / RUN_ID
    line()
    line("  产物目录：runs/%s/" % RUN_ID)
    for name in ("report.pdf", "report.xlsx", "report.html"):
        target = base / name
        if target.exists():
            line(f"    {target.relative_to(ROOT)}")
    line()
    line("  全部完成。")
    return 0


def run_web() -> int:
    port = _free_port(8501)
    if port != 8501:
        line(f"  [提示] 端口 8501 已被占用，本次改用 {port}")
    line(f"  正在启动网页界面，浏览器会自动打开 http://localhost:{port}")
    line("  关闭网页服务：在本窗口按 Ctrl+C")
    line()
    return _run(PY + ["-m", "streamlit", "run", str(ROOT / "app.py"),
                      "--server.port", str(port)])


def _setup_console() -> None:
    """把控制台统一成 UTF-8，避免中文输出乱码或直接抛错。

    两种模式都要做：
      - 冻结成 exe 时，外壳进程与它拉起的 python 子进程必须用同一编码写同一个控制台；
      - 源码模式（python launcher.py）下，控制台代码页可能是 437/936/950，
        直接 print 中文会乱码，编码严格时甚至抛 UnicodeEncodeError 中断流程。
    统一控制台代码页、自身三条流与子进程编码（PYTHONIOENCODING）为 UTF-8。
    """
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:                                    # noqa: BLE001
            pass
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def diagnose() -> int:
    """--diagnose：一次性环境体检，用于把问题整屏输出给他人排查。"""
    line()
    line("  财报一致性量化核查智能体 · 环境诊断（--diagnose）")
    line(f"  项目根目录：{ROOT}")
    line(f"  运行方式：{Path(sys.executable).name + '（冻结）' if FROZEN else 'python launcher.py（源码）'}")

    tree_ok = check_tree()

    step("[2] Python 解释器")
    cmd = find_python()
    if cmd:
        line(f"  [OK] {_version(cmd)}（{cmd[0]}）")
        ver = _ver_tuple(cmd)
        if ver and ver[:2] >= PY_WARN_MAX:
            line(f"  [注意] Python {ver[0]}.{ver[1]} 较新，可能存在轮子缺失问题。")
    else:
        line("  [!!] 未找到可用 Python（需 3.10+）；首次运行时可自动下载内嵌运行时。")

    step("[3] 依赖包")
    if cmd:
        absent = _missing(cmd, NEEDED)
        line("  [OK] 8 个依赖全部可导入" if not absent
             else f"  [!!] 无法导入：{', '.join(absent)}")
    else:
        line("  [跳过] 无可用解释器")

    step("[4] 网络可达性（TCP 443）")
    for host in ("pypi.org", "files.pythonhosted.org", "api.deepseek.com"):
        line(f"  {'[OK]' if _net_ok(host) else '[!!]'} {host}")

    step("[5] 网页端口")
    port = _free_port(8501)
    line(f"  [OK] 可用端口 {port}"
         + ("（8501 空闲）" if port == 8501 else f"（8501 被占用，将改用 {port}）"))

    step("[6] 配置与数据")
    demo = ROOT / "data" / "demo"
    line(f"  {'[OK]' if demo.exists() else '[!!]'} 自带 demo 数据：data/demo")
    line(f"  {'[OK]' if (ROOT / 'configs' / 'demo.yaml').exists() else '[!!]'} "
         "默认配置：configs/demo.yaml")

    step("诊断结束")
    line("  结论：" + ("环境具备运行条件（离线模式可直接复现）"
                      if tree_ok and cmd else "存在阻断项，见上面 [!!] 行"))
    line("  把以上整屏输出发给开发者即可定位问题。")
    line(f"  启动日志：{LOG_FILE.name}（若控制台一行都没有输出，请看这个文件的首行）")
    return 0


def main() -> int:
    global PY
    _setup_console()
    _log(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 mode="
         f"{'exe' if FROZEN else 'src'} root={ROOT} argv={sys.argv[1:]}", reset=True)
    if "--diagnose" in sys.argv or "-d" in sys.argv:
        return diagnose()

    line()
    line("  财报一致性量化核查智能体 · 交互式启动器")
    line("  （离线模式不需要网络与 API Key，可直接用于评审复现）")

    if not check_tree():
        line()
        line("  已退出：请按上面的提示修复运行包后重试。")
        return 1

    found = check_python()
    if found is None:
        line()
        line("  已退出：请安装 Python 3.10+ 后重新运行本程序。")
        return 1
    PY[:] = found

    if not check_deps(PY):
        return 1

    if choose_mode() == "2":
        return run_web()

    online = ensure_key()
    step("[4/4] 运行核查")
    return run_cli(online)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        line("\n  已中断。")
        raise SystemExit(130)
    except SystemExit:
        raise                                               # 正常/自定退出码，不是崩溃
    except BaseException:                                    # noqa: BLE001
        # 控制台可能被吞掉输出；堆栈必须落盘，否则这类失败完全无法排查
        import traceback
        text = traceback.format_exc()
        _log(text)
        try:
            line(f"  [!!] 启动器异常退出，完整堆栈已写入 {LOG_FILE.name}：")
            line(text)
        except Exception:                                    # noqa: BLE001
            pass
        raise SystemExit(1)

# FinCheck · 财报一致性核查智能体

**FinCheck** 面向上市公司定期报告，把「研报写的」与「财报实际的」摆在同一条账上逐条比对。
系统读取本地财报与研究报告草稿，由程序完成数据解析、单位换算、规则校验与指数计算，
由大语言模型完成科目语义匹配与异常原因解释，最终输出结构化核查结果与核查报告。

- 选题方向：数据结构化提取、上市公司财务报告分析、研究报告纠错核查
- 数据环境：半封闭环境，仅访问本地材料与大模型接口，不启用网页搜索与外部数据库
- 交付形态：命令行程序 + 网页界面 + 结构化结果文件（JSON / Excel / 报告）

---

## 一、环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11、macOS、Linux |
| Python | **3.11 或 3.12**（不建议 3.13 及以上，部分库缺少预编译安装包） |
| 网络 | 在线模式需可访问 `api.deepseek.com`；离线模式无需网络 |
| 磁盘 | 建议预留 500 MB（含依赖与样例数据） |

> **先确认本机有没有 Python**——第一、二章都建立在这个前提上，它也是最容易卡住的一步：
>
> - **PowerShell**：用 `where.exe python` 或 `Get-Command python -All`（`py --list` 列出全部已装版本）。
>   ⚠️ PowerShell 里的 `where` 是 cmdlet `Where-Object` 的**别名**，单独敲 `where python` 会**静默无输出**，
>   这**不代表**没装 Python；只有 `where.exe python` 才等价于 cmd 的行为。
> - **cmd / 终端**：`where python`
> - **没装 Python、版本低于 3.10，或者根本不想折腾环境**：**直接跳到「四之二、交互式启动」**，
>   双击 `run.bat`（PowerShell 里请输入 `.\run.bat`）。启动器会自检解释器，必要时**自动下载官方
>   内嵌运行时到 `.runtime/`** 并自动安装依赖——**第一、二章可以整章跳过**。

安装依赖（仅「手工搭建环境」这条路线需要；一键启动会自动装）：

    python -m pip install -r requirements.txt

Windows 下若使用 PowerShell 激活虚拟环境时报执行策略错误，先执行一次：

    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

---

## 二、初始化（仅首次）

> **前置条件**：本机已安装 Python 3.11 / 3.12（用 `py --list` 确认）。
> 下文的 `py -3.11 -m venv .venv` 只有在 **3.11 运行时已装好**时才有意义；
> 否则会报 `No suitable Python runtime found`——那是「本机没有 3.11」，**不是命令写错了**。
> 本节是「手工搭建环境」路线；只想把流程跑一遍（复核、演示）的话，
> **本节与「安装依赖」都可跳过，直接看「四之二、交互式启动」**。

### 1. 创建虚拟环境

Windows（命令提示符）：

    py -3.11 -m venv .venv
    .venv\Scripts\activate.bat

Windows（PowerShell）：

    py -3.11 -m venv .venv
    .\.venv\Scripts\Activate.ps1

macOS / Linux：

    python3.11 -m venv .venv
    source .venv/bin/activate

激活成功的标志：命令行提示符前出现 `(.venv)`。

### 2. 安装依赖

    python -m pip install -r requirements.txt

无外网环境改用离线安装包：

    python -m pip install --no-index --find-links=offline_wheels -r requirements.txt

### 3. 确认解释器

    where.exe python  # PowerShell 与 cmd 都可用（PowerShell 里 where 是 Where-Object 的别名，见第一章）
    python -V

第一行路径应指向项目内的 `.venv`，版本应为 3.11.x。

---

## 三、配置 API Key

系统仅在**在线模式**下需要 API Key。Key 由使用者自行申请，本仓库不包含任何密钥。

### 方式一：`.env` 文件（推荐）

1. 复制模板：

       copy .env.example .env        # macOS / Linux 用 cp

2. 到 `platform.deepseek.com` 注册并创建 API Key，填入 `.env`：

       DEEPSEEK_API_KEY=sk-你的key
       MODEL_ID=deepseek-flash

### 方式二：环境变量

Windows（命令提示符，仅当前窗口有效）：

    set DEEPSEEK_API_KEY=sk-你的key

macOS / Linux：

    export DEEPSEEK_API_KEY=sk-你的key

### 方式三：运行时检查

    python check_key.py

该脚本只打印 Key 的掩码与来源，不输出明文。

### 注意事项

1. `.env` 已加入 `.gitignore`，**任何情况下不要提交到版本库**。
2. 密钥仅在调用时从环境变量读取，不写入日志、不写入报告、不写入缓存文件。
3. 日志与报告中一律以掩码形式呈现，形如 `sk-abc***xyz`。
4. 若 `check_key.py` 显示"生效值来自 .env 且与文件一致"但仍返回 401，说明 Key 本身无效，
   请在开放平台重新创建，并确认登录的是你自己的账号。

---

## 四、可用模型

| `MODEL_ID` 取值 | 说明 | 建议用途 |
|---|---|---|
| `deepseek-flash` | DeepSeek-V4.1-Flash 的 API 模型标识；适用于受限分类与映射任务（默认） |
| `deepseek-v4-pro` | 能力更强 | 复杂语义判断、异常原因解释 |

代码内置白名单校验：`MODEL_ID` 不在上表范围内时，程序在启动瞬间报错并说明原因，
不会发出任何请求。

---

## 四之二、交互式启动（推荐先跑这一个）

Windows 下**双击仓库根目录的 `run.bat`**（它只是 `python launcher.py` 的薄包装），
或在项目根目录执行：

    python launcher.py

也可以直接双击 **`fincheck-launcher.exe`**（PyInstaller 打包的外壳，约 8.6 MB）。它只做
「检测环境 → 引导 → 拉起源码」，真正的核查逻辑仍用旁边的 Python 源码跑；**本机没有 Python 时，
它会自动下载官方内嵌运行时到 `.runtime/`**（约 11 MB，不改系统环境），所以免装环境也能用。
重新打包：双击 `build_exe.bat`。

之后按提示逐步操作即可，**全程无需改动任何文件**：

1. **环境自检**（四小项）：
   - **运行包完整性**：校验 `fincheck/`、`src/`、`app.py`、`requirements.txt`、`configs/demo.yaml`
     是否与 `launcher.py` / `fincheck-launcher.exe` 同目录；缺任何一项会直接说明「exe 不能单独拷出来运行」
     并停止，避免后续只得到一串找不到文件的报错。
   - **Python 解释器**：定位并校验版本 ≥ 3.10（可识别 Microsoft Store 存根）；3.13+ 给出提醒。
   - **依赖包**：逐个 `import` 探测，缺则自动执行 `pip install -r requirements.txt`，
     **装完再复验一次**；失败会提示是否为网络 / 代理问题并给出手工命令。
   - 若本机没有 Python：`fincheck-launcher.exe` 自动下载内嵌运行时，`run.bat` 提示并打开官方下载页
     （自行安装时勾选 “Add Python to PATH”）。
2. **选择运行方式**：`1` 命令行（跑完直接出 PDF / Excel / 网页三端报告）/ `2` 网页（自动打开浏览器界面）。
3. **检查 DeepSeek 配置**：`.env` 中已有 `DEEPSEEK_API_KEY` 时，让你选在线 / 离线；
   没有时让你输入 Key（写入 `.env`，该文件已 gitignore，不会提交），或直接回车跳过走离线。
   **离线模式不需要网络与 API Key**，是评审复现的默认口径。
4. **输入材料文件夹路径**：可混放年报 / 研究报告的 PDF、DOCX，支持多份，**无需文件名约定**
   （按文件内容识别）；输入后会**先校验**路径是否存在、是否为文件夹、里面有没有 PDF / DOCX，
   不合法会让你重输；**直接回车 = 使用仓库自带 demo 数据**。
5. **运行**：命令行分支复用 `fincheck`（`ingest` / `run` 并自动生成三端报告）；
   网页分支启动 `streamlit run app.py`，**端口 8501 被占用时自动避让并打印真实地址**。

> **环境诊断**：任一入口加 `--diagnose`（如 `fincheck-launcher.exe --diagnose` 或 `python launcher.py --diagnose`）
> 会一次性打印「运行包完整性 / Python / 依赖 / 网络可达性 / 网页端口 / 配置与数据」六项体检结果，
> 并给出结论；**把整屏输出发给他人即可定位问题**，无需手工排查。

> **在 PowerShell 里运行**：PowerShell **不会**从当前目录直接加载脚本，必须加 `.\` 前缀——
> 用 `.\run.bat`、`.\fincheck-launcher.exe --diagnose`；在 cmd 里或直接双击则不需要前缀。

> **若控制台一行输出都没有**：启动器会把「启动标记 + 崩溃堆栈」写入仓库根目录的
> `launcher_run.log`。**该文件里有启动行** → Python 已跑起来（问题只在输出）；
> **文件不存在或为空** → 根本没轮到 Python，常见原因是**安全软件拦截了 PyInstaller 单文件产物**，
> 请把该目录加入信任后重试，或改用源码模式 `python launcher.py --diagnose` 对照。

> **关于 exe**：`fincheck-launcher.exe` 只打包了**启动器外壳**，没有把 Streamlit 应用整体塞进去——
> 那样会有数百 MB、还需处理静态资源钩子，构建脆弱且对评审没有额外加分。大赛要求提交
> **完整源代码 + 依赖文件 + 运行说明**，本仓库三者齐备；exe 只是给「不想敲命令」的使用者
> 多留一个入口，三条入口（`run.bat` / `launcher.py` / `fincheck-launcher.exe`）行为完全一致。
> 文件名带 `fincheck-` 前缀，避免与 PATH 中的同名程序（如 IDE 自带的 `launcher.exe`）冲突。
> 注意：exe 由 `launcher.py` 打包而来，**改过 `launcher.py` 后需重新执行 `build_exe.bat`**。

---

## 五、运行

所有命令均在**项目根目录**执行。

### 1. 最小调用测试

    python test.py

首次运行应输出 `状态： API_OK(try=1)`；再次运行应输出 `状态： CACHE_HIT`。
第二次命中缓存时不发起网络请求，也不产生费用。

### 2. 契约自检

    python testcontract.py

检查六项：模型名称、调用方式、输入格式、返回格式、错误处理、联网边界。

### 3. 字段字典与规则库自检

    python tools\check_fields.py

校验 `rules/fields.yaml` 自身完整性（期间/附注定位）、`rules/rules.yaml` 引用的字段是否都已定义、
规则写法是否合法（`kind` / `requires` 与 `zero_if_absent` 是否自相矛盾），以及规则条数是否在 20–30。

**规则库**：`rules/rules.yaml` 共 **27 条**（另有 C研 的 `RC01`，在 `src/research_check.py`），
五个维度与数据来源：

| 维度 | 条数 | 规则 | 数据来源 |
|---|---|---|---|
| C内 表内勾稽 | 12 | BS01 BS02 IS03–IS07 CF01 CF02 SI01–SI03 | 合并主表、第三节分部表、现金流量表补充资料、合并所有者权益变动表 |
| C间 表间一致 | 7 | LJ01–LJ07 | 主表 ↔ 补充资料 / 所有者权益变动表 |
| C期 跨期衔接 | 6 | XQ_CASH XQ_AR XQ_INV XQ_UNDIST XQ_SURPLUS XQ_EQ_PARENT | 同一时点在本期期初与上期期末的两次出现 |
| C附 主表附注 | 2 | NB01 NB02 | 附注七、1 货币资金；附注七、5 应收账款 |
| C研 研报引用 | 1 | RC01（每条 claim 一例） | 研报原文 vs 财报结构化数据 |

逐条规则的字段需求、容差档位与出处见 `rules/rules.yaml` 注释；字段清单真源见
`rules/fields.yaml`（如需可读版：`python tools/export_fields.py --out <输出路径>`）。

### 4. 返回结构与证据校验

    python -m pytest test_tasks.py -q

用真实模型输出验证 schema 校验路径与证据真实性校验（原独立脚本
`test_schema_roundtrip.py` 的用例已并入 `test_tasks.py`，随全量测试一起跑）。

### 5. 密钥配置诊断

    python check_key.py

### 6. 跑完整流程（主入口）

    python -m fincheck run --config configs/demo.yaml --run-id 20261005_demo

跑完会打印一份运行摘要：

    运行完成  run_id = 20261005_demo
      模型调用     api_ok=11  cache_hit=0  failed=0  合计=11
      科目归一化   API_OK(try=1)  66 条（待复核 0）
      研报引用匹配 API_OK(try=1)  103 条（覆盖 81/81 条 claim，待复核 0）
      C研 数值比对 FAIL=4  PASS=4（待人工复核 95）
      规则核查     78 条  NA=10  PASS=68
      异常解释     4 条  API_OK(try=1)
      一致性指数   CSI=95.0  等级 A（高度一致）
        分项       C内:1.0  C期:1.0  C研:0.5  C间:1.0  C附:1.0
      产物目录     runs/20261005_demo/

**在线模式**需要 `.env` 里的 `DEEPSEEK_API_KEY`；**离线模式**不需要密钥、不联网：

    python -m fincheck run --config configs/demo.yaml --run-id 20261005_demo --offline

离线模式只读 `runs/<run_id>/llm_cache/` 里的缓存。若某条没有缓存，它会记为
`NO_CACHE_OFFLINE` 并转入人工复核，**不会**发起网络请求。

### 7. 文件夹入口（现场测试用）

给一个文件夹，程序自己识别里面是什么材料，解析完直接跑完整流程：

    python -m fincheck ingest --from "D:\评委给的材料" --run-id 20261005_onsite

**不需要任何文件名约定**——识别依据是**文件内容**：

| 内容特征 | 判定 | 处理 |
|---|---|---|
| 含「合并资产负债表 / 合并利润表 / 合并现金流量表」 | 年报 | 抽取结构化字段 |
| 含「核心观点 / 投资评级 / 盈利预测 / 证券研究报告」 | 研究报告 | 抽取 claim |

支持 `.pdf` 与 `.docx` 混放，可多份。报告期优先从**文件名**取 4 位年份，
取不到用 `--year` 兜底。两者都不像的文件会列在输出里，**不做猜测**。

**任意 PDF / DOCX 都会拿回报告**：一份年报都没识别出来（文件损坏、加密或
根本不是财报内容）时，程序**不报错、也不回落共享基线数据**——它用本次（空的）
解析结果跑完流程，产出一份「未识别到可核查字段」的说明性报告（PDF / Excel / HTML）。
命令行与网页两条入口行为一致。

实测（文件夹内混放年报 docx + 研报 docx）：

    扫描到 2 个文件：
      [annual  ] 2024年年度报告.docx（2024） -> 抽取 44 条字段
      [research] 2024年模拟研究报告.docx（2024） -> 抽取 39 条 claim
    解析产物 -> runs/20261005_onsite/ingested/

    C研 数值比对 FAIL=1  PASS=1

半年报需单独放在一个目录并显式指定期间，避免同一目录中的年报也被按半年口径解释：

    python -m fincheck ingest --from "D:\评委给的半年报材料" --run-id 20261012_h1 --period half_year

半年报的流量本期按 `YTD` 处理；当前抽取器不导入流量比较列和权益变动表，直到用原始半年报核验其列头与期间定位。该模式已接入 CLI，但仍需以比赛指定半年报做字段和值的验收。

**数据隔离**：现场解析结果全部写在 `runs/<run_id>/ingested/` 下，
**不写**共享的 `data/structured/`。因为 `load_structured` 会把目录下所有
`*.json` 全读进来，若往共享目录堆文件，两批材料会混在一起用。

**桌面文件夹演示（无需仓库预置 `data/`，只上传年报也可运行）**：在桌面创建 `财务核查材料`，放入一份或多份年报 PDF / DOCX。文件名最好包含报告年度；然后运行：

    python -m fincheck ingest --from "$env:USERPROFILE\Desktop\财务核查材料" --run-id onsite_demo --report-folder "$env:USERPROFILE\Desktop\财务核查结果"

程序按文件内容识别并解析年报，结果先写入项目的 `runs/onsite_demo/ingested/`，再将本次完整运行目录（PDF、Excel、HTML 报告，结构化字段、规则结果及日志）复制到桌面结果目录。年报单独运行时会跳过研报匹配，不会拿仓库 demo 研报冒充本次输入。默认在线调用需配置 `.env` 中的 `DEEPSEEK_API_KEY`；`--offline` 不联网，未命中缓存的语义任务转人工复核，规则核查和报告仍会生成。每次运行用不同 `--run-id`，避免覆盖既有结果。

### 8. 核查报告（PDF / Excel / 网页）

`ingest` 与 `run` 跑完会**自动生成**三种报告到 `runs/<run_id>/`；
也可以单独重生成（只读产物，不重跑流程、不调模型）：

    python -m fincheck report --run-id 20261005_demo
    python -m fincheck report --run-id 20261005_demo --formats html    # 只要网页版

| 文件 | 形式 | 用途 |
|---|---|---|
| `report.pdf` | 正式报告 | 计划书附图、答辩展示、归档 |
| `report.xlsx` | 异常清单 | 可筛选排序，交金融侧逐条复核 |
| `report.html` | 网页 | 点击查看证据、指标明细 |

内容按《项目思路》第八步的 13 项组织，首页含免责声明
（"本系统输出为财务数据一致性核查结果，不构成投资建议，所有结论需人工复核"）。
**报告只呈现程序产物，不重算、不改写任何数值**——所以改完规则要重跑流程，
只跑 `report` 不会更新结论。

除清单式章节外，三种报告都含两段**成段结论**：

- **核查总评**（首页）：核查规模（字段数 / 规则数 / 可执行与不适用）、异常分布
  （集中在哪个维度各几条）、CSI 等级，以及处置建议（先核哪几条、哪些是待复核、
  离线未命中缓存的影响）。
- **研报观点与财报数字差异点评**：逐条列出研报原句、研报引用值、财报披露值、
  差异与容差、确定性归因（单位量级 / 符号 / 数值），便于人工回原文追溯。

这两段由程序按确定性模板从已有产物生成，**不新增模型调用**，离线同样可复现。

**核查图表（2026-10-19）**：PDF 与 HTML 报告、网页端均新增三张可视化图，统一由 `src/viz.py` 生成，
数据口径与报告总评**同源**（同一 `merged_anomaly_counts`，拆分合计等于总计数）——
① **一致性维度雷达图**（五维得分，不适用维度标注「（不适用）」）；
② **异常分布**（按 C内 / C间 / C期 / C附 / C研 的「异常 / 待复核」堆叠柱状）；
③ **研报纠错对比表**（研报写的 / 应为（正确值）/ 位置）。
网页端用 Plotly 交互图（`app.py`），PDF 用 reportlab 手绘静态矢量图（零额外依赖），
HTML 内嵌 plotly.js 一次（`figures_html`），报告无需联网即可打开。

**报告结构增强（2026-10-14）**：① 数据提取结果**按报表类型分组展示**
（资产负债表 / 利润表 / 现金流量表 / 所有者权益 / 附注 / 分部，各成小节）；
② 新增独立的**「财务比率分析」章节**，按「盈利能力 / 费用率 / 趋势 / 结构 /
盈利预测（情景测算）」分组渲染 `src/financial_analysis.py` 与 `src/forecast.py`
的确定性计算结果；③ 一致性量化评价节新增 **CSI 可执行维度数统计**
（「可执行维度 X 个 / 共 Y 个维度」，X 为 `rules_applicable>0` 的维度数，不重算得分）。

跑完不想生成报告可加 `--no-report`。

### 9. 网页界面

    streamlit run app.py

浏览器打开后，可在页面上方拖入或选择年报 / 半年报 PDF / DOCX，填写没有写在文件名中的报告年度，选择**报告期间（年报 / 半年报）**与离线/在线模式，然后点击“开始核查并生成报告”。完成后页面自动切换到本次运行，可查看异常、CSI、字段和规则明细，并下载 PDF、Excel、HTML。也可以在左侧选择历史运行编号：

上传**任意** PDF / DOCX 都会返回一份报告：识别不出年报时同样生成三种报告，
只是内容为「未提取到字段记录 / 未计算 CSI」，并在报告中说明原因，而不是弹出报错。

> **报告期间必须与材料一致**：半年度报告务必选「半年报」。否则会按年报列头抽取——
> 利润表 / 现金流量表的年初至 6-30 累计数会被标成全年 `FY`，资产负债表期末余额被标成
> `2026-12-31`（该时点尚不存在）。网页上传与命令行 `--period` 行为一致，见 §五.7。

> **「选择运行编号」= 历史运行浏览器**：`runs/` 下每个目录是一个运行编号。选中哪一个，
> 右侧所有指标与明细就读哪一个目录的落盘产物，**与上传表单里当前是否勾选离线无关**。
> 上传新文件后会自动切到本次运行。顶部「模型调用」= **在线（api_ok，真实请求成功）/ 缓存
> （cache_hit，命中本地缓存）**；因此离线运行、或该次全部命中缓存时都会显示 `0 在线`——
> 这是"看的是哪一次运行"的差别，不是当前表单状态。

> **模型调用需要 API Key**。填在 `.env` 的 `DEEPSEEK_API_KEY`，**也可以在上传表单里临时填入**
> （`type="password"`，仅本次运行使用，不写日志/报告/缓存；留空则回落 `.env`）。流程：
> 取消勾选「离线运行」→ 填入 Key（或已配好 `.env`）→ 开始核查，模型任务即真实出站调用。

| 页签 | 内容 |
|---|---|
| ① 异常清单 | 逐条展开：计算式、差异、容差、证据定位与原文片段 |
| ② 一致性指标 | 各维度得分表 + CSI 公式/权重 + 柱状图 |
| ③ 提取字段 | 全部字段记录，可排序筛选，支持导出 CSV |
| ④ 规则明细 | 按状态筛选；含科目归一化结果 |
| ⑤ 报告下载 | 直接下载 PDF / Excel / 网页三种报告 |

界面**只读** `runs/<run_id>/` 下的产物，不重跑流程、不调模型——展示与计算分离，
看到的永远是已落盘、可复核的结果。

**本轮验收（2026-10-13）**：已用真实浏览器经**原生文件对话框**上传
`贵州茅台酒股份有限公司2024年年度报告.pdf`，运行 `web_20261013_224837_438aa4`：
`manifest.json` 状态 `COMPLETED`、`calls.api_ok=0`（离线，未出站）；`rules/csi.json`
CSI=100.0、等级 A，且带 `provisional: true` / `profile: "default-provisional"`；
产出提取字段 117、规则结果 37、异常 0，以及 `uploaded/*.pdf`、`manifest.json`、
`ingested/*.json`、`rules/results.json`、`rules/csi.json`、`report.pdf/xlsx/html`、
`llm/mappings.json`、`logs.jsonl` 共 **11 项**产物齐备。网页展示与下载均只读该次运行产物。

### 10. 质量核算（提取准确率 / 可复现性）

**结构化提取准确率 E** —— 分母取自人工标准答案，**不是**系统自己的输出：

    python tools/build_gold_standard.py --year 2024 --pdf <年报.pdf>     # 生成标准答案
    python tools/check_accuracy.py --gold data/gold_standard/贵州茅台_2024_核心字段.json --run FINAL

标准答案用**双独立来源交叉**：PDF 解析路径（与生产用的 docx 路径是两套不同代码）
+ 《一致性规则表》里金融侧独立做出的验算值。两者冲突则不写入，并提示人工裁定。
每条都带证据（文件、页码、原文片段），可逐条回验。

**结果可复现性** —— 比对两次运行的产物：

    python tools/check_reproducible.py --a RUN1 --b RUN2
    python tools/check_reproducible.py --a RUN1 --b RUN2 --strict-model   # 核验缓存重放

产物分两类，核验标准不同：

| 类别 | 产物 | 要求 |
|---|---|---|
| 确定性（纯程序计算） | `ingested/*`、`rules/results.json`、`rules/csi.json` | **必须逐字节一致** |
| 模型相关 | `rules/research_claims.json`、`rules/explanations.json` | 真实调用间允许浮动；缓存重放须一致 |

比对时剔除 `run_id`（它是运行标识，本就该不同）。**模型措辞不写进结果文件**——
否则"结果可机器比对"就失效了；模型输出单独存放，展示时按编号关联。

### 11. 离线 mock 联调（无需 API Key）

给还没有密钥、或想先验证契约的成员用：

    python tools/make_mock.py --task match_item  --run-id mytest --emit-template
    python tools/make_mock.py --task match_claim --run-id mytest --emit-template
    python -m fincheck run --config configs/demo.yaml --run-id mytest --offline

`--emit-template` 写出符合 `src/schemas.py` 的骨架响应；也可以用
`--response <自己的json>` 覆盖。**注意**：缓存键由「提示词内容 + 模型标识 +
推理参数 + 输入数据」算出，改动其中任一项都会让 mock 失效（表现为
`NO_CACHE_OFFLINE`），需重新生成。

### 13. MCP 服务（stdio，零新增依赖）

    python -m mcp_server              # 启动 MCP 服务（stdio，供 MCP 客户端连接）
    python -m mcp_server --selftest   # 离线自检：不发网络请求、不调模型

把本项目的核查能力以标准工具形式暴露给 MCP 客户端（仓库根 `.mcp.json` 为接入配置样例）。
暴露 **5 个工具**（`fincheck_ingest` / `fincheck_run` / `fincheck_report` /
`calculate_ratios` / `check_consistency`）与 **3 个资源**（`fincheck://runs`、
`fincheck://runs/{run_id}/manifest.json`、`fincheck://runs/{run_id}/rules/csi.json`）。
实现见 `mcp_server/server.py`（标准库 stdio JSON-RPC 2.0，**不新增任何依赖**），
接口设计见交付文档 `docs/MCP接口设计.md`（随交付包提供，不在本源码仓内）。MCP 层是既有函数/入口的**薄封装**，产物与 CLI 逐字节一致。

### 12. 汇总测试

    python -m pytest -q

**当前状态（2026-10-16）**：收集 **149** 项 / **全部通过（149 passed / 0 failed）**。
此前唯一红项 `test_web_app_handles_run_without_report` 已修复：`app.py` 报告下载页
原先把「有没有报告」的判断与提示**一并**放进 `if tab5.open:` 惰性页签渲染门控，
未打开该页签时 `streamlit.testing` 的 AppTest 取不到该提示文案；现改为只对
「读报告字节、建下载按钮」这一半门控，判断与提示始终渲染（未打开页签时不在浏览器上显示）。
`test_netguard_installed`、`test_web_app_renders_without_exception` 实测**通过**。

---

## 五之二、数据分两层：哪些要自备，哪些 clone 就有

这一点容易搞混，单独说明。项目的数据分两层：

| 层 | 位置 | 是否入库 | 谁需要 |
|---|---|---|---|
| **① 已解析的结构化数据** | `data/structured/*.json`、`data/demo/*.json` | ✅ **已提交**（约 90 KB） | 跑流程的人 —— **clone 即有** |
| **② 原始材料**（年报 PDF、研报 docx） | 各人本地 / 共享盘 | ❌ 不入库（`data/full/` 已 gitignore） | 只有**重新解析**时才需要 |

**结论：只要目标是"跑通流程 / 复现结果"，clone 仓库就够了，不需要任何外部文件。**
因为流程消费的是第 ① 层，不是 PDF 本身。

第 ② 层只在两种情况下才需要：**换了公司 / 换了报告期**，要重新从原始材料抽一次数。
这时用工具参数传你自己的本地路径即可，**代码里没有硬编码材料路径**：

    python tools/extract_fields.py --pdf "<你的路径>/2024年年度报告.pdf" --year 2024 --out 贵州茅台_2024
    python tools/extract_fields.py --input "<你的路径>/2026年半年度报告.pdf" --year 2026 --period half_year --out 贵州茅台_2026H1
    python tools/extract_claims.py --docx "<你的路径>/模拟研究报告.docx" --tag 2024 --out 研报claims_2024

解析结果写回 `data/structured/` 与 `data/demo/`；确认无误后再提交给全组。

> Windows 路径含空格或中文时记得加引号。两个工具都接受绝对路径与相对路径。
>
> 注意：`configs/demo.yaml` 里的 `annual_2024` / `annual_2025` 两行是**注释掉的**
> 示例占位，当前流程不读取——不要以为缺了它就跑不了。

---

## 六、离线复现（评审推荐，无需 API Key）

系统采用"调用缓存 + 结果比对"的方式支持无凭据复现。

### 原理

缓存键的构成为：

    cache_key = sha256(提示词内容 + 模型标识 + 推理参数 + 输入数据)

其中**推理参数**固定为 `temperature=0`、`top_p=1`、`max_tokens=8000`、`stream=false`、
`thinking={"type":"disabled"}`（见 `src/llm.py` 的 `PARAMS` 与 `THINKING`；关闭深度思考，
避免推理 token 占满 `max_tokens` 预算导致正文为空）。缓存键**不包含 API Key、不包含时间戳、
不包含机器名**。因此同一份输入在不同机器、不同使用者手中命中同一份缓存，输出结果一致。

### 人工植错研报回归样例

运行以下命令可生成一份明确标注为竞赛模拟材料的研报摘录、10 条 claims、核验真值 JSON 和契约 CSV 错误清单：

    python tools/build_error_case_fixture.py

错误类别按接口冻结枚举各 2 条（单位、期间、口径、数值、符号）。该 fixture 用于契约及回归测试；
收到的原始 15 条 DOCX 清单另存为转录草稿，因若干标准答案方向与源文冲突，不能与本 fixture 混作已确认真值。

### 操作步骤

1. 打开 `runs/demo_baseline/`，即为预先跑好的基准运行结果。
2. 确认 `llm_cache/` 目录中存在缓存文件（数量大于零）。
3. 移除网络连接（或直接断网）。
4. 运行：

       python test.py
       python -m pytest test_tasks.py -q

5. 预期结果：输出内容与 `runs/demo_baseline/` 中记录的结果一致，且全程未联网、未使用密钥。

### 离线运行的判定

| 现象 | 含义 |
|---|---|
| 状态显示 `CACHE_HIT` | 命中缓存，未发起请求 |
| 状态显示 `NO_CACHE_OFFLINE` | 离线模式下无对应缓存，该条转为需人工复核 |
| 状态显示 `API_OK` | 发起了真实请求，说明缓存未命中 |

---

## 七、目录结构

    .
    ├── .env                  凭据文件，不提交
    ├── .env.example          变量模板，提交
    ├── .gitignore
    ├── .mcp.json             MCP 客户端连接配置（stdio，指向 mcp_server）
    ├── requirements.txt      锁定依赖
    ├── README.md             运行说明
    ├── app.py                网页入口（Streamlit）
    ├── check_key.py          密钥配置诊断
    ├── test.py               最小调用测试
    ├── testcontract.py       契约自检
    ├── test_tasks.py         任务 / 提示词 / 校验 / MCP 协议测试
    ├── run.bat              双击入口（薄包装，仅调用 launcher.py）
    ├── launcher.py           交互式启动器：环境自检 → 选命令行/网页 → 填 Key → 选材料 → 运行
    ├── fincheck-launcher.exe  launcher.py 的 exe 外壳（免装 Python，缺环境自动下载内嵌运行时）
    ├── build_exe.bat        重新打包 fincheck-launcher.exe（PyInstaller）
    ├── fincheck/
    │   └── __main__.py       CLI 入口：run / ingest / report 与运行摘要
    ├── mcp_server/           MCP 服务（stdio JSON-RPC 2.0，标准库实现，零新增依赖）
    │   ├── __init__.py
    │   ├── __main__.py       `python -m mcp_server` 入口与 --selftest
    │   └── server.py         5 个 Tool + 3 个 Resource + 协议分发
    ├── configs/
    │   ├── demo.yaml         运行配置
    │   └── model.yaml        模型与推理参数声明（`src/llm.py` 启动时加载）
    ├── prompts/              5 个提示词（内容哈希写入 manifest）
    │   ├── match_item.txt    科目归一化
    │   ├── match_claim.txt   研报引用匹配
    │   ├── explain_anomaly.txt  异常原因解释
    │   ├── audit_claims.txt     语义审计（发现清单外语义问题）
    │   └── gen_commentary.txt   报告定性文字（研报式评价）
    ├── skills/               9 个 SKILL.md（声明式，不接入运行时）
    │   ├── match_item/SKILL.md     科目归一化（模型侧）
    │   ├── match_claim/SKILL.md    研报引用匹配（模型侧）
    │   ├── explain_anomaly/SKILL.md 异常原因解释（模型侧）
    │   ├── gen_commentary/SKILL.md  报告定性文字（模型侧）
    │   ├── audit_claims/SKILL.md    研报语义审计（模型侧，清单外疑点）
    │   ├── financial-ratio-calculator/SKILL.md  财务比率计算（确定性）
    │   ├── consistency-checker/SKILL.md         一致性核查（封装 27 条规则）
    │   ├── field-extractor/SKILL.md             字段提取
    │   └── report-generator/SKILL.md            报告生成
    ├── rules/
    │   ├── fields.yaml       字段字典
    │   ├── rules.yaml        规则配置（27 条）
    │   └── tolerance.yaml    容差配置
    ├── src/                  23 个模块
    │   ├── llm.py            模型调用闸门（缓存、重试、JSON 强制输出）
    │   ├── errors.py         错误分类与提示
    │   ├── inputs.py         5 类模型输入构造
    │   ├── schemas.py        5 类返回结构契约
    │   ├── validators.py     三层校验
    │   ├── tasks.py          任务级接口：加载提示词→调用→校验→修复重试
    │   ├── netguard.py       联网白名单与离线自检
    │   ├── pipeline.py       主编排：解析→语义→规则→解释→落盘
    │   ├── derived.py        确定性派生计算（同比/比率/情景测算）
    │   ├── financial_analysis.py  确定性财务分析（盈利能力/费用率/趋势/结构）
    │   ├── forecast.py       盈利预测（分产品外推 + PE 估值情景）
    │   ├── ingest.py         文件夹入口：按内容识别材料
    │   ├── web.py            网页上传→核查→报告共用编排
    │   ├── rules_engine.py   五维规则核查 + CSI 计算
    │   ├── research_check.py C研 研报数值比对
    │   ├── explain.py        异常原因解释
    │   ├── report.py         报告生成（PDF / Excel / 网页）
    │   ├── viz.py            可视化（Plotly 交互图 + reportlab 静态矢量图）
    │   ├── logger.py         事件记录
    │   ├── semantic_audit.py 大模型语义审计（结论仅落「待复核」）
    │   ├── refresh.py        产物新鲜度：按代码指纹重算确定性产物
    │   ├── codefingerprint.py 代码指纹（识别旧版产物并触发重算）
    │   └── manifest.py       运行清单
    ├── tools/                9 个脚本
    │   ├── extract_fields.py / extract_claims.py        年报 / 研报抽取
    │   ├── build_gold_standard.py / check_accuracy.py   标准答案与准确率 E
    │   ├── check_reproducible.py  可复现性比对
    │   ├── export_fields.py / check_fields.py           字段字典导出与自检
    │   └── make_mock.py / build_error_case_fixture.py   离线 mock 与植错 fixture
    ├── data/
    │   ├── demo/             样例材料（含研报 claims）
    │   ├── gold_standard/    人工标准答案
    │   ├── mock/             离线 mock 联调输入/输出 fixture
    │   └── structured/       解析输出的结构化数据
    └── runs/
        └── demo_baseline/    基准运行结果（提交，用于离线复现）

> 交付用文档（`docs/`）与评审交付物（`交付材料/`）与**本代码目录并列存放**，不随源码仓库提交，
> 打包时一并交付即可。

---

## 八、输出产物

所有结果都在 **`runs/<run_id>/`** 目录下（`runs/` 已加入 `.gitignore`，不提交）。
一次完整运行产生：

| 路径 | 内容 | 状态 |
|---|---|---|
| `manifest.json` | 运行清单：模型标识、推理参数、提示词哈希、输入文件哈希、依赖版本、调用统计 | ✅ |
| `logs.jsonl` | 事件记录（JSONL，追加写）：每次模型调用及状态、耗时、缓存键前 16 位 | ✅ |
| `llm_cache/*.json` | 模型响应缓存，**离线重放就靠它** | ✅ |
| `llm/mappings.json` | 科目归一化结果：财报原文科目名 → 标准科目编号，含程序回填的来源证据 | ✅ |
| `llm/claim_matches.json` | 研报引用匹配：每条 claim → 科目 + 期间 | ✅ |
| `rules/research_claims.json` | **C研** 研报引用数值比对结果（PASS / FAIL / REVIEW） | ✅ |
| `rules/results.json` | 表内 / 表间 / 跨期 / 附注 四维规则明细（`rules/rules.yaml` 共 27 条，逐期展开） | ✅ |
| `rules/csi.json` | 各维度得分与综合一致性指数 | ✅ |
| `rules/placeholder.json` | 无年报可核查时的占位说明 | ✅ |
| `report.pdf` | 核查报告正式版（13 项内容 + 免责声明） | ✅ |
| `report.xlsx` | 多表工作簿：摘要 / 异常清单 / 规则明细 / 提取字段 / 研报引用 / 待人工复核 | ✅ |
| `report.html` | 网页版报告，可点击查看证据 | ✅ |

**查看结果**：`llm/*.json` 与 `rules/*.json` 都是 UTF-8、`sort_keys` 序列化的 JSON，
可直接用编辑器或 Excel 打开比对。

运行清单中的 `calls` 字段统计 `api_ok` / `cache_hit` / `failed`。
注意 `logs.jsonl` 是**追加写**的，同一 `run_id` 重跑会累积；`calls` 已按
`started_at` 过滤，只计本轮。想彻底干净就换个 `run_id`。

---

## 九、联网边界

1. 运行期唯一允许的出站目标为 `api.deepseek.com:443`；其余地址由 `src/netguard.py`
   拦截并记录到 `logs.jsonl`。
2. 离线模式不读取凭据、不发起网络请求，仅读取本地缓存。
3. 系统不启用网页搜索、网络爬虫与外部数据库检索。
4. 系统中出现的"本地检索"指本地文件的文本匹配，不产生外部请求。
5. `src/netguard.py` 的白名单**放行本机回环地址**（`127.0.0.1` / `::1` / `localhost`），
   以便本地 `streamlit run app.py` 的页面自检与离线自检正常完成，不视为对外出站。

---

## 十、错误处理

| 错误 | 是否重试 | 程序行为 |
|---|---|---|
| 400 请求体格式错误 | 否 | 停止并提示检查模型名与参数 |
| 401 认证失败 | 否 | 停止并提示检查密钥 |
| 402 余额不足 | 否 | 自动降级为离线模式 |
| 422 参数错误 | 否 | 停止并提示检查参数写法 |
| 429 请求速率上限 | 是 | 指数退避重试，最多 3 次 |
| 500 / 503 服务端异常 | 是 | 指数退避重试，最多 3 次 |
| 网络超时或连接失败 | 是 | 指数退避重试，最多 3 次 |
| 输出不是合法 JSON | 是 | 提示词补充说明后重试 1 次 |
| 重试全部失败 | — | 该条记为需人工复核，不中断整体流程 |

---

## 十一、协作约定

1. 主分支只放可运行的版本。
2. 各人运行产物放入 `runs/<用户名>_<日期>_<用途>/`，互不覆盖。
3. `runs/demo_baseline/` 为公共基准结果，**修改后需全组同步**。
4. 字段名、枚举取值、容差档位的变更须先改 `rules/fields.yaml` 并升版本号，再改代码。
5. 大数据文件（完整年报、视频）走共享盘，不进入版本库。
6. 提交前执行 `git status`，确认清单中不含 `.env`、`.venv/`、`runs/<其他 run_id>/`。

---

## 十二、第三方依赖与说明

| 名称 | 版本 | 来源 | 许可证 | 用途 |
|---|---|---|---|---|
| Python | 3.11 | python.org | PSF | 运行环境 |
| openai | 见 requirements.txt | PyPI | Apache-2.0 | 调用兼容接口的客户端 |
| pydantic | 见 requirements.txt | PyPI | MIT | 返回结构定义与校验 |
| python-dotenv | 见 requirements.txt | PyPI | BSD-3-Clause | 读取环境变量文件 |
| PyYAML | 见 requirements.txt | PyPI | MIT | 读取规则与字段配置 |
| pdfplumber | 见 requirements.txt | PyPI | MIT | 解析年报 PDF 文本与表格 |
| pandas | 见 requirements.txt | PyPI | BSD-3-Clause | 网页界面表格与 CSV 导出 |
| DeepSeek API | 见 openai 平台文档 | 深度求索开放平台 | 商业服务 | 语义匹配与文字生成（请求模型标识 `deepseek-flash`，产品名 DeepSeek-V4.1-Flash） |

使用范围：上述依赖仅用于文档解析、结构校验、配置读取与模型调用。
逐项清单（名称、版本、来源、许可证、具体使用范围）见交付文档 `docs/third_party.md`。
第三方模型与商业软件不提交源代码，其名称、版本、调用方式与使用范围见本节、`docs/third_party.md` 与 `docs/model_adapter_contract.md`（均随交付包提供，不在本源码仓内）。

---

## 十三、免责声明

本系统输出为财务数据一致性核查结果，用于辅助人工复核，**不构成投资建议、评级或买卖意见**。
所有核查结论均需由专业人员复核。系统中使用的模拟研究材料仅为竞赛演示样例，
不代表任何机构发布的观点。年报等原始材料仅用于本地受控环境下的展示与核查，不对外分发。

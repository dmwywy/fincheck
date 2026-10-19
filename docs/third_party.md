# 第三方依赖与模型清单

---

## 1. 运行环境

| 名称 | 版本 | 来源 | 许可证 | 具体使用范围 |
|---|---|---|---|---|
| Python | 3.11（实测 3.11.9；README §一允许 3.11 / 3.12，不建议 3.13+） | python.org | PSF-2.0 | 全部程序的运行环境 |

---

## 2. 直接依赖（业务代码实际 import）

| 名称 | 版本 | 来源 | 许可证 | 具体使用范围 |
|---|---|---|---|---|
| openai | 3.19.2 | PyPI | Apache-2.0 | 唯一的模型客户端。`src/llm.py` 以 `base_url=https://api.deepseek.com` 调用 `/chat/completions`（`openai` SDK 自动补路径）。全项目唯一出站入口，其余模块不得直接调用 |
| pydantic | 2.13.5 | PyPI | MIT | `src/schemas.py` 定义并校验 5 类模型返回结构（`MatchItemOut` / `MatchClaimOut` / `ExplainOut` / `ReportTextOut` / `AuditOut`）；`src/tasks.py` 的三层校验第一层 |
| python-dotenv | 1.2.3 | PyPI | BSD-3-Clause | `src/llm.py` 读取 `.env` 中的 `DEEPSEEK_API_KEY`、`MODEL_ID`；凭据仅调用时读取，不落日志/报告/缓存 |
| PyYAML | 6.0.3 | PyPI | MIT | 读取配置与规则：`rules/fields.yaml`、`rules/rules.yaml`、`rules/tolerance.yaml`、`configs/demo.yaml`、`configs/model.yaml`；亦用于 `src/tasks.py` 加载标准科目清单 |
| pdfplumber | 0.11.10 | PyPI | MIT | 年报 PDF 的文本/表格解析：`src/ingest.py::_pdf_text`、`tools/extract_fields.py` 的 PDF 路径、`tools/build_gold_standard.py` |
| pandas | 3.0.6 | PyPI | BSD-3-Clause | 网页界面的表格渲染与导出：`app.py`（异常清单/指标/字段/规则明细，字段 CSV 导出） |

### 交付/展示相关直接依赖（由上述同一份 `requirements.txt` 锁定）

| 名称 | 版本 | 来源 | 许可证 | 具体使用范围 |
|---|---|---|---|---|
| streamlit | 1.64.0 | PyPI | Apache-2.0 | 网页界面入口 `app.py`（上传核查、5 个结果页签、报告下载） |
| openpyxl | 3.1.5 | PyPI | MIT | `src/report.py` 生成 `report.xlsx`（摘要/异常清单/规则明细/提取字段/研报引用/待人工复核） |
| reportlab | 4.2.5 | PyPI | BSD-3-Clause | `src/report.py` 生成 `report.pdf`（13 项内容 + 免责声明） |

---

## 3. 第三方模型与商业服务

| 名称 | 版本/标识 | 来源 | 许可证 | 具体使用范围 |
|---|---|---|---|---|
| DeepSeek API | 请求模型标识 **`deepseek-flash`**；对外产品名 **DeepSeek-V4.1-Flash**（另有白名单可选 `deepseek-v4-pro`） | 深度求索开放平台（`api.deepseek.com`） | 商业服务（非开源，不提交源代码） | 受限期语义任务（**5 个**）：`match_item` 科目归一化、`match_claim` 研报引用匹配、`explain_anomaly` 异常原因解释（候选中选一）、`gen_commentary` 报告定性文字（研报式评价）、`audit_claims` 研报语义审计（清单外问题的自由审计，结论仅落「待人工复核」）。请求参数固定 `temperature=0 / top_p=1 / max_tokens=8000 / stream=false / response_format={"type":"json_object"} / thinking={"type":"disabled"}`，见 `docs/model_adapter_contract.md` §4、§9 |

使用边界（与 `docs/model_adapter_contract.md` 一致）：

- 模型只做语义判断与文字表述，**不产出任何数值、不产出位置、不生成页码**。
- 请求里 `model` 字段必须写 `deepseek-flash`，不得写产品名 `DeepSeek-V4.1-Flash`；写错在启动瞬间由 `src/llm.py` 的白名单报错，不发出请求。
- 凭据仅来自 `.env` 或系统环境变量（变量名 `DEEPSEEK_API_KEY`），一律掩码呈现（形如 `sk-abc***xyz`）；不写入缓存键、日志、报告，故换机器/换 Key 仍命中同一份缓存。
- 运行期唯一允许的出站目标为 `api.deepseek.com:443`（`src/netguard.py`）；离线模式不读凭据、不发起请求。

---

## 4. 其余锁定包（传递依赖，`requirements.txt` 其余项）

以下包由 `requirements.txt` 一并锁定，仅用于支撑上述直接依赖（HTTP 栈、图表、PDF/图片处理、类型/序列化、测试框架等），
业务代码不直接调用其 API；许可证随上游发布包。

| 名称 | 版本（requirements.txt） | 备注/许可证（据上游） |
|---|---|---|
| altair | 6.3.0 | BSD-3-Clause（Streamlit 图表） |
| numpy | 2.4.6 | BSD-3-Clause（pandas 依赖） |
| pyarrow | 25.0.1 | Apache-2.0（pandas 列式后端） |
| narwhals | 2.26.0 | MIT（pandas 依赖） |
| jsonschema / jsonschema-specifications / referencing / rpds-py | 4.26.0 / 2025.9.1 / 0.37.0 / 2026.6.3 | MIT（Streamlit 系） |
| Jinja2 / MarkupSafe / itsdangerous | 3.1.6 / 3.0.3 / 2.2.0 | BSD-3-Clause（Streamlit/模板） |
| requests / certifi / charset-normalizer / idna / urllib3 | 2.34.2 / 2026.7.22 / 3.5.1 / 3.20 / 2.8.0 | requests 系（Apache-2.0 / MPL-2.0 / MIT / BSD-3-Clause / MIT） |
| pdfminer.six | 20260107 | MIT（pdfplumber 依赖） |
| pymupdf | 1.28.2 | **AGPL-3.0**（注意交付合规，属外围/可选路径） |
| pypdfium2 | 5.13.0 | 见上游包（含 PDFium） |
| pillow | 12.3.0 | MIT-CMU / HPND（图片处理） |
| protobuf | 7.36.2 | BSD-3-Clause（Streamlit 依赖） |
| pydeck | 0.9.3 | Apache-2.0（Streamlit 依赖） |
| attrs | 26.1.0 | MIT |
| click / colorama / toml | 8.5.0 / 0.4.6 / 0.10.2 | BSD-3-Clause / BSD-3-Clause / MIT |
| pytest / pluggy / iniconfig / packaging / Pygments | 9.1.1 / 1.6.0 / 2.3.0 / 26.3 / 2.21.0 | 测试与工具链 |
| typing_extensions / typing-inspection / annotated-types | 4.16.0 / 0.4.4 / 0.8.0 | 类型与 pydantic 依赖 |
| pydantic_core | 2.46.5 | MIT（pydantic 依赖） |
| cryptography / cffi / pycparser | 50.0.1 / 2.1.1 / 3.0 | 加密与 C 绑定 |
| python-dateutil / six / tzdata | 2.9.0.post0 / 1.17.0 / 2026.4 | 日期与时区 |
| anyio / sniffio / h11 / httpcore2 / httpx2 / jiter / truststore | 4.15.1 / 1.3.1 / 0.16.0 / 2.13.1 / 2.13.1 / 0.17.0 / 0.10.4 | HTTP 与 JSON 解析栈（`openai` 依赖；`httpx2`/`httpcore2` 为 `requirements.txt` 中的锁定名） |
| starlette / uvicorn / websockets / httptools / watchdog | 1.7.0 / 0.54.0 / 16.1.1 / 0.8.0 / 6.0.0 | Streamlit/ASGI 运行栈 |
| python-multipart | 0.0.32 | Apache-2.0（网页上传表单） |

---

## 4.1 构建工具（可选，仅打包 `fincheck-launcher.exe` 时使用）

| 名称 | 版本 | 来源 | 许可证 | 具体使用范围 |
|---|---|---|---|---|
| PyInstaller | 6.22.3 | https://pyinstaller.org（PyPI 分发） | GPL-2.0-or-later，附「打包产物不受 GPL 约束」的例外条款（见上游 LICENSE.txt） | 仅由 `build_exe.bat` 调用，把 `launcher.py` 打成单文件 `fincheck-launcher.exe`。**不在 `requirements.txt` 内、运行时不 import**；exe 内只有启动器外壳，不含任何第三方库源码、模型或数据。不使用 exe 时该工具完全不参与评测流程（用 `run.bat` / `python launcher.py` 即可）。 |

## 5. 提交合规结论

1. 上列第三方库均为公开发布的开源软件（除 DeepSeek API 为商业服务），**随源码提交的只有本仓库自身代码**，
   第三方库通过 `requirements.txt` 声明、由使用者 `pip install` 获取，不复制其源码入库。
2. DeepSeek 模型与商业服务不提交源代码；其名称、版本、调用方式与使用范围见本文第 3 节及
   `docs/model_adapter_contract.md`。
3. 本仓库不包含任何 API Key、密钥或个人凭据；`.env` 已在 `.gitignore`。

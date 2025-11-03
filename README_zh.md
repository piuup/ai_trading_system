# AI 交易系统

基于对话的量化交易助手：读取自然语言策略需求，生成统一 YAML 配置，调用 Backtrader 使用 Tushare 数据回测，并在聊天界面解读结果。  
👉 **English version:** [README.md](README.md)

![AI Trading Copilot UI](strategy_test.png)

## 项目亮点
- DeepSeek 驱动的对话式策略设计（可通过 `.env` 或 `config/defaults.yaml` 配置）
- 自动拆解策略模块并生成统一的 YAML 策略模板
- 结合 Tushare 行情 + Backtrader 的一键回测流程
- AI 讲解回测指标并提供优化建议
- 拟 ChatGPT 的前端交互，默认填充近 4 年区间及 ETF `510300.SH`

## 目录结构
```
backend/
  backtester.py      # Backtrader 集成、Tushare 数据、指标汇总
  config.py          # 环境配置加载（支持 .env + config/defaults.yaml）
  llm_client.py      # DeepSeek API 封装，用于策略拆解与结果解读
  main.py            # FastAPI 应用入口 & REST 接口
  schemas.py         # Pydantic 数据模型
  strategy_parser.py # 统一 YAML 的合法性校验
config/
  defaults.yaml      # 全局参数配置（API key、UI 默认值、回测参数）
frontend/
  index.html         # 聊天界面
  scripts.js         # 交互逻辑与默认值初始化
  styles.css         # 样式
requirements.txt
```

## 快速上手
1. **安装依赖**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   pip install -r requirements.txt
   ```
2. **配置凭据**
   - 方式 A：复制 `.env.example` → `.env`，填写 `DEEPSEEK_API_KEY`、`TUSHARE_TOKEN`
   - 方式 B：直接修改 `config/defaults.yaml`（文件中的数值会作为默认回退）
   - `DEEPSEEK_API_BASE` 未设置时默认使用 `https://api.deepseek.com/v1`
3. **启动服务**
   ```bash
   uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
   ```
   浏览器访问 `http://localhost:8000/` 体验聊天界面。

## 工作流程
1. 前端将用户策略描述、标的代码与时间区间 POST 到 `/api/chat`。
2. `LLMClient` 通过 DeepSeek 生成包含模块拆解、统一 YAML、结构图的 JSON。
3. YAML 由 `strategy_parser.py` 校验，并在 `backtester.py` 中映射成 Backtrader 策略。
4. 系统请求 Tushare 日线数据，执行回测并收集绩效指标。
5. 回测指标与策略摘要再次发送给 DeepSeek，生成优化建议。
6. 前端聊天框展示模块信息、YAML、回测摘要以及 AI 解读。

如果想修改 YAML 并重新回测，可直接向 `/api/backtest` 发送相同结构的请求体。

## 配置说明
- `config/defaults.yaml` 定义了 UI 默认值（如 `default_symbol`、`default_years`、`initial_cash`）以及 DeepSeek、Backtrader 参数。
- 环境变量优先级高于 YAML，便于在生产环境中对敏感信息进行覆盖。
- 前端会先使用内置回退值，待 `/api/config` 返回后再更新为服务器配置。

## 限制与后续方向
- 当前仅支持 `backtester.py` 中实现的策略规则（如 `sma_crossover`、`ema_crossover`、`rsi_oversold` 等），需要更多指标时可扩展 `_build_strategy_class`。
- 默认只处理 Tushare 日线数据。
- DeepSeek 与 Tushare API 访问依赖网络环境。
- 现阶段策略假设为多头股票/ETF。若需多标的或做空场景，请扩展仓位管理逻辑。

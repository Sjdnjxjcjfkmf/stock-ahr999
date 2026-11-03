# Stock AHR999 美股自动估值平台（Prototype 0.1）

## 当前功能
- 输入美股 ticker 自动请求行情与财务数据（yfinance 原型数据源）
- 计算最近 200 个交易日收盘价的等额定投成本（调和平均数）
- 从现金流量表估算历史自由现金流及 CAGR
- 自动生成简化质量评分
- 使用 10 年增长逐步衰减的 DCF 模型估值
- 计算 Stock AHR999 = (P / C) × (P / V) ÷ q
- SQLite 保存每次估值快照
- 提供演示模式（明确标注合成数据）

## 本地运行
建议 Python 3.11 或更新版本。

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
uvicorn backend.main:app --reload
```
浏览器打开 http://127.0.0.1:8000

## API
- `GET /api/health`
- `GET /api/analyze/AAPL`
- `GET /api/analyze/AAPL?demo=true`
- `GET /api/history/AAPL`
- `GET /api/universe`

## 估值逻辑与重要限制
1. 成本锚 C：最近最多 200 个有效日收盘价的调和平均数。若未来要模拟每天固定金额买入并考虑分红、拆股、交易成本，应升级为复权总回报口径。
2. 价值锚 V：当前版本使用最新年度自由现金流/股作为起点，10 年显式预测，增长率线性收敛到 3% 永续增长率，折现率固定 10%。
3. 增长率：尝试从近年年度 FCF 计算 CAGR，限制在 -10% 至 20%；不可用时暂用 6%，并明确警告。
4. 质量 q：简单规则评分原型，依据负债权益比、利润率、历史 FCF 正负情况。不是信用评级，也不是成熟的护城河模型。
5. 若无法获取正 FCF/股，不伪造 DCF 估值，AHR999 返回不可计算。
6. yfinance 是非官方数据接口，可能限流、变更或中断。该项目目前未集成 SEC EDGAR，不能将现阶段数据视为经过 SEC 交叉验证的正式财报数据。
7. 不构成投资建议。生产版必须补充 XBRL 数据映射、公司行动调整、行业模型、敏感性分析、数据血缘、回测和前视偏差控制。

## 下一阶段路线
- 接入 SEC EDGAR Company Facts API，作为财务数据主来源，并保留原始 accession / filing date。
- 价格历史按 point-in-time 数据和公司行动处理。
- 预测模型增加行业分类、FCF margin、ROIC、净债务、股权稀释。
- 对 DCF 做 bear/base/bull 情景与敏感性矩阵。
- 建立 PIT 回测：每个历史日期只允许使用当时已公开的财报。
- 为行情 API 加缓存、熔断和可替换 provider adapter。

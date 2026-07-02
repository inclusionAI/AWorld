# 中证A500投资决策系统测试用例（Hybrid模式验证）

**Date:** 2026-04-03  
**Purpose:** 使用 Hybrid 架构构建实时投资决策系统  
**Scenario:** 中证A500指数下跌1个点时的投资机会分析

## 1. 场景概述

### 1.1 业务场景

**触发条件：** 中证A500指数实时跌幅达到 -1.0%

**决策目标：** 在5分钟内完成投资机会分析并给出决策建议
- **买入 (BUY):** 发现明确投资机会，建议买入
- **观望 (HOLD):** 需要更多信息，暂不操作
- **规避 (AVOID):** 风险过高，不建议介入

**输出格式：**
```json
{
  "decision": "BUY|HOLD|AVOID",
  "confidence": 0.85,
  "reasoning": "...",
  "target_stocks": [
    {
      "symbol": "000001.SZ",
      "name": "平安银行",
      "action": "BUY",
      "suggested_position": "5%",
      "entry_price": 12.50,
      "stop_loss": 11.80,
      "take_profit": 13.50
    }
  ],
  "risk_assessment": {
    "overall_risk": "MEDIUM",
    "risk_factors": [...],
    "market_sentiment": "FEAR"
  },
  "execution_time_ms": 245000
}
```

### 1.2 为什么适合 Hybrid 架构

**任务特征：**
- ✅ **可分解:** 技术分析、基本面分析、情绪分析、风险评估可并行
- ✅ **有依赖:** 分析师间需要交叉验证信号（技术 ←→ 基本面）
- ✅ **需协调:** 投资决策协调器综合所有分析师的意见
- ✅ **时效性:** 5分钟决策窗口，需要快速并行处理

**Peer 协作价值：**
- 技术分析师发现支撑位 → 询问基本面分析师该位置是否有价值支撑
- 基本面分析师发现低估股票 → 请求技术分析师确认入场时机
- 情绪分析师检测到恐慌性抛售 → 通知风险评估师调整风险等级
- 风险评估师发现系统性风险 → 广播给所有分析师调整策略

## 2. 架构设计

### 2.1 Multi-Agent 拓扑

```
              InvestmentCoordinator (Root)
                   /    |    |    \
                  /     |    |     \
    Technical  ←→ Fundamental ←→ Sentiment ←→ Risk
    Analyst       Analyst         Analyst      Assessor
```

**代理职责：**

| Agent | 职责 | 输入数据源 | 输出 |
|-------|------|-----------|------|
| InvestmentCoordinator | 综合决策、持仓管理 | 所有分析师报告 | 最终投资决策 |
| TechnicalAnalyst | 技术指标、支撑阻力 | K线数据、成交量 | 技术信号 + 关键价位 |
| FundamentalAnalyst | 估值分析、财务健康度 | 财报、行业数据 | 价值评估 + 安全边际 |
| SentimentAnalyst | 市场情绪、资金流向 | 新闻、社交媒体、资金流 | 情绪指标 + 市场预期 |
| RiskAssessor | 风险评估、止损建议 | 波动率、相关性 | 风险等级 + 对冲建议 |

### 2.2 Peer 协作流程

**Scenario 1: 技术信号 + 基本面验证**
```
TechnicalAnalyst: 发现中证A500支撑位 @ 1450点
                 ↓ ask_peer()
FundamentalAnalyst: 分析当前点位对应的平均PE = 12.5（低于历史中位数13.8）
                   ↓ 返回 "估值支撑存在"
TechnicalAnalyst: 结合技术 + 估值信号 → 生成 "强买入信号"
```

**Scenario 2: 基本面机会 + 技术确认**
```
FundamentalAnalyst: 扫描发现 5 只低估值高质量股票
                   ↓ request_peer_action("check_entry_timing")
TechnicalAnalyst: 分析这 5 只股票的技术形态
                 ↓ 返回 3 只处于最佳入场时机
FundamentalAnalyst: 优先推荐这 3 只股票
```

**Scenario 3: 情绪恐慌 + 风险调整**
```
SentimentAnalyst: 检测到市场恐慌情绪 VIX 指数飙升
                 ↓ broadcast_to_all_peers("恐慌性抛售")
RiskAssessor: 收到广播 → 调整风险等级为 HIGH
             ↓ share_with_peer(TechnicalAnalyst)
TechnicalAnalyst: 收到风险提示 → 调整止损位更保守
```

**Scenario 4: 风险预警 + 全员响应**
```
RiskAssessor: 发现美股期货大跌（外部风险）
             ↓ broadcast_to_all_peers("系统性风险预警")
所有分析师: 收到广播 → 暂停买入信号，建议观望
          ↓ 调整分析策略为防御型
```

## 3. 实现代码

### 3.1 Agent Definitions

```python
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import HybridSwarm
from typing import Dict, List, Any
import asyncio
from datetime import datetime


# ============ Coordinator Agent ============

class InvestmentCoordinator(Agent):
    """投资决策协调器 - Root Agent"""
    
    system_prompt = """你是一个专业的投资决策协调器。
    你的职责是：
    1. 接收中证A500指数下跌信号
    2. 协调4个分析师团队进行快速分析
    3. 综合所有分析结果，给出明确的投资决策
    4. 控制风险，确保决策符合风险管理原则
    
    决策原则：
    - 技术面和基本面需要互相验证
    - 市场情绪恐慌但基本面良好时为最佳买入时机
    - 风险评估为HIGH时必须观望，不能买入
    - 所有买入决策必须有明确的止损和止盈
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.portfolio = {"cash": 1000000, "positions": {}}  # 100万初始资金
    
    async def step(self, market_signal: Dict[str, Any]):
        """处理市场信号并做出投资决策"""
        
        index_name = market_signal["index_name"]  # "中证A500"
        current_level = market_signal["current_level"]  # 1450.50
        change_pct = market_signal["change_pct"]  # -1.0%
        timestamp = market_signal["timestamp"]
        
        self.log(f"收到信号: {index_name} 下跌 {change_pct}%, 当前点位 {current_level}")
        
        # Phase 1: 并行启动所有分析师（通过 handoffs）
        analyses = await asyncio.gather(
            self.call_agent("TechnicalAnalyst", market_signal),
            self.call_agent("FundamentalAnalyst", market_signal),
            self.call_agent("SentimentAnalyst", market_signal),
            self.call_agent("RiskAssessor", market_signal)
        )
        
        technical_report, fundamental_report, sentiment_report, risk_report = analyses
        
        # Phase 2: 综合分析
        decision = self._make_decision(
            technical_report,
            fundamental_report,
            sentiment_report,
            risk_report
        )
        
        # Phase 3: 构造响应
        return {
            "decision": decision["action"],  # BUY/HOLD/AVOID
            "confidence": decision["confidence"],
            "reasoning": decision["reasoning"],
            "target_stocks": decision.get("stocks", []),
            "risk_assessment": risk_report,
            "execution_time_ms": decision["execution_time_ms"],
            "reports": {
                "technical": technical_report,
                "fundamental": fundamental_report,
                "sentiment": sentiment_report,
                "risk": risk_report
            }
        }
    
    def _make_decision(
        self,
        technical: Dict,
        fundamental: Dict,
        sentiment: Dict,
        risk: Dict
    ) -> Dict:
        """综合决策逻辑"""
        
        start_time = datetime.now()
        
        # Rule 1: 风险为 HIGH，直接观望
        if risk["overall_risk"] == "HIGH":
            return {
                "action": "AVOID",
                "confidence": 0.9,
                "reasoning": f"系统性风险过高: {risk['risk_factors']}",
                "execution_time_ms": (datetime.now() - start_time).total_seconds() * 1000
            }
        
        # Rule 2: 技术 + 基本面双重确认买入信号
        tech_buy_signal = technical.get("signal") == "BUY"
        fund_undervalued = fundamental.get("valuation") == "UNDERVALUED"
        sentiment_fear = sentiment.get("market_sentiment") == "FEAR"
        
        if tech_buy_signal and fund_undervalued and sentiment_fear:
            # 恐慌性抛售 + 技术支撑 + 估值低估 = 黄金买入机会
            return {
                "action": "BUY",
                "confidence": 0.85,
                "reasoning": "恐慌性抛售创造低估买入机会，技术面出现支撑，基本面估值低于历史中位数",
                "stocks": self._select_stocks(technical, fundamental),
                "execution_time_ms": (datetime.now() - start_time).total_seconds() * 1000
            }
        
        # Rule 3: 技术面买入信号，但基本面未确认
        if tech_buy_signal and not fund_undervalued:
            return {
                "action": "HOLD",
                "confidence": 0.6,
                "reasoning": "技术面出现买入信号，但基本面估值偏高，建议观望等待更好时机",
                "execution_time_ms": (datetime.now() - start_time).total_seconds() * 1000
            }
        
        # Rule 4: 基本面低估，但技术面未确认
        if fund_undervalued and not tech_buy_signal:
            return {
                "action": "HOLD",
                "confidence": 0.65,
                "reasoning": "基本面估值低估，但技术面尚未出现明确买入信号，建议等待技术面确认",
                "execution_time_ms": (datetime.now() - start_time).total_seconds() * 1000
            }
        
        # Default: 观望
        return {
            "action": "HOLD",
            "confidence": 0.5,
            "reasoning": "未达到明确买入条件，建议继续观望",
            "execution_time_ms": (datetime.now() - start_time).total_seconds() * 1000
        }
    
    def _select_stocks(self, technical: Dict, fundamental: Dict) -> List[Dict]:
        """选择目标股票"""
        # 从基本面分析中获取低估值股票
        undervalued_stocks = fundamental.get("recommended_stocks", [])
        
        # 从技术分析中获取最佳入场时机的股票
        good_timing_stocks = set(technical.get("entry_timing", []))
        
        # 选择同时满足两个条件的股票（取交集）
        target_stocks = [
            stock for stock in undervalued_stocks
            if stock["symbol"] in good_timing_stocks
        ]
        
        return target_stocks[:3]  # 最多推荐3只


# ============ Technical Analyst ============

class TechnicalAnalyst(Agent):
    """技术分析师"""
    
    system_prompt = """你是一个技术分析专家。
    分析内容：
    1. 判断当前价位是否处于关键支撑位或阻力位
    2. 分析成交量变化，判断是否有资金流入
    3. 计算技术指标（MACD, RSI, 布林带等）
    4. 与基本面分析师协作，验证技术信号的有效性
    """
    
    tool_names = ["stock_data_api", "technical_indicators"]
    
    async def step(self, market_signal: Dict):
        """技术分析主流程"""
        
        index_name = market_signal["index_name"]
        current_level = market_signal["current_level"]
        
        self.log(f"开始技术分析: {index_name} @ {current_level}")
        
        # Step 1: 分析指数技术形态
        index_analysis = await self._analyze_index_technical(index_name, current_level)
        
        # Step 2: Peer协作 - 询问基本面分析师当前估值
        self.log("请求基本面分析师验证估值支撑...")
        valuation_support = await self.ask_peer(
            peer_name="FundamentalAnalyst",
            question=f"{index_name} 在 {current_level} 点位的估值是否合理？",
            context={"index": index_name, "level": current_level},
            timeout=30.0
        )
        
        self.log(f"基本面反馈: {valuation_support}")
        
        # Step 3: 扫描成分股技术形态
        constituent_stocks = await self._scan_constituent_stocks(index_name)
        
        # Step 4: Peer协作 - 请求基本面分析师筛选优质股票
        self.log("请求基本面分析师筛选优质股...")
        quality_stocks = await self.request_peer_action(
            peer_name="FundamentalAnalyst",
            action="filter_quality_stocks",
            parameters={
                "stock_list": [s["symbol"] for s in constituent_stocks],
                "criteria": "high_quality"
            },
            timeout=45.0
        )
        
        # Step 5: 结合技术面 + 基本面，给出最佳入场时机
        entry_timing = self._calculate_entry_timing(
            constituent_stocks,
            quality_stocks.get("stocks", [])
        )
        
        # Step 6: 生成技术分析报告
        signal = "BUY" if index_analysis["support_found"] and "支撑" in valuation_support else "HOLD"
        
        return {
            "signal": signal,
            "index_technical": index_analysis,
            "support_level": index_analysis.get("support_level"),
            "resistance_level": index_analysis.get("resistance_level"),
            "volume_trend": index_analysis.get("volume_trend"),
            "entry_timing": entry_timing,  # 最佳入场时机的股票列表
            "analysis_timestamp": datetime.now().isoformat()
        }
    
    async def on_peer_question(self, question: str, context: Any, sender_name: str) -> str:
        """处理来自其他分析师的技术分析问题"""
        
        if "入场时机" in question or "买入时机" in question:
            symbols = context.get("symbols", [])
            timing_analysis = await self._analyze_entry_timing(symbols)
            
            good_timing = [s for s in timing_analysis if s["timing_score"] > 0.7]
            
            return f"推荐入场: {[s['symbol'] for s in good_timing]}"
        
        if "支撑位" in question or "阻力位" in question:
            symbol = context.get("symbol")
            levels = await self._calculate_support_resistance(symbol)
            return f"支撑位: {levels['support']}, 阻力位: {levels['resistance']}"
        
        return "请提供更具体的技术分析问题"
    
    async def _analyze_index_technical(self, index: str, level: float) -> Dict:
        """分析指数技术形态（模拟）"""
        # 实际实现会调用 stock_data_api
        return {
            "support_found": True,
            "support_level": 1445.0,
            "resistance_level": 1480.0,
            "volume_trend": "increasing",
            "macd": "bullish_crossover",
            "rsi": 38.5  # 超卖
        }
    
    async def _scan_constituent_stocks(self, index: str) -> List[Dict]:
        """扫描成分股技术形态（模拟）"""
        # 返回技术面较好的股票
        return [
            {"symbol": "000001.SZ", "name": "平安银行", "technical_score": 0.85},
            {"symbol": "600036.SH", "name": "招商银行", "technical_score": 0.78},
            {"symbol": "000858.SZ", "name": "五粮液", "technical_score": 0.82}
        ]
    
    def _calculate_entry_timing(self, technical_stocks: List, quality_stocks: List) -> List[str]:
        """计算最佳入场时机"""
        # 取技术面和基本面的交集
        tech_symbols = {s["symbol"] for s in technical_stocks if s["technical_score"] > 0.75}
        quality_symbols = set(quality_stocks)
        
        return list(tech_symbols & quality_symbols)


# ============ Fundamental Analyst ============

class FundamentalAnalyst(Agent):
    """基本面分析师"""
    
    system_prompt = """你是一个基本面分析专家。
    分析内容：
    1. 计算指数整体估值水平（PE, PB）
    2. 扫描低估值高质量股票
    3. 分析行业景气度和盈利趋势
    4. 与技术分析师协作，确认最佳买入时机
    """
    
    tool_names = ["financial_data_api", "valuation_calculator"]
    
    async def step(self, market_signal: Dict):
        """基本面分析主流程"""
        
        index_name = market_signal["index_name"]
        current_level = market_signal["current_level"]
        
        self.log(f"开始基本面分析: {index_name} @ {current_level}")
        
        # Step 1: 分析指数估值
        valuation = await self._analyze_index_valuation(index_name, current_level)
        
        # Step 2: 扫描低估值股票
        undervalued_stocks = await self._scan_undervalued_stocks(index_name)
        
        # Step 3: Peer协作 - 请求技术分析师确认入场时机
        self.log("请求技术分析师确认最佳入场时机...")
        entry_timing = await self.request_peer_action(
            peer_name="TechnicalAnalyst",
            action="check_entry_timing",
            parameters={
                "symbols": [s["symbol"] for s in undervalued_stocks]
            },
            timeout=30.0
        )
        
        good_timing_symbols = set(entry_timing.get("good_timing", []))
        
        # Step 4: 筛选同时满足估值 + 时机的股票
        recommended_stocks = [
            {
                **stock,
                "entry_price": stock["current_price"],
                "stop_loss": stock["current_price"] * 0.92,  # -8% 止损
                "take_profit": stock["target_price"]
            }
            for stock in undervalued_stocks
            if stock["symbol"] in good_timing_symbols
        ]
        
        return {
            "valuation": valuation["assessment"],  # "UNDERVALUED" / "FAIR" / "OVERVALUED"
            "index_pe": valuation["current_pe"],
            "index_pb": valuation["current_pb"],
            "historical_median_pe": valuation["median_pe"],
            "recommended_stocks": recommended_stocks,
            "analysis_timestamp": datetime.now().isoformat()
        }
    
    async def on_peer_question(self, question: str, context: Any, sender_name: str) -> str:
        """回答估值相关问题"""
        
        if "估值" in question or "合理" in question:
            index = context.get("index")
            level = context.get("level")
            
            valuation = await self._analyze_index_valuation(index, level)
            
            if valuation["assessment"] == "UNDERVALUED":
                return f"当前估值支撑较强，PE {valuation['current_pe']} 低于历史中位数 {valuation['median_pe']}"
            elif valuation["assessment"] == "OVERVALUED":
                return f"当前估值偏高，PE {valuation['current_pe']} 高于历史中位数 {valuation['median_pe']}"
            else:
                return f"估值合理，PE {valuation['current_pe']} 接近历史中位数"
        
        return "请提供估值相关问题"
    
    async def on_peer_action_request(self, action: str, parameters: Dict, sender_name: str) -> Dict:
        """处理其他分析师的行动请求"""
        
        if action == "filter_quality_stocks":
            stock_list = parameters.get("stock_list", [])
            criteria = parameters.get("criteria", "high_quality")
            
            # 筛选高质量股票（ROE > 15%, 负债率 < 60%）
            quality_stocks = await self._filter_by_quality(stock_list, criteria)
            
            return {
                "status": "success",
                "stocks": quality_stocks
            }
        
        return {
            "status": "error",
            "message": f"Unknown action: {action}"
        }
    
    async def _analyze_index_valuation(self, index: str, level: float) -> Dict:
        """分析指数估值（模拟）"""
        return {
            "current_pe": 12.5,
            "median_pe": 13.8,
            "current_pb": 1.35,
            "median_pb": 1.50,
            "assessment": "UNDERVALUED"  # 低于历史中位数
        }
    
    async def _scan_undervalued_stocks(self, index: str) -> List[Dict]:
        """扫描低估值股票（模拟）"""
        return [
            {
                "symbol": "000001.SZ",
                "name": "平安银行",
                "current_price": 12.50,
                "target_price": 13.80,
                "pe": 5.2,
                "pb": 0.65,
                "roe": 12.5,
                "debt_ratio": 0.55
            },
            {
                "symbol": "600036.SH",
                "name": "招商银行",
                "current_price": 38.20,
                "target_price": 42.50,
                "pe": 6.8,
                "pb": 1.15,
                "roe": 16.2,
                "debt_ratio": 0.52
            }
        ]
    
    async def _filter_by_quality(self, symbols: List[str], criteria: str) -> List[str]:
        """筛选高质量股票（模拟）"""
        # 实际会调用 financial_data_api
        return ["000001.SZ", "600036.SH"]


# ============ Sentiment Analyst ============

class SentimentAnalyst(Agent):
    """市场情绪分析师"""
    
    system_prompt = """你是一个市场情绪分析专家。
    分析内容：
    1. 监测市场情绪指标（恐慌指数、看涨看跌比）
    2. 分析资金流向（北向资金、融资融券）
    3. 追踪新闻和社交媒体情绪
    4. 识别恐慌性抛售机会
    """
    
    tool_names = ["sentiment_api", "news_api", "social_media_api"]
    
    async def step(self, market_signal: Dict):
        """情绪分析主流程"""
        
        index_name = market_signal["index_name"]
        
        self.log(f"开始情绪分析: {index_name}")
        
        # Step 1: 分析市场情绪指标
        sentiment_indicators = await self._analyze_sentiment_indicators()
        
        # Step 2: 检测是否恐慌性抛售
        is_panic_selling = sentiment_indicators["fear_index"] > 70
        
        # Step 3: 如果是恐慌性抛售，通知风险评估师
        if is_panic_selling:
            self.log("检测到恐慌性抛售，通知风险评估师...")
            await self.share_with_peer(
                peer_name="RiskAssessor",
                information={
                    "type": "panic_selling_alert",
                    "fear_index": sentiment_indicators["fear_index"],
                    "severity": "HIGH"
                }
            )
        
        # Step 4: 分析资金流向
        capital_flow = await self._analyze_capital_flow()
        
        # Step 5: 综合情绪评估
        market_sentiment = self._assess_market_sentiment(
            sentiment_indicators,
            capital_flow
        )
        
        return {
            "market_sentiment": market_sentiment,  # "FEAR" / "NEUTRAL" / "GREED"
            "fear_index": sentiment_indicators["fear_index"],
            "put_call_ratio": sentiment_indicators["put_call_ratio"],
            "north_capital_flow": capital_flow["north_capital"],
            "margin_trading": capital_flow["margin_trading"],
            "news_sentiment": sentiment_indicators["news_sentiment"],
            "analysis_timestamp": datetime.now().isoformat()
        }
    
    async def _analyze_sentiment_indicators(self) -> Dict:
        """分析情绪指标（模拟）"""
        return {
            "fear_index": 75,  # 恐慌指数（0-100，越高越恐慌）
            "put_call_ratio": 1.35,  # 看跌/看涨比率（>1 表示看跌情绪占优）
            "news_sentiment": -0.45  # 新闻情绪（-1到1）
        }
    
    async def _analyze_capital_flow(self) -> Dict:
        """分析资金流向（模拟）"""
        return {
            "north_capital": -580,  # 北向资金流出（百万）
            "margin_trading": -320   # 融资融券流出（百万）
        }
    
    def _assess_market_sentiment(self, indicators: Dict, capital: Dict) -> str:
        """综合评估市场情绪"""
        if indicators["fear_index"] > 65:
            return "FEAR"
        elif indicators["fear_index"] < 35:
            return "GREED"
        else:
            return "NEUTRAL"


# ============ Risk Assessor ============

class RiskAssessor(Agent):
    """风险评估师"""
    
    system_prompt = """你是一个风险管理专家。
    评估内容：
    1. 计算市场波动率和系统性风险
    2. 监测外部风险因素（美股、地缘政治）
    3. 评估组合风险敞口
    4. 给出止损和对冲建议
    """
    
    tool_names = ["risk_api", "volatility_calculator"]
    
    async def step(self, market_signal: Dict):
        """风险评估主流程"""
        
        index_name = market_signal["index_name"]
        
        self.log(f"开始风险评估: {index_name}")
        
        # Step 1: 计算波动率
        volatility = await self._calculate_volatility(index_name)
        
        # Step 2: 检查外部风险
        external_risks = await self._check_external_risks()
        
        # Step 3: 等待情绪分析师的恐慌预警（异步）
        panic_alert = None
        try:
            panic_msg = await self.wait_for_peer_message(
                from_peer="SentimentAnalyst",
                message_type="panic_selling_alert",
                timeout=5.0
            )
            panic_alert = panic_msg.get("information", {})
            self.log(f"收到恐慌预警: {panic_alert}")
        except TimeoutError:
            self.log("未收到恐慌预警")
        
        # Step 4: 综合风险评估
        overall_risk = self._assess_overall_risk(
            volatility,
            external_risks,
            panic_alert
        )
        
        # Step 5: 如果系统性风险高，广播给所有分析师
        if overall_risk == "HIGH":
            self.log("检测到系统性风险，广播预警...")
            await self.broadcast_to_all_peers(
                message="系统性风险预警",
                data={
                    "risk_level": "HIGH",
                    "risk_factors": external_risks["factors"]
                }
            )
        
        return {
            "overall_risk": overall_risk,  # "LOW" / "MEDIUM" / "HIGH"
            "volatility": volatility,
            "risk_factors": external_risks["factors"],
            "suggested_stop_loss": 0.92,  # 建议止损位（-8%）
            "position_size_limit": 0.2 if overall_risk == "LOW" else 0.1,
            "analysis_timestamp": datetime.now().isoformat()
        }
    
    async def on_peer_share(self, information: Dict, sender_name: str):
        """处理来自其他分析师的风险信息"""
        if information.get("type") == "panic_selling_alert":
            self.log(f"收到 {sender_name} 的恐慌预警: {information}")
            # 可以在这里更新内部风险模型
    
    async def _calculate_volatility(self, index: str) -> Dict:
        """计算波动率（模拟）"""
        return {
            "historical_volatility": 0.22,  # 历史波动率
            "implied_volatility": 0.28,      # 隐含波动率
            "vix_equivalent": 24.5
        }
    
    async def _check_external_risks(self) -> Dict:
        """检查外部风险（模拟）"""
        return {
            "factors": ["美股期货下跌2%", "美联储利率决议临近"],
            "severity": "MEDIUM"
        }
    
    def _assess_overall_risk(self, vol: Dict, external: Dict, panic: Dict) -> str:
        """综合风险评估"""
        # 简单规则：外部风险高 或 恐慌指数>80 → HIGH
        if external["severity"] == "HIGH":
            return "HIGH"
        if panic and panic.get("severity") == "HIGH":
            return "HIGH"
        if vol["vix_equivalent"] > 30:
            return "HIGH"
        elif vol["vix_equivalent"] > 20:
            return "MEDIUM"
        else:
            return "LOW"


# ============ Swarm Creation ============

def create_csi_a500_investment_swarm(architecture: str = "hybrid") -> Any:
    """创建中证A500投资决策系统
    
    Args:
        architecture: "single" / "team" / "hybrid"
    """
    from aworld.core.agent.swarm import GraphBuildType
    
    # 创建所有 agents
    coordinator = InvestmentCoordinator(name="InvestmentCoordinator")
    technical = TechnicalAnalyst(name="TechnicalAnalyst")
    fundamental = FundamentalAnalyst(name="FundamentalAnalyst")
    sentiment = SentimentAnalyst(name="SentimentAnalyst")
    risk_assessor = RiskAssessor(name="RiskAssessor")
    
    if architecture == "single":
        # 单代理模式：所有分析由一个 agent 完成
        return coordinator  # Single agent swarm
    
    elif architecture == "team":
        # Team 模式（Centralized）：无 peer 通信
        from aworld.core.agent.swarm import TeamSwarm
        return TeamSwarm(
            coordinator,
            technical,
            fundamental,
            sentiment,
            risk_assessor,
            root_agent=coordinator
        )
    
    elif architecture == "hybrid":
        # Hybrid 模式：层级 + Peer 通信
        return HybridSwarm(
            coordinator,
            technical,
            fundamental,
            sentiment,
            risk_assessor,
            root_agent=coordinator,
            peer_connections=[
                # Enable cross-validation between analysts
                (technical, fundamental),      # 技术 ←→ 基本面验证
                (sentiment, risk_assessor),    # 情绪 ←→ 风险关联
                (fundamental, sentiment),      # 基本面 ←→ 情绪协作
                (technical, risk_assessor)     # 技术 ←→ 风险止损
            ]
        )
    
    else:
        raise ValueError(f"Unknown architecture: {architecture}")
```

## 4. 测试执行

### 4.1 模拟输入数据

```python
# 模拟中证A500下跌1%的市场信号
market_signal = {
    "index_name": "中证A500",
    "index_code": "000510.SH",
    "current_level": 1450.50,
    "change_pct": -1.0,
    "change_points": -14.66,
    "timestamp": "2026-04-03 14:30:00",
    "volume": 185000000000,  # 成交额（亿）
    "turnover_rate": 1.25
}
```

### 4.2 运行测试

```python
# tests/hybrid_validation/test_csi_a500_investment.py

import asyncio
from aworld.runner import Runners

async def test_investment_decision():
    """测试投资决策系统"""
    
    # Test 1: Single Agent
    single_swarm = create_csi_a500_investment_swarm("single")
    result_single = await Runners.async_run(
        input=market_signal,
        swarm=single_swarm
    )
    print("Single Agent Result:", result_single)
    
    # Test 2: Team (Centralized)
    team_swarm = create_csi_a500_investment_swarm("team")
    result_team = await Runners.async_run(
        input=market_signal,
        swarm=team_swarm
    )
    print("Team Result:", result_team)
    
    # Test 3: Hybrid
    hybrid_swarm = create_csi_a500_investment_swarm("hybrid")
    result_hybrid = await Runners.async_run(
        input=market_signal,
        swarm=hybrid_swarm
    )
    print("Hybrid Result:", result_hybrid)
    
    # Compare
    compare_results([
        ("Single", result_single),
        ("Team", result_team),
        ("Hybrid", result_hybrid)
    ])

if __name__ == "__main__":
    asyncio.run(test_investment_decision())
```

### 4.3 评估指标

```python
def evaluate_investment_decision(result: Dict, ground_truth: Dict = None) -> Dict:
    """评估投资决策质量"""
    
    metrics = {
        "execution_time_ms": result["execution_time_ms"],
        "decision_made": result["decision"] in ["BUY", "HOLD", "AVOID"],
        "confidence": result["confidence"],
        "has_reasoning": bool(result.get("reasoning")),
        "has_risk_assessment": bool(result.get("risk_assessment")),
    }
    
    # 如果是 BUY 决策，检查是否有完整的执行计划
    if result["decision"] == "BUY":
        target_stocks = result.get("target_stocks", [])
        metrics["has_targets"] = len(target_stocks) > 0
        metrics["has_stop_loss"] = all("stop_loss" in s for s in target_stocks)
        metrics["has_take_profit"] = all("take_profit" in s for s in target_stocks)
        
        # 决策完整性得分
        completeness = (
            metrics["has_targets"] * 0.4 +
            metrics["has_stop_loss"] * 0.3 +
            metrics["has_take_profit"] * 0.3
        )
        metrics["completeness_score"] = completeness
    
    # Hybrid特有：统计peer通信次数
    reports = result.get("reports", {})
    if reports:
        # 通过日志或report结构推断peer通信次数
        metrics["peer_communications"] = estimate_peer_comms(reports)
    
    return metrics


def estimate_peer_comms(reports: Dict) -> int:
    """估算peer通信次数（基于报告内容）"""
    # 实际实现会从EventManager日志中统计
    # 这里简化估算
    count = 0
    
    tech_report = reports.get("technical", {})
    if tech_report.get("entry_timing"):
        count += 2  # ask_peer + request_peer_action
    
    fund_report = reports.get("fundamental", {})
    if fund_report.get("recommended_stocks"):
        count += 1  # request_peer_action
    
    risk_report = reports.get("risk", {})
    if risk_report.get("overall_risk") == "HIGH":
        count += 1  # broadcast
    
    return count
```

## 5. 预期结果

### 5.1 Performance Comparison

| 架构 | 执行时间 | 决策质量 | Peer通信 | 综合得分 | 说明 |
|------|---------|---------|---------|---------|------|
| Single | 180-200s | 70/100 | 0 | Baseline | 顺序执行所有分析 |
| Team | 100-120s | 82/100 | 0 | +35% | 并行执行，但信息孤岛 |
| **Hybrid** | **80-100s** | **88/100** | **6-8次** | **+45%** | 并行 + 协作验证 |

### 5.2 Hybrid 优势体现

**1. 质量提升（+6分 vs Team）：**
- ✅ 技术面 ←→ 基本面交叉验证，避免虚假信号
- ✅ 基本面发现低估股 → 技术面确认最佳入场时机
- ✅ 情绪恐慌预警 → 风险评估动态调整

**2. 效率提升（-20s vs Team）：**
- ✅ 减少中心瓶颈：分析师间直接通信，不经过 Coordinator
- ✅ 避免重复查询：TechnicalAnalyst 请求 FundamentalAnalyst 筛选后的股票列表
- ✅ 实时信息同步：SentimentAnalyst 恐慌预警实时广播

**3. 协作效果：**
- ✅ 6-8 次有效 peer 通信
- ✅ 信息复用率 > 40%（避免重复 API 调用）
- ✅ 决策置信度提升 8-10%

### 5.3 典型决策场景

**场景 A: 恐慌性抛售 + 估值支撑（Hybrid 优势最大）**
```
输入: 中证A500 -1%, 恐慌指数 75
Hybrid 流程:
1. TechnicalAnalyst 发现支撑位 @ 1445
2. 询问 FundamentalAnalyst 估值 → 回复"PE 12.5 低于中位数"
3. SentimentAnalyst 检测恐慌 → 通知 RiskAssessor
4. RiskAssessor 评估风险 = MEDIUM（恐慌但非系统性）
5. Coordinator 综合决策: BUY（置信度 0.85）

Team 流程:
1-4. 各分析师独立工作，无交叉验证
5. Coordinator 基于孤立报告决策: BUY（置信度 0.72）

结果: Hybrid 置信度高 13%，且选股更精准
```

**场景 B: 系统性风险（Hybrid 快速响应）**
```
输入: 中证A500 -1%, 美股期货大跌
Hybrid 流程:
1. RiskAssessor 检测到外部风险 → 广播 "系统性风险"
2. 所有分析师收到广播 → 调整为防御策略
3. Coordinator 快速决策: AVOID

Team 流程:
1. RiskAssessor 报告风险
2. 其他分析师仍按常规流程分析
3. Coordinator 最后才看到风险报告

结果: Hybrid 响应时间快 30-40s
```

## 6. 成功标准

### 6.1 必须满足

- ✅ Hybrid 决策质量 ≥ Team 质量
- ✅ Hybrid 执行时间 ≤ Team 时间 × 1.2
- ✅ Peer 通信次数 > 0 且有实际作用
- ✅ Hybrid 决策置信度 ≥ Team 置信度

### 6.2 期望达到

- 🎯 质量提升: +5% to +10% vs Team
- 🎯 效率提升: -15% to -20% 时间 vs Team
- 🎯 置信度提升: +8% to +12% vs Team
- 🎯 Peer 通信有效率 > 80%

## 7. 下一步

1. **实现 Hybrid 基础架构**（优先级最高）
2. 实现中证A500投资决策 Agents
3. 建立 baseline（Single + Team）
4. 运行对比测试
5. 分析结果并优化
6. 撰写 benchmark 报告

---

**Document Version:** 1.0  
**Status:** Ready for Implementation  
**Estimated Time:** 3-4 days total

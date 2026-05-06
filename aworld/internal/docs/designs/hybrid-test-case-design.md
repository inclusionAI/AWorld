# Hybrid Swarm 验证测试用例设计

**Date:** 2026-04-03  
**Purpose:** 设计能够充分验证 Hybrid 架构优势的测试场景  
**Reference:** Google Research "Towards a science of scaling agent systems"

## 1. 测试用例设计原则

### 1.1 基于论文的关键发现

**Hybrid 架构的理想场景：**
- ✅ 任务可以分解成多个子任务（并行化潜力）
- ✅ 子任务之间有**部分依赖**（需要 peer 协作）
- ✅ 需要中心协调和最终综合（层级监督）
- ✅ 工具数量适中（<16 个，避免工具协调开销）

**预期性能对比：**
| 架构 | 适用场景 | 预期表现 |
|------|---------|---------|
| Single-Agent | 严格顺序任务 | Baseline |
| Team (Centralized) | 完全独立子任务 | +80.9% (论文数据) |
| **Hybrid** | **部分依赖子任务** | **+15% to +25% vs Team** |
| Handoff (Decentralized) | 复杂协作推理 | 可能过度通信 |

### 1.2 测试设计目标

1. **量化对比:** 同一任务在不同架构下的性能差异
2. **可重现:** 标准化输入和评估指标
3. **现实性:** 模拟真实应用场景
4. **可观测:** 能够追踪 peer 通信和层级调用

## 2. 测试场景：中证A500指数投资决策系统

### 2.1 任务描述

**场景：** 中证A500指数下跌1个点时的投资机会分析与决策

**输入：**
```json
{
  "product": "AI-powered Code Review Tool",
  "target_market": "Enterprise Software Development Teams",
  "budget": "$50,000",
  "timeline": "Q2 2026"
}
```

**预期输出：**
```json
{
  "executive_summary": "...",
  "competitor_analysis": {
    "top_3_competitors": [...],
    "feature_comparison": {...},
    "pricing_analysis": {...}
  },
  "market_trends": {
    "key_trends": [...],
    "growth_projections": {...}
  },
  "customer_insights": {
    "pain_points": [...],
    "feature_requests": [...]
  },
  "technical_assessment": {
    "feasibility_score": 0.85,
    "tech_stack_recommendations": [...]
  },
  "go_to_market_strategy": {...}
}
```

### 2.2 子任务分解

**4 个执行器代理 + 1 个协调器：**

```
                ResearchCoordinator (Root)
                    /    |    |    \
                   /     |    |     \
      Competitor  ←→  Market  ←→  Customer  ←→  Technical
       Analyst         Analyst     Analyst      Evaluator
```

**子任务依赖关系：**

| 子任务 | 执行器 | 输入依赖 | Peer 协作需求 |
|--------|--------|---------|--------------|
| 竞品分析 | CompetitorAnalyst | 无 | 需要 MarketAnalyst 的趋势数据 |
| 市场趋势 | MarketAnalyst | 无 | 需要 CompetitorAnalyst 的定价信息 |
| 客户洞察 | CustomerAnalyst | 竞品分析 | 需要 TechnicalEvaluator 的技术评估 |
| 技术评估 | TechnicalEvaluator | 竞品分析 | 需要 CustomerAnalyst 的需求数据 |
| 最终综合 | ResearchCoordinator | 所有子任务 | - |

**关键特性：**
- ✅ 4 个子任务可以部分并行
- ✅ 子任务之间有依赖（CompetitorAnalyst ←→ MarketAnalyst）
- ✅ 需要中心协调（最终综合报告）
- ✅ 工具数量：6-8 个（Web Search, Data Analysis, API Calls 等）

### 2.3 实现代码

```python
# ============ Agent Definitions ============

class ResearchCoordinator(Agent):
    """Root agent coordinating market research."""
    
    system_prompt = """You are a market research coordinator. 
    Your job is to delegate research tasks to specialists and synthesize their findings.
    Always ensure comprehensive coverage of competitors, market trends, customers, and technology."""
    
    async def step(self, task_input):
        product = task_input["product"]
        target_market = task_input["target_market"]
        
        # Phase 1: Parallel initial research (via handoffs)
        competitor_analysis = await self.call_agent(
            "CompetitorAnalyst",
            {"product": product, "market": target_market}
        )
        
        market_trends = await self.call_agent(
            "MarketAnalyst",
            {"product": product, "market": target_market}
        )
        
        # Phase 2: Deep dives with context
        customer_insights = await self.call_agent(
            "CustomerAnalyst",
            {"product": product, "competitors": competitor_analysis}
        )
        
        technical_assessment = await self.call_agent(
            "TechnicalEvaluator",
            {"product": product, "competitors": competitor_analysis}
        )
        
        # Phase 3: Synthesize
        final_report = self._synthesize_report(
            competitor_analysis,
            market_trends,
            customer_insights,
            technical_assessment
        )
        
        return final_report


class CompetitorAnalyst(Agent):
    """Analyze competitors."""
    
    system_prompt = """You are a competitive intelligence analyst.
    Identify top competitors, analyze their features, pricing, and market positioning.
    Collaborate with MarketAnalyst for trend context."""
    
    tool_names = ["web_search", "company_data_api", "github_search"]
    
    async def step(self, task_input):
        product = task_input["product"]
        
        # Find competitors
        competitors = await self._find_competitors(product)
        
        # Peer collaboration: Get market trends for context
        market_trends = await self.ask_peer(
            peer_name="MarketAnalyst",
            question=f"What are the key market trends for {product}?",
            context={"product": product}
        )
        
        # Analyze features
        feature_comparison = await self._compare_features(
            competitors,
            market_context=market_trends
        )
        
        # Share pricing data with MarketAnalyst
        await self.share_with_peer(
            peer_name="MarketAnalyst",
            information={
                "type": "competitor_pricing",
                "data": self._extract_pricing(competitors)
            }
        )
        
        return {
            "competitors": competitors,
            "features": feature_comparison,
            "market_positioning": self._analyze_positioning(competitors, market_trends)
        }
    
    async def on_peer_question(self, question, context, sender_name):
        """Handle questions from peers."""
        if "pricing" in question.lower():
            competitors = context.get("competitors", [])
            return self._extract_pricing(competitors)
        
        if "feature" in question.lower():
            feature = context.get("feature")
            return self._check_competitor_features(feature)
        
        return "No relevant competitor data."


class MarketAnalyst(Agent):
    """Analyze market trends."""
    
    system_prompt = """You are a market trends analyst.
    Identify growth trends, market size, and future projections.
    Use competitor pricing data from CompetitorAnalyst."""
    
    tool_names = ["web_search", "market_data_api", "trend_analysis"]
    
    async def step(self, task_input):
        market = task_input["market"]
        
        # Analyze trends
        trends = await self._analyze_market_trends(market)
        
        # Wait for competitor pricing data (async)
        try:
            pricing_data = await self.wait_for_peer_message(
                from_peer="CompetitorAnalyst",
                message_type="competitor_pricing",
                timeout=15.0
            )
            
            # Enrich trend analysis with pricing context
            trends["pricing_trends"] = self._analyze_pricing_trends(
                pricing_data.get("data", [])
            )
        except TimeoutError:
            logger.warning("No pricing data from CompetitorAnalyst")
        
        # Broadcast key findings to all peers
        await self.broadcast_to_all_peers(
            message="Market analysis complete",
            data={
                "key_trends": trends["key_trends"][:3],
                "growth_rate": trends.get("growth_rate")
            }
        )
        
        return trends
    
    async def on_peer_question(self, question, context, sender_name):
        """Respond to market trend questions."""
        product = context.get("product")
        
        if product:
            trends = await self._analyze_market_trends(product)
            return f"Key trends for {product}: {trends.get('key_trends', [])}"
        
        return "Please provide product context."


class CustomerAnalyst(Agent):
    """Analyze customer needs and pain points."""
    
    system_prompt = """You are a customer insights analyst.
    Gather customer feedback, identify pain points, and feature requests.
    Coordinate with TechnicalEvaluator on technical feasibility."""
    
    tool_names = ["web_search", "social_media_api", "review_scraper"]
    
    async def step(self, task_input):
        product = task_input["product"]
        competitors_info = task_input.get("competitors", {})
        
        # Gather customer data
        pain_points = await self._gather_pain_points(product, competitors_info)
        feature_requests = await self._gather_feature_requests(product)
        
        # Peer collaboration: Check technical feasibility
        feasibility_check = await self.request_peer_action(
            peer_name="TechnicalEvaluator",
            action="assess_feature_feasibility",
            parameters={
                "features": feature_requests[:5],
                "product": product
            },
            timeout=30.0
        )
        
        # Filter requests by feasibility
        prioritized_features = self._prioritize_features(
            feature_requests,
            feasibility_check.get("assessments", [])
        )
        
        return {
            "pain_points": pain_points,
            "feature_requests": prioritized_features,
            "customer_segments": self._segment_customers(pain_points)
        }


class TechnicalEvaluator(Agent):
    """Evaluate technical feasibility and architecture."""
    
    system_prompt = """You are a technical architecture evaluator.
    Assess technical feasibility, recommend tech stacks, and identify risks.
    Respond to feasibility queries from CustomerAnalyst."""
    
    tool_names = ["github_search", "tech_stack_api", "code_analysis"]
    
    async def step(self, task_input):
        product = task_input["product"]
        competitors = task_input.get("competitors", {})
        
        # Technical assessment
        tech_analysis = await self._analyze_tech_stacks(competitors)
        
        # Request customer needs
        customer_needs = await self.ask_peer(
            peer_name="CustomerAnalyst",
            question="What are the top 3 customer pain points?",
            context={"product": product}
        )
        
        # Match tech solutions to customer needs
        recommended_stack = self._recommend_tech_stack(
            customer_needs,
            tech_analysis
        )
        
        return {
            "feasibility_score": self._calculate_feasibility(recommended_stack),
            "tech_stack_recommendations": recommended_stack,
            "technical_risks": self._identify_risks(recommended_stack)
        }
    
    async def on_peer_action_request(self, action, parameters, sender_name):
        """Handle technical evaluation requests."""
        if action == "assess_feature_feasibility":
            features = parameters.get("features", [])
            assessments = []
            
            for feature in features:
                score = await self._assess_feasibility(feature)
                assessments.append({
                    "feature": feature,
                    "feasibility_score": score,
                    "complexity": self._estimate_complexity(feature)
                })
            
            return {
                "status": "success",
                "assessments": assessments
            }
        
        return {"status": "error", "message": f"Unknown action: {action}"}


# ============ Swarm Configuration ============

def create_market_research_swarm(architecture: str = "hybrid"):
    """Create market research swarm with specified architecture.
    
    Args:
        architecture: "single", "team", or "hybrid"
    """
    coordinator = ResearchCoordinator(name="ResearchCoordinator")
    competitor_analyst = CompetitorAnalyst(name="CompetitorAnalyst")
    market_analyst = MarketAnalyst(name="MarketAnalyst")
    customer_analyst = CustomerAnalyst(name="CustomerAnalyst")
    technical_evaluator = TechnicalEvaluator(name="TechnicalEvaluator")
    
    if architecture == "single":
        # Single agent does everything
        return SingleAgentSwarm(coordinator)
    
    elif architecture == "team":
        # Team (Centralized): No peer communication
        return TeamSwarm(
            coordinator,
            competitor_analyst,
            market_analyst,
            customer_analyst,
            technical_evaluator,
            root_agent=coordinator
        )
    
    elif architecture == "hybrid":
        # Hybrid: Hierarchical + Peer communication
        return HybridSwarm(
            coordinator,
            competitor_analyst,
            market_analyst,
            customer_analyst,
            technical_evaluator,
            root_agent=coordinator,
            peer_connections=[
                # Enable peer collaboration
                (competitor_analyst, market_analyst),
                (customer_analyst, technical_evaluator),
                (competitor_analyst, customer_analyst)
            ]
        )
    
    else:
        raise ValueError(f"Unknown architecture: {architecture}")
```

### 2.4 评估指标

**主要指标：**

1. **任务完成质量 (Accuracy):**
   - 竞品识别准确率
   - 市场趋势覆盖度
   - 技术评估正确性
   - 最终报告完整性分数（0-100）

2. **执行效率 (Efficiency):**
   - 总执行时间（秒）
   - Token 消耗量
   - API 调用次数
   - Agent 交互轮次

3. **协作效果 (Collaboration):**
   - Peer 通信次数
   - 信息共享有效性
   - 跨 Agent 数据复用率

4. **错误率 (Error Rate):**
   - 任务失败率
   - 错误传播次数
   - 重试次数

**评估公式：**
```
Performance Score = (Quality × 0.5) + (1/Time × 0.3) + (Collaboration × 0.2)

其中：
- Quality: 0-100 (专家评分或自动评估)
- Time: 秒数 (归一化)
- Collaboration: Peer通信有效性 0-100
```

### 2.5 预期结果

**Hypothesis:**

| 架构 | 质量分数 | 执行时间 | Peer通信 | 综合得分 |
|------|---------|---------|---------|---------|
| Single-Agent | 70 | 180s | 0 | **Baseline** |
| Team (Centralized) | 85 | 120s | 0 | +30% |
| **Hybrid** | **90** | **100s** | **12次** | **+45%** ✨ |

**Hybrid 优势体现：**
1. ✅ **质量提升:** Peer 协作避免信息孤岛（+5分 vs Team）
2. ✅ **效率提升:** 减少中心瓶颈，子任务并行执行（-20s vs Team）
3. ✅ **协作效果:** CompetitorAnalyst ←→ MarketAnalyst 数据共享避免重复查询

## 3. 测试场景二：复杂问题求解（GAIA-Inspired）

### 3.1 任务描述

**场景：** 多步骤信息检索和推理任务

**示例任务（GAIA Level 3 风格）：**
> "In 2026, what is the total market capitalization of the top 3 AI infrastructure companies 
> that have launched new GPU products in the past 6 months and have partnerships with 
> at least 2 major cloud providers? Provide the breakdown by company."

**子任务分解：**
1. **NewsResearcher:** 搜索过去 6 个月发布 GPU 产品的公司
2. **PartnershipAnalyst:** 验证云服务商合作关系
3. **FinancialAnalyst:** 获取市值数据
4. **DataValidator:** 交叉验证数据一致性

**Peer 协作场景：**
- NewsResearcher 发现公司 → 询问 PartnershipAnalyst 是否满足条件
- PartnershipAnalyst 验证后 → 通知 FinancialAnalyst 查询市值
- DataValidator 从各 Agent 收集数据 → 交叉验证

### 3.2 实现概要

```python
class MultiStepCoordinator(Agent):
    """Coordinates multi-step reasoning."""
    
    async def step(self, question):
        # Decompose question
        subtasks = self._decompose_question(question)
        
        # Execute with coordination
        results = await self._execute_subtasks_with_coordination(subtasks)
        
        # Synthesize answer
        return self._synthesize_answer(results)


class NewsResearcher(Agent):
    """Search for recent news and announcements."""
    
    async def step(self, query):
        companies = await self._search_gpu_launches(query)
        
        # Peer collaboration: Verify partnerships
        verified_companies = []
        for company in companies:
            is_qualified = await self.ask_peer(
                peer_name="PartnershipAnalyst",
                question=f"Does {company} have 2+ cloud partnerships?",
                context={"company": company, "min_partnerships": 2}
            )
            
            if is_qualified == "yes":
                verified_companies.append(company)
        
        return verified_companies


class PartnershipAnalyst(Agent):
    """Verify partnership relationships."""
    
    async def on_peer_question(self, question, context, sender_name):
        company = context.get("company")
        min_partnerships = context.get("min_partnerships", 2)
        
        partnerships = await self._check_cloud_partnerships(company)
        
        if len(partnerships) >= min_partnerships:
            # Notify FinancialAnalyst to fetch market cap
            await self.share_with_peer(
                peer_name="FinancialAnalyst",
                information={
                    "type": "qualified_company",
                    "company": company,
                    "partnerships": partnerships
                }
            )
            return "yes"
        
        return "no"


class FinancialAnalyst(Agent):
    """Get financial data."""
    
    async def on_peer_share(self, information, sender_name):
        if information.get("type") == "qualified_company":
            company = information["company"]
            market_cap = await self._get_market_cap(company)
            
            # Share with validator
            await self.share_with_peer(
                peer_name="DataValidator",
                information={
                    "company": company,
                    "market_cap": market_cap,
                    "source": "FinancialAPI"
                }
            )
```

## 4. 测试执行计划

### 4.1 Benchmark 运行

```bash
# Step 1: Establish baseline with existing architectures
cd examples/gaia
python run.py --split validation --start 0 --end 50 --architecture single
python run.py --split validation --start 0 --end 50 --architecture team

# Step 2: Run with Hybrid architecture
python run.py --split validation --start 0 --end 50 --architecture hybrid

# Step 3: Compare results
python compare_architectures.py --baseline single --compare team hybrid
```

### 4.2 自定义测试

```bash
# Run market research test case
cd tests/hybrid_validation
python test_market_research.py --architecture single
python test_market_research.py --architecture team
python test_market_research.py --architecture hybrid

# Generate comparison report
python generate_report.py --output hybrid_validation_report.md
```

### 4.3 评估脚本

```python
# tests/hybrid_validation/evaluate.py

def evaluate_market_research(result: Dict, ground_truth: Dict) -> float:
    """Evaluate market research quality."""
    
    # Competitor coverage
    competitor_score = len(
        set(result["competitors"]) & set(ground_truth["competitors"])
    ) / len(ground_truth["competitors"])
    
    # Trend accuracy
    trend_score = calculate_trend_overlap(
        result["market_trends"],
        ground_truth["market_trends"]
    )
    
    # Technical feasibility
    tech_score = abs(
        result["technical_feasibility"] - ground_truth["technical_feasibility"]
    ) / ground_truth["technical_feasibility"]
    
    # Report completeness
    completeness = check_report_sections(result)
    
    final_score = (
        competitor_score * 0.3 +
        trend_score * 0.3 +
        (1 - tech_score) * 0.2 +
        completeness * 0.2
    ) * 100
    
    return final_score


def compare_architectures(results: Dict[str, List[Dict]]) -> pd.DataFrame:
    """Compare performance across architectures."""
    
    comparison = []
    
    for arch, arch_results in results.items():
        comparison.append({
            "Architecture": arch,
            "Avg Quality": np.mean([r["quality"] for r in arch_results]),
            "Avg Time (s)": np.mean([r["time"] for r in arch_results]),
            "Peer Communications": np.mean([r.get("peer_comms", 0) for r in arch_results]),
            "Token Usage": np.mean([r["tokens"] for r in arch_results]),
            "Success Rate": np.mean([r["success"] for r in arch_results]) * 100
        })
    
    df = pd.DataFrame(comparison)
    
    # Calculate relative improvement
    baseline = df[df["Architecture"] == "single"].iloc[0]
    df["Quality Δ%"] = ((df["Avg Quality"] - baseline["Avg Quality"]) / baseline["Avg Quality"] * 100).round(1)
    df["Time Δ%"] = ((baseline["Avg Time (s)"] - df["Avg Time (s)"]) / baseline["Avg Time (s)"] * 100).round(1)
    
    return df
```

## 5. 成功标准

### 5.1 验证目标

**Hybrid 架构应该满足：**

1. ✅ **质量优于 Team:** Avg Quality Score > Team Score
2. ✅ **效率不低于 Team:** Avg Time ≤ Team Time × 1.2
3. ✅ **Peer 通信有效:** Peer Communication Count > 0 且有实际作用
4. ✅ **错误率不高于 Team:** Error Rate ≤ Team Error Rate

### 5.2 预期提升范围

基于论文发现，Hybrid 在部分依赖任务上的预期提升：

| 指标 | 预期提升（vs Team） |
|------|-------------------|
| 任务质量 | +5% to +10% |
| 执行效率 | +10% to +20% |
| 综合得分 | +15% to +25% |

### 5.3 失败判定

**如果出现以下情况，需要重新审视设计：**

- ❌ Hybrid Quality < Team Quality（Peer 通信引入噪声）
- ❌ Hybrid Time > Team Time × 1.5（协调开销过大）
- ❌ Peer 通信次数 > 50（过度通信）
- ❌ Error Rate > Team Error Rate × 1.2（错误放大）

## 6. 下一步行动

1. **实现市场研究测试用例**（当前优先级最高）
2. 运行 baseline (Single + Team)
3. 实现 Hybrid 架构
4. 对比评估
5. 根据结果调优
6. 扩展到 GAIA benchmark 子集

---

**Document Version:** 1.0  
**Status:** Ready for Implementation  
**Estimated Time:** 2-3 days for test case + baseline

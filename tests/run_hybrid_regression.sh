#!/bin/bash
# Run Hybrid implementation regression tests

set -e  # Exit on error

echo "=================================="
echo "HYBRID REGRESSION TEST SUITE"
echo "=================================="
echo ""

# Color codes
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Track results
TOTAL_PASSED=0
TOTAL_FAILED=0

# Test 1: Core regression tests
echo "Test 1: Core Swarm Regression Tests"
echo "------------------------------------"
if python tests/core/test_swarm_regression.py; then
    echo -e "${GREEN}✅ Core regression tests PASSED${NC}"
    ((TOTAL_PASSED++))
else
    echo -e "${RED}❌ Core regression tests FAILED${NC}"
    ((TOTAL_FAILED++))
fi
echo ""

# Test 2: Hybrid architecture tests
echo "Test 2: Hybrid Architecture Tests"
echo "------------------------------------"
cd examples/multi_agents/hybrid/data_processing
if python test_simple.py > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Hybrid architecture tests PASSED${NC}"
    ((TOTAL_PASSED++))
else
    echo -e "${RED}❌ Hybrid architecture tests FAILED${NC}"
    ((TOTAL_FAILED++))
fi
cd - > /dev/null
echo ""

# Test 3: Import validation
echo "Test 3: Import Validation"
echo "------------------------------------"
if python -c "
from aworld.core.agent.swarm import Swarm, GraphBuildType
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig

# Test WORKFLOW
agent1 = Agent(name='a1', conf=AgentConfig())
agent2 = Agent(name='a2', conf=AgentConfig())
swarm = Swarm(agent1, agent2, build_type=GraphBuildType.WORKFLOW)
swarm.reset()
assert swarm.build_type == GraphBuildType.WORKFLOW.value

# Test TEAM
agent1 = Agent(name='a1', conf=AgentConfig())
agent2 = Agent(name='a2', conf=AgentConfig())
swarm = Swarm(agent1, agent2, build_type=GraphBuildType.TEAM)
swarm.reset()
assert swarm.build_type == GraphBuildType.TEAM.value

# Test HANDOFF (requires pairs)
agent1 = Agent(name='a1', conf=AgentConfig())
agent2 = Agent(name='a2', conf=AgentConfig())
swarm = Swarm((agent1, agent2), (agent2, agent1), build_type=GraphBuildType.HANDOFF)
swarm.reset()
assert swarm.build_type == GraphBuildType.HANDOFF.value

# Test HYBRID
agent1 = Agent(name='a1', conf=AgentConfig())
agent2 = Agent(name='a2', conf=AgentConfig())
swarm = Swarm(agent1, agent2, build_type=GraphBuildType.HYBRID)
swarm.reset()
assert swarm.build_type == GraphBuildType.HYBRID.value

print('All build types work correctly')
" 2>&1; then
    echo -e "${GREEN}✅ Import validation PASSED${NC}"
    ((TOTAL_PASSED++))
else
    echo -e "${RED}❌ Import validation FAILED${NC}"
    ((TOTAL_FAILED++))
fi
echo ""

# Summary
echo "=================================="
echo "REGRESSION TEST SUMMARY"
echo "=================================="
echo "Total test suites: $((TOTAL_PASSED + TOTAL_FAILED))"
echo -e "Passed: ${GREEN}${TOTAL_PASSED}${NC}"
echo -e "Failed: ${RED}${TOTAL_FAILED}${NC}"
echo ""

if [ $TOTAL_FAILED -eq 0 ]; then
    echo -e "${GREEN}✅ ALL REGRESSION TESTS PASSED${NC}"
    echo "Hybrid implementation is safe to use!"
    exit 0
else
    echo -e "${RED}❌ SOME TESTS FAILED${NC}"
    echo "Please review the failures above."
    exit 1
fi

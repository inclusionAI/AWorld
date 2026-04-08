#!/bin/bash
# Run all spawn_parallel tests
# Usage: ./run_spawn_parallel_tests.sh [quick|full|example|all]

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored messages
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check prerequisites
check_prerequisites() {
    print_info "Checking prerequisites..."

    # Check Python version
    if ! command -v python &> /dev/null; then
        print_error "Python not found. Please install Python 3.11+"
        exit 1
    fi

    PYTHON_VERSION=$(python --version 2>&1 | awk '{print $2}')
    print_info "Python version: $PYTHON_VERSION"

    # Check .env file
    if [ ! -f .env ]; then
        print_error ".env file not found!"
        print_info "Creating .env from template..."
        cp .env.example .env
        print_warning "Please edit .env and add your LLM API key"
        print_info "Example: LLM_API_KEY=sk-your-api-key-here"
        exit 1
    fi

    # Check if API key is set
    if grep -q "your_api_key_here" .env 2>/dev/null; then
        print_warning ".env contains placeholder API key"
        print_warning "Please update LLM_API_KEY in .env"
    fi

    # Check AWorld installation
    if ! python -c "import aworld" 2>/dev/null; then
        print_error "AWorld not installed. Run: pip install -e ."
        exit 1
    fi

    # Check spawn_subagent tool
    if ! python -c "from aworld.core.tool.builtin import SpawnSubagentTool" 2>/dev/null; then
        print_error "SpawnSubagentTool not found. Check installation."
        exit 1
    fi

    print_info "✓ All prerequisites met"
    echo ""
}

# Function to run quick test
run_quick_test() {
    print_info "========================================="
    print_info "Running Quick Test"
    print_info "========================================="
    echo ""

    if python quick_test_spawn_parallel.py; then
        print_info "✓ Quick test PASSED"
        return 0
    else
        print_error "✗ Quick test FAILED"
        return 1
    fi
}

# Function to run full test suite
run_full_tests() {
    print_info "========================================="
    print_info "Running Full Test Suite"
    print_info "========================================="
    echo ""

    if python test_spawn_parallel_aworld.py; then
        print_info "✓ Full test suite PASSED"
        return 0
    else
        print_error "✗ Full test suite FAILED"
        return 1
    fi
}

# Function to run examples
run_examples() {
    print_info "========================================="
    print_info "Running Examples"
    print_info "========================================="
    echo ""

    if python parallel_spawn_example.py; then
        print_info "✓ Examples completed"
        return 0
    else
        print_error "✗ Examples FAILED"
        return 1
    fi
}

# Main execution
main() {
    local test_type="${1:-quick}"

    print_info "Spawn Parallel Test Runner"
    print_info "Test type: $test_type"
    echo ""

    # Check prerequisites
    check_prerequisites

    # Track results
    local failed=0

    case "$test_type" in
        quick)
            run_quick_test || failed=$((failed + 1))
            ;;

        full)
            run_full_tests || failed=$((failed + 1))
            ;;

        example)
            run_examples || failed=$((failed + 1))
            ;;

        all)
            print_info "Running all tests..."
            echo ""

            run_quick_test || failed=$((failed + 1))
            echo ""
            sleep 2

            run_full_tests || failed=$((failed + 1))
            echo ""
            sleep 2

            run_examples || failed=$((failed + 1))
            ;;

        *)
            print_error "Unknown test type: $test_type"
            echo ""
            echo "Usage: $0 [quick|full|example|all]"
            echo ""
            echo "Options:"
            echo "  quick   - Run quick test (default)"
            echo "  full    - Run full test suite (4 tests)"
            echo "  example - Run usage examples (5 scenarios)"
            echo "  all     - Run all tests"
            exit 1
            ;;
    esac

    # Print summary
    echo ""
    print_info "========================================="
    print_info "Test Summary"
    print_info "========================================="

    if [ $failed -eq 0 ]; then
        print_info "✓ All tests PASSED"
        echo ""
        print_info "Next steps:"
        print_info "1. Read documentation: docs/features/parallel-subagent-spawning.md"
        print_info "2. Check examples: parallel_spawn_example.py"
        print_info "3. Integrate into your project"
        exit 0
    else
        print_error "✗ $failed test(s) FAILED"
        echo ""
        print_info "Troubleshooting:"
        print_info "1. Check .env configuration"
        print_info "2. Verify LLM API key is valid"
        print_info "3. Review TEST_SPAWN_PARALLEL_GUIDE.md"
        print_info "4. Run with verbose logging: LOG_LEVEL=DEBUG"
        exit 1
    fi
}

# Run main with arguments
main "$@"

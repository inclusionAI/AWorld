"""
SymPy MCP Server

This module provides MCP server functionality for symbolic mathematics using the SymPy library.
It supports algebraic operations, calculus, matrix operations, and equation solving.

Key features:
- Perform algebraic simplifications and expansions
- Solve equations and systems of equations
- Perform calculus operations like differentiation and integration
- Handle matrix operations including determinants and eigenvalues

Main functions:
- mcpalgebraic: Performs algebraic operations
- mcpsolve: Solves equations
- mcpcalculus: Performs calculus operations
- mcpmatrix: Handles matrix operations
- mcpsolveode: Solves ordinary differential equations
- mcpsolvelinear: Solves systems of linear equations
"""

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from aworld.logs.util import logger
from aworld.mcp_servers.utils import run_mcp_server
from aworld.utils import import_package

# Import SymPy package, install if not available
import_package("sympy")
import sympy
from sympy import (
    Matrix,
    Symbol,
    det,
    diff,
    eigenvals,
    eigenvects,
    expand,
    factor,
    integrate,
    inv,
    latex,
    limit,
    pretty,
    series,
    simplify,
    solve,
    symbols,
    sympify,
)


# Define model classes for different SymPy operations
class SymbolicResult(BaseModel):
    """Model representing a symbolic computation result"""

    operation: str
    input_expr: str
    result: str
    result_latex: str
    result_pretty: str
    variables: List[str]
    description: str


class EquationResult(BaseModel):
    """Model representing equation solving results"""

    operation: str
    equation: str
    variables: List[str]
    solutions: List[str]
    solutions_latex: List[str]
    description: str


class CalculusResult(BaseModel):
    """Model representing calculus operation results"""

    operation: str
    input_expr: str
    variable: str
    result: str
    result_latex: str
    result_pretty: str
    description: str


class MatrixResult(BaseModel):
    """Model representing matrix operation results"""

    operation: str
    input_matrix: List[List[Any]]
    result: Any
    result_latex: str
    result_pretty: str
    description: str


class SymPyError(BaseModel):
    """Model representing an error in SymPy processing"""

    error: str
    operation: str


def handle_error(e: Exception, operation_type: str) -> str:
    """Unified error handling and return standard format error message"""
    error_msg = f"{operation_type} error: {str(e)}"
    logger.error(error_msg)

    error = SymPyError(error=error_msg, operation=operation_type)

    return error.model_dump_json()


def parse_expression(expr_str: str, var_names: Optional[List[str]] = None):
    """
    Parse a string expression into a SymPy expression.

    Args:
        expr_str: String representation of the expression
        var_names: Optional list of variable names to create symbols for

    Returns:
        SymPy expression and list of symbols
    """
    # Create symbols for variables if provided
    if var_names:
        var_symbols = symbols(var_names)
        if len(var_names) == 1:
            var_symbols = [var_symbols]

        # Create a dictionary of variable symbols
        var_dict = {name: sym for name, sym in zip(var_names, var_symbols)}

        # Parse the expression with the variables
        expr = sympify(expr_str, locals=var_dict)
        return expr, var_symbols
    else:
        # Find all symbols in the expression automatically
        expr = sympify(expr_str)
        var_symbols = list(expr.free_symbols)
        return expr, var_symbols


def mcpalgebraic(
    operation: str = Field(
        description="Algebraic operation (simplify, expand, factor)"
    ),
    expression: str = Field(description="Mathematical expression as a string"),
    variables: Optional[List[str]] = Field(
        default=None, description="List of variable names in the expression"
    ),
) -> str:
    """
    Perform algebraic operations on symbolic expressions.

    Args:
        operation: Type of algebraic operation
        expression: Mathematical expression as a string
        variables: List of variable names in the expression

    Returns:
        JSON string containing the result
    """
    try:
        # Parse the expression
        expr, var_symbols = parse_expression(expression, variables)

        # Get variable names as strings
        var_names = [str(sym) for sym in var_symbols]

        result = None
        description = ""

        if operation == "simplify":
            result = simplify(expr)
            description = "Simplified expression"

        elif operation == "expand":
            result = expand(expr)
            description = "Expanded expression"

        elif operation == "factor":
            result = factor(expr)
            description = "Factored expression"

        else:
            raise ValueError(f"Unknown operation: {operation}")

        # Create result object
        symbolic_result = SymbolicResult(
            operation=operation,
            input_expr=expression,
            result=str(result),
            result_latex=latex(result),
            result_pretty=pretty(result, use_unicode=True),
            variables=var_names,
            description=description,
        )

        return symbolic_result.model_dump_json()

    except Exception as e:
        return handle_error(e, "Algebraic Operation")


def mcpsolve(
    equation: str = Field(
        description="Equation to solve (use '=' to separate left and right sides)"
    ),
    variable: str = Field(description="Variable to solve for"),
    variables: Optional[List[str]] = Field(
        default=None, description="List of all variable names in the equation"
    ),
) -> str:
    """
    Solve an equation for a specific variable.

    Args:
        equation: Equation to solve (use '=' to separate left and right sides)
        variable: Variable to solve for
        variables: List of all variable names in the equation

    Returns:
        JSON string containing the solutions
    """
    try:
        # Ensure variables list includes the variable to solve for
        if variables is None:
            variables = [variable]
        elif variable not in variables:
            variables.append(variable)

        # Parse the equation
        if "=" in equation:
            left_side, right_side = equation.split("=", 1)
            left_expr, _ = parse_expression(left_side, variables)
            right_expr, _ = parse_expression(right_side, variables)
            eq = sympy.Eq(left_expr, right_expr)
        else:
            # If no equals sign, assume equation is set equal to zero
            expr, _ = parse_expression(equation, variables)
            eq = sympy.Eq(expr, 0)

        # Get the symbol for the variable to solve for
        var_sym = Symbol(variable)

        # Solve the equation
        solutions = solve(eq, var_sym)

        # Convert solutions to strings and LaTeX
        solution_strs = [str(sol) for sol in solutions]
        solution_latex = [latex(sol) for sol in solutions]

        # Create result object
        equation_result = EquationResult(
            operation="solve",
            equation=equation,
            variables=variables,
            solutions=solution_strs,
            solutions_latex=solution_latex,
            description=f"Solutions for {variable} in the equation {equation}",
        )

        return equation_result.model_dump_json()

    except Exception as e:
        return handle_error(e, "Equation Solving")


def mcpcalculus(
    operation: str = Field(
        description="Calculus operation (differentiate, integrate, limit, series)"
    ),
    expression: str = Field(description="Mathematical expression as a string"),
    variable: str = Field(
        description="Variable with respect to which the operation is performed"
    ),
    variables: Optional[List[str]] = Field(
        default=None, description="List of all variable names in the expression"
    ),
    point: Optional[float] = Field(
        default=None, description="Point at which to evaluate the limit"
    ),
    order: Optional[int] = Field(
        default=None, description="Order of the derivative or series expansion"
    ),
    lower_limit: Optional[str] = Field(
        default=None, description="Lower limit for definite integral"
    ),
    upper_limit: Optional[str] = Field(
        default=None, description="Upper limit for definite integral"
    ),
) -> str:
    """
    Perform calculus operations on symbolic expressions.

    Args:
        operation: Type of calculus operation
        expression: Mathematical expression as a string
        variable: Variable with respect to which the operation is performed
        variables: List of all variable names in the expression
        point: Point at which to evaluate the limit
        order: Order of the derivative or series expansion
        lower_limit: Lower limit for definite integral
        upper_limit: Upper limit for definite integral

    Returns:
        JSON string containing the result
    """
    try:
        # Ensure variables list includes the main variable
        if variables is None:
            variables = [variable]
        elif variable not in variables:
            variables.append(variable)

        # Parse the expression
        expr, var_symbols = parse_expression(expression, variables)

        # Get the symbol for the main variable
        var_sym = Symbol(variable)

        result = None
        description = ""

        if operation == "differentiate":
            # Differentiate the expression
            order_val = order if order is not None else 1
            result = diff(expr, var_sym, order_val)
            description = f"Derivative of order {order_val} with respect to {variable}"

        elif operation == "integrate":
            # Integrate the expression
            if lower_limit is not None and upper_limit is not None:
                # Definite integral
                lower = sympify(lower_limit)
                upper = sympify(upper_limit)
                result = integrate(expr, (var_sym, lower, upper))
                description = f"Definite integral from {lower_limit} to {upper_limit} with respect to {variable}"
            else:
                # Indefinite integral
                result = integrate(expr, var_sym)
                description = f"Indefinite integral with respect to {variable}"

        elif operation == "limit":
            # Calculate the limit
            if point is None:
                raise ValueError("Point is required for limit calculation")

            result = limit(expr, var_sym, point)
            description = f"Limit as {variable} approaches {point}"

        elif operation == "series":
            # Calculate the series expansion
            if point is None:
                point = 0  # Default to expansion around 0

            order_val = order if order is not None else 6  # Default to 6th order
            result = series(expr, var_sym, point, order_val)
            description = f"Series expansion around {point} up to order {order_val}"

        else:
            raise ValueError(f"Unknown operation: {operation}")

        # Create result object
        calculus_result = CalculusResult(
            operation=operation,
            input_expr=expression,
            variable=variable,
            result=str(result),
            result_latex=latex(result),
            result_pretty=pretty(result, use_unicode=True),
            description=description,
        )

        return calculus_result.model_dump_json()

    except Exception as e:
        return handle_error(e, "Calculus Operation")


def mcpmatrix(
    operation: str = Field(
        description="Matrix operation (determinant, inverse, eigenvalues, eigenvectors, solve)"
    ),
    matrix: List[List[Any]] = Field(description="Matrix as a list of lists"),
    vector: Optional[List[Any]] = Field(
        default=None, description="Vector for solving linear systems"
    ),
) -> str:
    """
    Perform matrix operations using SymPy.

    Args:
        operation: Type of matrix operation
        matrix: Matrix as a list of lists
        vector: Vector for solving linear systems

    Returns:
        JSON string containing the result
    """
    try:
        # Convert the matrix to a SymPy Matrix
        sym_matrix = Matrix(matrix)

        result = None
        description = ""

        if operation == "determinant":
            result = det(sym_matrix)
            description = "Determinant of the matrix"

        elif operation == "inverse":
            if sym_matrix.is_square:
                result = inv(sym_matrix)
                description = "Inverse of the matrix"
            else:
                raise ValueError("Matrix must be square to compute its inverse")

        elif operation == "eigenvalues":
            if sym_matrix.is_square:
                result = eigenvals(sym_matrix)
                # Convert dictionary to a more JSON-friendly format
                result = {str(k): v for k, v in result.items()}
                description = "Eigenvalues of the matrix"
            else:
                raise ValueError("Matrix must be square to compute eigenvalues")

        elif operation == "eigenvectors":
            if sym_matrix.is_square:
                result = eigenvects(sym_matrix)
                # Convert to a more readable format
                formatted_result = []
                for eigenvalue, multiplicity, eigenvectors in result:
                    formatted_result.append(
                        {
                            "eigenvalue": str(eigenvalue),
                            "multiplicity": multiplicity,
                            "eigenvectors": [str(v) for v in eigenvectors],
                        }
                    )
                result = formatted_result
                description = "Eigenvectors of the matrix"
            else:
                raise ValueError("Matrix must be square to compute eigenvectors")

        elif operation == "solve":
            if vector is None:
                raise ValueError("Vector is required for solving linear systems")

            sym_vector = Matrix(vector)
            result = sym_matrix.solve(sym_vector)
            result = [str(x) for x in result]
            description = "Solution to the linear system Ax = b"

        else:
            raise ValueError(f"Unknown operation: {operation}")

        # Create result object
        matrix_result = MatrixResult(
            operation=operation,
            input_matrix=matrix,
            result=result,
            result_latex=latex(result) if not isinstance(result, (dict, list)) else "",
            result_pretty=(
                pretty(result, use_unicode=True)
                if not isinstance(result, (dict, list))
                else ""
            ),
            description=description,
        )

        return matrix_result.model_dump_json()

    except Exception as e:
        return handle_error(e, "Matrix Operation")


def mcpsolveode(
    equation: str = Field(
        description="Differential equation (use 'y' as the function and 'x' as the independent variable)"
    ),
    function_symbol: str = Field(
        default="y", description="Symbol for the function (default: 'y')"
    ),
    variable: str = Field(
        default="x", description="Independent variable (default: 'x')"
    ),
    ics: Optional[Dict[str, float]] = Field(
        default=None, description="Initial conditions as a dictionary"
    ),
) -> str:
    """
    Solve ordinary differential equations.

    Args:
        equation: Differential equation as a string
        function_symbol: Symbol for the function
        variable: Independent variable
        ics: Initial conditions as a dictionary

    Returns:
        JSON string containing the solution
    """
    try:
        # Import specific ODE solver functions
        from sympy import Eq, Function, dsolve

        # Create function and variable symbols
        x = Symbol(variable)
        y = Function(function_symbol)(x)

        # Parse the equation
        if "=" in equation:
            left_side, right_side = equation.split("=", 1)
            left_expr = sympify(
                left_side.replace(
                    f"{function_symbol}'", f"diff({function_symbol}(x), x)"
                ).replace(f"{function_symbol}''", f"diff({function_symbol}(x), x, 2)")
            )
            right_expr = sympify(
                right_side.replace(
                    f"{function_symbol}'", f"diff({function_symbol}(x), x)"
                ).replace(f"{function_symbol}''", f"diff({function_symbol}(x), x, 2)")
            )
            eq = Eq(left_expr, right_expr)
        else:
            # If no equals sign, assume equation is set equal to zero
            expr = sympify(
                equation.replace(
                    f"{function_symbol}'", f"diff({function_symbol}(x), x)"
                ).replace(f"{function_symbol}''", f"diff({function_symbol}(x), x, 2)")
            )
            eq = Eq(expr, 0)

        # Solve the ODE
        if ics:
            # Convert initial conditions to the format expected by dsolve
            ics_dict = {}
            for key, value in ics.items():
                if key == function_symbol:
                    ics_dict[y.subs(x, 0)] = value
                elif key == f"{function_symbol}'":
                    ics_dict[y.diff(x).subs(x, 0)] = value
                elif key == f"{function_symbol}''":
                    ics_dict[y.diff(x, 2).subs(x, 0)] = value
                else:
                    # Handle conditions at points other than 0
                    try:
                        point = float(key.split(",")[1])
                        if key.startswith(function_symbol):
                            ics_dict[y.subs(x, point)] = value
                        elif key.startswith(f"{function_symbol}'"):
                            ics_dict[y.diff(x).subs(x, point)] = value
                    except:
                        raise ValueError(f"Invalid initial condition format: {key}")

            solution = dsolve(eq, y, ics=ics_dict)
        else:
            solution = dsolve(eq, y)

        # Create result object
        result = {
            "operation": "solve_ode",
            "equation": equation,
            "function": function_symbol,
            "variable": variable,
            "solution": str(solution),
            "solution_latex": latex(solution),
            "solution_pretty": pretty(solution, use_unicode=True),
            "description": "Solution to the differential equation",
        }

        return json.dumps(result)

    except Exception as e:
        return handle_error(e, "ODE Solving")


def mcpsolvelinear(
    equations: List[str] = Field(description="List of linear equations"),
    variables: List[str] = Field(description="List of variables to solve for"),
) -> str:
    """
    Solve a system of linear equations.

    Args:
        equations: List of linear equations as strings
        variables: List of variables to solve for

    Returns:
        JSON string containing the solutions
    """
    try:
        # Create symbols for variables
        var_symbols = symbols(variables)
        if len(variables) == 1:
            var_symbols = [var_symbols]

        # Parse equations
        sym_equations = []
        for eq_str in equations:
            if "=" in eq_str:
                left_side, right_side = eq_str.split("=", 1)
                left_expr, _ = parse_expression(left_side, variables)
                right_expr, _ = parse_expression(right_side, variables)
                sym_equations.append(sympy.Eq(left_expr, right_expr))
            else:
                # If no equals sign, assume equation is set equal to zero
                expr, _ = parse_expression(eq_str, variables)
                sym_equations.append(sympy.Eq(expr, 0))

        # Solve the system
        solution = solve(sym_equations, var_symbols)

        # Handle different solution formats
        if isinstance(solution, dict):
            # Dictionary format
            solution_dict = {str(var): str(val) for var, val in solution.items()}
            solutions = [f"{var} = {val}" for var, val in solution_dict.items()]
            solutions_latex = [
                f"{latex(Symbol(var))} = {latex(sympify(val))}"
                for var, val in solution_dict.items()
            ]
        elif isinstance(solution, list):
            if len(solution) > 0 and isinstance(solution[0], dict):
                # List of dictionaries (multiple solution sets)
                solutions = []
                solutions_latex = []
                for i, sol_dict in enumerate(solution):
                    solutions.append(f"Solution set {i+1}:")
                    solutions_latex.append(f"\\text{{Solution set }} {i+1}:")
                    for var, val in sol_dict.items():
                        solutions.append(f"  {var} = {val}")
                        solutions_latex.append(f"  {latex(var)} = {latex(val)}")
            else:
                # List format (for a single variable)
                solutions = [f"{variables[0]} = {str(sol)}" for sol in solution]
                solutions_latex = [
                    f"{latex(Symbol(variables[0]))} = {latex(sol)}" for sol in solution
                ]
        else:
            # Empty solution or other format
            solutions = [str(solution)]
            solutions_latex = [latex(solution)]

        # Create result object
        result = EquationResult(
            operation="solve_linear_system",
            equation=", ".join(equations),
            variables=variables,
            solutions=solutions,
            solutions_latex=solutions_latex,
            description=f"Solutions for the system of linear equations",
        )

        return result.model_dump_json()

    except Exception as e:
        return handle_error(e, "Linear System Solving")


# Main function
if __name__ == "__main__":
    run_mcp_server(
        "SymPy Server",
        funcs=[
            mcpalgebraic,
            mcpsolve,
            mcpcalculus,
            mcpmatrix,
            mcpsolveode,
            mcpsolvelinear,
        ],
        port=4449,
    )

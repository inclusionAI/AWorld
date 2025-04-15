"""
Math MCP Server

This module provides MCP server functionality for performing various mathematical operations.
It includes tools for basic arithmetic, statistics, geometry, trigonometry, and equation solving.

Key features:
- Perform basic arithmetic operations
- Calculate statistical measures
- Solve geometric problems
- Perform trigonometric calculations
- Solve equations and systems of equations

Main functions:
- mcpbasicmath: Performs basic arithmetic operations
- mcpstatistics: Calculates statistical measures
- mcpgeometry: Solves geometric problems
- mcptrigonometry: Performs trigonometric calculations
- mcpequationsolve: Solves equations and systems of equations
"""

import json
import math
import random
import statistics
import traceback
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from aworld.logs.util import logger
from aworld.mcp_servers.utils import run_mcp_server


# Define model classes for different math operations
class BasicMathResult(BaseModel):
    """Model representing a basic math operation result"""

    operation: str
    inputs: Dict[str, Any]
    result: float
    description: str


class StatisticsResult(BaseModel):
    """Model representing statistics calculation results"""

    operation: str
    data: List[float]
    result: Dict[str, float]
    description: str


class GeometryResult(BaseModel):
    """Model representing geometry calculation results"""

    shape: str
    parameters: Dict[str, float]
    results: Dict[str, float]
    description: str


class TrigonometryResult(BaseModel):
    """Model representing trigonometry calculation results"""

    function: str
    angle: float
    angle_unit: str  # 'degrees' or 'radians'
    result: float
    description: str


class EquationResult(BaseModel):
    """Model representing equation solving results"""

    equation_type: str
    coefficients: List[float]
    solutions: List[float]
    description: str


class MathError(BaseModel):
    """Model representing an error in math processing"""

    error: str
    operation: str


def handle_error(e: Exception, operation_type: str) -> str:
    """Unified error handling and return standard format error message"""
    error_msg = f"{operation_type} error: {str(e)}"
    logger.error(f"{operation_type} operation failed: {str(e)}")
    logger.error(traceback.format_exc())

    error = MathError(error=error_msg, operation=operation_type)

    return error.model_dump_json()


def mcpbasicmath(
    operation: str = Field(
        description="Mathematical operation (add, subtract, multiply, divide, power, sqrt, log, factorial, round, floor, ceil, abs, mod)"
    ),
    a: float = Field(description="First number"),
    b: Optional[float] = Field(
        default=None, description="Second number (optional for some operations)"
    ),
    base: Optional[float] = Field(
        default=None, description="Base for logarithm (default is natural log)"
    ),
    digits: Optional[int] = Field(
        default=None, description="Number of decimal places for rounding"
    ),
) -> str:
    """
    Perform basic mathematical operations.

    Args:
        operation: Type of operation to perform
        a: First number
        b: Second number (optional for some operations)
        base: Base for logarithm (default is natural log)
        digits: Number of decimal places for rounding (optional)

    Returns:
        JSON string containing the result
    """
    logger.info(
        f"Performing basic math operation: {operation} with a={a}, b={b}, base={base}, digits={digits}"
    )
    try:
        result = 0.0
        description = ""

        if operation == "add":
            if b is None:
                raise ValueError("Second number is required for addition")
            result = a + b
            description = f"Sum of {a} and {b}"

        elif operation == "subtract":
            if b is None:
                raise ValueError("Second number is required for subtraction")
            result = a - b
            description = f"Difference of {a} and {b}"

        elif operation == "multiply":
            if b is None:
                raise ValueError("Second number is required for multiplication")
            result = a * b
            description = f"Product of {a} and {b}"

        elif operation == "divide":
            if b is None:
                raise ValueError("Second number is required for division")
            if b == 0:
                raise ValueError("Division by zero is not allowed")
            result = a / b
            description = f"Quotient of {a} divided by {b}"

        elif operation == "power":
            if b is None:
                raise ValueError("Exponent is required for power operation")
            result = math.pow(a, b)
            description = f"{a} raised to the power of {b}"

        elif operation == "sqrt":
            if a < 0:
                raise ValueError("Cannot calculate square root of a negative number")
            result = math.sqrt(a)
            description = f"Square root of {a}"

        elif operation == "log":
            if a <= 0:
                raise ValueError("Logarithm is only defined for positive numbers")
            if base is not None:
                if base <= 0 or base == 1:
                    raise ValueError(
                        "Logarithm base must be positive and not equal to 1"
                    )
                result = math.log(a, base)
                description = f"Logarithm of {a} with base {base}"
            else:
                result = math.log(a)
                description = f"Natural logarithm of {a}"

        elif operation == "factorial":
            if a < 0 or not a.is_integer():
                raise ValueError("Factorial is only defined for non-negative integers")
            result = math.factorial(int(a))
            description = f"Factorial of {int(a)}"

        elif operation == "round":
            if digits is not None:
                result = round(a, digits)
                description = f"Value of {a} rounded to {digits} decimal places"
            else:
                result = round(a)
                description = f"Value of {a} rounded to nearest integer"

        elif operation == "floor":
            result = math.floor(a)
            description = f"Floor value (largest integer not greater than) of {a}"

        elif operation == "ceil":
            result = math.ceil(a)
            description = f"Ceiling value (smallest integer not less than) of {a}"

        elif operation == "abs":
            result = abs(a)
            description = f"Absolute value of {a}"

        elif operation == "mod":
            if b is None:
                raise ValueError("Second number is required for modulo operation")
            if b == 0:
                raise ValueError("Modulo by zero is not allowed")
            result = a % b
            description = f"Remainder of {a} divided by {b}"

        else:
            raise ValueError(f"Unknown operation: {operation}")

        # Create result object
        math_result = BasicMathResult(
            operation=operation,
            inputs={"a": a, "b": b, "base": base, "digits": digits},
            result=result,
            description=description,
        )

        logger.info(
            f"Basic math operation {operation} completed successfully with result: {result}"
        )
        return math_result.model_dump_json()

    except Exception as e:
        return handle_error(e, "Basic Math")


def mcpstatistics(
    operation: str = Field(
        description="Statistical operation (mean, median, mode, stdev, variance, min, max, range, quantile)"
    ),
    data: List[float] = Field(description="List of numbers for statistical analysis"),
    p: Optional[float] = Field(
        default=None, description="Percentile value (0-1) for quantile operation"
    ),
) -> str:
    """
    Perform statistical calculations on a dataset.

    Args:
        operation: Type of statistical operation to perform
        data: List of numbers for analysis
        p: Percentile value (0-1) for quantile operation

    Returns:
        JSON string containing the statistical results
    """
    logger.info(
        f"Performing statistics operation: {operation} on dataset of {len(data)} elements"
    )
    try:
        if not data:
            raise ValueError("Data list cannot be empty")

        results = {}
        description = ""

        if operation == "mean":
            results["mean"] = statistics.mean(data)
            description = "Arithmetic mean (average) of the data"

        elif operation == "median":
            results["median"] = statistics.median(data)
            description = "Median (middle value) of the data"

        elif operation == "mode":
            try:
                results["mode"] = statistics.mode(data)
                description = "Mode (most common value) of the data"
            except statistics.StatisticsError:
                # Handle case where there is no unique mode
                results["mode"] = None
                description = "No unique mode found in the data"

        elif operation == "stdev":
            if len(data) < 2:
                raise ValueError("Standard deviation requires at least two data points")
            results["stdev"] = statistics.stdev(data)
            description = "Standard deviation of the data"

        elif operation == "variance":
            if len(data) < 2:
                raise ValueError("Variance requires at least two data points")
            results["variance"] = statistics.variance(data)
            description = "Variance of the data"

        elif operation == "min":
            results["min"] = min(data)
            description = "Minimum value in the data"

        elif operation == "max":
            results["max"] = max(data)
            description = "Maximum value in the data"

        elif operation == "range":
            results["min"] = min(data)
            results["max"] = max(data)
            results["range"] = max(data) - min(data)
            description = "Range of the data (difference between max and min)"

        elif operation == "quantile":
            if p is None:
                raise ValueError(
                    "Percentile value (p) is required for quantile operation"
                )
            if p < 0 or p > 1:
                raise ValueError("Percentile value must be between 0 and 1")
            results["quantile"] = (
                statistics.quantiles(data, n=100)[int(p * 100) - 1]
                if p > 0
                else min(data)
            )
            description = f"{int(p*100)}th percentile of the data"

        elif operation == "all":
            # Calculate all basic statistics
            results["mean"] = statistics.mean(data)
            results["median"] = statistics.median(data)
            try:
                results["mode"] = statistics.mode(data)
            except statistics.StatisticsError:
                results["mode"] = None
            if len(data) > 1:
                results["stdev"] = statistics.stdev(data)
                results["variance"] = statistics.variance(data)
            results["min"] = min(data)
            results["max"] = max(data)
            results["range"] = max(data) - min(data)
            description = "Complete statistical summary of the data"

        else:
            raise ValueError(f"Unknown operation: {operation}")

        # Create result object
        stats_result = StatisticsResult(
            operation=operation, data=data, result=results, description=description
        )

        logger.info(f"Statistics operation {operation} completed successfully")
        return stats_result.model_dump_json()

    except Exception as e:
        return handle_error(e, "Statistics")


def mcpgeometry(
    shape: str = Field(
        description="Geometric shape (circle, rectangle, triangle, sphere, cylinder, cone)"
    ),
    parameters: Dict[str, float] = Field(
        description="Shape parameters (e.g., radius, width, height)"
    ),
) -> str:
    """
    Calculate geometric properties of various shapes.

    Args:
        shape: Type of geometric shape
        parameters: Dictionary of shape parameters

    Returns:
        JSON string containing geometric calculations
    """
    logger.info(
        f"Performing geometry calculation for shape: {shape} with parameters: {parameters}"
    )
    try:
        results = {}
        description = ""

        if shape == "circle":
            if "radius" not in parameters:
                raise ValueError("Radius is required for circle calculations")

            radius = parameters["radius"]
            if radius <= 0:
                raise ValueError("Radius must be positive")

            results["area"] = math.pi * radius**2
            results["circumference"] = 2 * math.pi * radius
            results["diameter"] = 2 * radius
            description = "Circle calculations based on radius"

        elif shape == "rectangle":
            if "width" not in parameters or "height" not in parameters:
                raise ValueError(
                    "Width and height are required for rectangle calculations"
                )

            width = parameters["width"]
            height = parameters["height"]
            if width <= 0 or height <= 0:
                raise ValueError("Width and height must be positive")

            results["area"] = width * height
            results["perimeter"] = 2 * (width + height)
            results["diagonal"] = math.sqrt(width**2 + height**2)
            description = "Rectangle calculations based on width and height"

        elif shape == "triangle":
            # Check which parameters are provided
            if "base" in parameters and "height" in parameters:
                # Calculate using base and height
                base = parameters["base"]
                height = parameters["height"]
                if base <= 0 or height <= 0:
                    raise ValueError("Base and height must be positive")

                results["area"] = 0.5 * base * height
                description = "Triangle area calculation based on base and height"

            elif all(side in parameters for side in ["a", "b", "c"]):
                # Calculate using three sides (Heron's formula)
                a = parameters["a"]
                b = parameters["b"]
                c = parameters["c"]
                if a <= 0 or b <= 0 or c <= 0:
                    raise ValueError("All sides must be positive")
                if a + b <= c or a + c <= b or b + c <= a:
                    raise ValueError(
                        "Triangle inequality not satisfied: sum of any two sides must exceed the third side"
                    )

                s = (a + b + c) / 2  # Semi-perimeter
                results["area"] = math.sqrt(s * (s - a) * (s - b) * (s - c))
                results["perimeter"] = a + b + c
                description = (
                    "Triangle calculations based on three sides (Heron's formula)"
                )

            else:
                raise ValueError(
                    "Either 'base' and 'height' or all three sides 'a', 'b', 'c' are required for triangle calculations"
                )

        elif shape == "sphere":
            if "radius" not in parameters:
                raise ValueError("Radius is required for sphere calculations")

            radius = parameters["radius"]
            if radius <= 0:
                raise ValueError("Radius must be positive")

            results["volume"] = (4 / 3) * math.pi * radius**3
            results["surface_area"] = 4 * math.pi * radius**2
            description = "Sphere calculations based on radius"

        elif shape == "cylinder":
            if "radius" not in parameters or "height" not in parameters:
                raise ValueError(
                    "Radius and height are required for cylinder calculations"
                )

            radius = parameters["radius"]
            height = parameters["height"]
            if radius <= 0 or height <= 0:
                raise ValueError("Radius and height must be positive")

            results["volume"] = math.pi * radius**2 * height
            results["surface_area"] = 2 * math.pi * radius * (radius + height)
            results["lateral_surface_area"] = 2 * math.pi * radius * height
            description = "Cylinder calculations based on radius and height"

        elif shape == "cone":
            if "radius" not in parameters or "height" not in parameters:
                raise ValueError("Radius and height are required for cone calculations")

            radius = parameters["radius"]
            height = parameters["height"]
            if radius <= 0 or height <= 0:
                raise ValueError("Radius and height must be positive")

            slant_height = math.sqrt(radius**2 + height**2)
            results["volume"] = (1 / 3) * math.pi * radius**2 * height
            results["surface_area"] = math.pi * radius * (radius + slant_height)
            results["slant_height"] = slant_height
            description = "Cone calculations based on radius and height"

        else:
            raise ValueError(f"Unknown shape: {shape}")

        # Create result object
        geometry_result = GeometryResult(
            shape=shape, parameters=parameters, results=results, description=description
        )

        logger.info(f"Geometry calculation for {shape} completed successfully")
        return geometry_result.model_dump_json()

    except Exception as e:
        return handle_error(e, "Geometry")


def mcptrigonometry(
    function: str = Field(
        description="Trigonometric function (sin, cos, tan, asin, acos, atan)"
    ),
    angle: float = Field(description="Angle value"),
    unit: str = Field(default="radians", description="Angle unit (radians or degrees)"),
) -> str:
    """
    Calculate trigonometric functions.

    Args:
        function: Trigonometric function to calculate
        angle: Angle value
        unit: Angle unit (radians or degrees)

    Returns:
        JSON string containing the trigonometric calculation
    """
    logger.info(f"Performing trigonometry calculation: {function} of {angle} {unit}")
    try:
        # Validate unit
        if unit not in ["radians", "degrees"]:
            raise ValueError("Unit must be either 'radians' or 'degrees'")

        # Convert to radians if needed
        angle_rad = angle if unit == "radians" else math.radians(angle)

        result = 0.0
        description = ""

        if function == "sin":
            result = math.sin(angle_rad)
            description = f"Sine of {angle} {unit}"

        elif function == "cos":
            result = math.cos(angle_rad)
            description = f"Cosine of {angle} {unit}"

        elif function == "tan":
            # Check for undefined values (multiples of π/2)
            if unit == "degrees" and angle % 180 == 90:
                raise ValueError("Tangent is undefined at odd multiples of 90 degrees")
            if unit == "radians" and abs(math.cos(angle_rad)) < 1e-10:
                raise ValueError("Tangent is undefined at odd multiples of π/2 radians")

            result = math.tan(angle_rad)
            description = f"Tangent of {angle} {unit}"

        elif function == "asin":
            if angle < -1 or angle > 1:
                raise ValueError("Arcsine input must be between -1 and 1")

            result_rad = math.asin(angle)
            result = result_rad if unit == "radians" else math.degrees(result_rad)
            description = f"Arcsine of {angle}, result in {unit}"

        elif function == "acos":
            if angle < -1 or angle > 1:
                raise ValueError("Arccosine input must be between -1 and 1")

            result_rad = math.acos(angle)
            result = result_rad if unit == "radians" else math.degrees(result_rad)
            description = f"Arccosine of {angle}, result in {unit}"

        elif function == "atan":
            result_rad = math.atan(angle)
            result = result_rad if unit == "radians" else math.degrees(result_rad)
            description = f"Arctangent of {angle}, result in {unit}"

        else:
            raise ValueError(f"Unknown function: {function}")

        # Create result object
        trig_result = TrigonometryResult(
            function=function,
            angle=angle,
            angle_unit=unit,
            result=result,
            description=description,
        )

        logger.info(
            f"Trigonometry calculation {function} completed successfully with result: {result}"
        )
        return trig_result.model_dump_json()

    except Exception as e:
        return handle_error(e, "Trigonometry")


def mcpsolveequation(
    equation_type: str = Field(description="Type of equation (linear, quadratic)"),
    coefficients: List[float] = Field(description="Coefficients of the equation"),
) -> str:
    """
    Solve mathematical equations.

    Args:
        equation_type: Type of equation to solve
        coefficients: Coefficients of the equation

    Returns:
        JSON string containing the solutions
    """
    logger.info(f"Solving {equation_type} equation with coefficients: {coefficients}")
    try:
        solutions = []
        description = ""

        if equation_type == "linear":
            # Linear equation: ax + b = 0
            if len(coefficients) != 2:
                raise ValueError(
                    "Linear equation requires exactly 2 coefficients (a, b)"
                )

            a, b = coefficients
            if a == 0:
                if b == 0:
                    description = "Infinite solutions (identity equation 0 = 0)"
                    solutions = ["All real numbers"]
                else:
                    description = "No solution (contradiction equation)"
                    solutions = []
            else:
                solution = -b / a
                description = f"Linear equation {a}x + {b} = 0"
                solutions = [solution]

        elif equation_type == "quadratic":
            # Quadratic equation: ax^2 + bx + c = 0
            if len(coefficients) != 3:
                raise ValueError(
                    "Quadratic equation requires exactly 3 coefficients (a, b, c)"
                )

            a, b, c = coefficients
            if a == 0:
                # Degenerate case: actually a linear equation
                if b == 0:
                    if c == 0:
                        description = "Infinite solutions (identity equation 0 = 0)"
                        solutions = ["All real numbers"]
                    else:
                        description = "No solution (contradiction equation)"
                        solutions = []
                else:
                    solution = -c / b
                    description = f"Degenerate case: Linear equation {b}x + {c} = 0"
                    solutions = [solution]
            else:
                # Calculate discriminant
                discriminant = b**2 - 4 * a * c

                if discriminant > 0:
                    # Two real solutions
                    x1 = (-b + math.sqrt(discriminant)) / (2 * a)
                    x2 = (-b - math.sqrt(discriminant)) / (2 * a)
                    description = f"Quadratic equation {a}x^2 + {b}x + {c} = 0 with two real solutions"
                    solutions = [x1, x2]
                elif discriminant == 0:
                    # One real solution (double root)
                    x = -b / (2 * a)
                    description = f"Quadratic equation {a}x^2 + {b}x + {c} = 0 with one real solution (double root)"
                    solutions = [x]
                else:
                    # Complex solutions (not supported in this implementation)
                    description = f"Quadratic equation {a}x^2 + {b}x + {c} = 0 with complex solutions"
                    real_part = -b / (2 * a)
                    imag_part = math.sqrt(abs(discriminant)) / (2 * a)
                    solutions = [
                        f"{real_part} + {imag_part}i",
                        f"{real_part} - {imag_part}i",
                    ]

        else:
            raise ValueError(f"Unknown equation type: {equation_type}")

        # Create result object
        equation_result = EquationResult(
            equation_type=equation_type,
            coefficients=coefficients,
            solutions=solutions if isinstance(solutions[0], float) else [],
            description=description,
        )

        logger.info(
            f"Equation solving for {equation_type} completed successfully with {len(solutions)} solution(s)"
        )
        return equation_result.model_dump_json()

    except Exception as e:
        return handle_error(e, "Equation Solving")


def mcprandom(
    operation: str = Field(
        description="Random operation (integer, float, choice, sample)"
    ),
    min_value: Optional[float] = Field(
        default=0, description="Minimum value for random number"
    ),
    max_value: Optional[float] = Field(
        default=1, description="Maximum value for random number"
    ),
    count: Optional[int] = Field(
        default=1, description="Number of random values to generate"
    ),
    choices: Optional[List[Any]] = Field(
        default=None, description="List of items to choose from"
    ),
) -> str:
    """
    Generate random numbers or make random selections.

    Args:
        operation: Type of random operation
        min_value: Minimum value for random number
        max_value: Maximum value for random number
        count: Number of random values to generate
        choices: List of items to choose from

    Returns:
        JSON string containing random results
    """
    logger.info(f"Performing random operation: {operation} with count={count}")
    try:
        result = None
        description = ""

        if operation == "integer":
            if count == 1:
                result = random.randint(int(min_value), int(max_value))
                description = (
                    f"Random integer between {int(min_value)} and {int(max_value)}"
                )
            else:
                result = [
                    random.randint(int(min_value), int(max_value)) for _ in range(count)
                ]
                description = f"{count} random integers between {int(min_value)} and {int(max_value)}"

        elif operation == "float":
            if count == 1:
                result = random.uniform(min_value, max_value)
                description = f"Random float between {min_value} and {max_value}"
            else:
                result = [random.uniform(min_value, max_value) for _ in range(count)]
                description = (
                    f"{count} random floats between {min_value} and {max_value}"
                )

        elif operation == "choice":
            if not choices:
                raise ValueError("Choices list cannot be empty for 'choice' operation")

            if count == 1:
                result = random.choice(choices)
                description = "Random selection from the provided choices"
            else:
                # Allow repeated selections
                result = [random.choice(choices) for _ in range(count)]
                description = f"{count} random selections from the provided choices (with replacement)"

        elif operation == "sample":
            if not choices:
                raise ValueError("Choices list cannot be empty for 'sample' operation")

            if count > len(choices):
                raise ValueError(
                    "Sample count cannot exceed the number of available choices"
                )

            result = random.sample(choices, count)
            description = f"Random sample of {count} items from the provided choices (without replacement)"

        else:
            raise ValueError(f"Unknown operation: {operation}")

        # Create result object
        random_result = {
            "operation": operation,
            "result": result,
            "description": description,
        }

        logger.info(f"Random operation {operation} completed successfully")
        return json.dumps(random_result)

    except Exception as e:
        return handle_error(e, "Random Generation")


def mcpconversion(
    value: float = Field(description="Value to convert"),
    from_unit: str = Field(description="Source unit"),
    to_unit: str = Field(description="Target unit"),
    unit_type: str = Field(
        description="Type of units (length, area, volume, mass, temperature, angle)"
    ),
) -> str:
    """
    Convert between different units of measurement.

    Args:
        value: Value to convert
        from_unit: Source unit
        to_unit: Target unit
        unit_type: Type of units

    Returns:
        JSON string containing conversion result
    """
    logger.info(f"Converting {value} from {from_unit} to {to_unit} ({unit_type})")
    try:
        # Define conversion factors to SI units
        conversion_factors = {
            "length": {
                "m": 1.0,  # meter (SI unit)
                "km": 1000.0,  # kilometer
                "cm": 0.01,  # centimeter
                "mm": 0.001,  # millimeter
                "in": 0.0254,  # inch
                "ft": 0.3048,  # foot
                "yd": 0.9144,  # yard
                "mi": 1609.344,  # mile
            },
            "area": {
                "m2": 1.0,  # square meter (SI unit)
                "cm2": 0.0001,  # square centimeter
                "km2": 1000000.0,  # square kilometer
                "ha": 10000.0,  # hectare
                "in2": 0.00064516,  # square inch
                "ft2": 0.09290304,  # square foot
                "yd2": 0.83612736,  # square yard
                "ac": 4046.8564224,  # acre
                "mi2": 2589988.110336,  # square mile
            },
            "volume": {
                "m3": 1.0,  # cubic meter (SI unit)
                "cm3": 0.000001,  # cubic centimeter
                "mm3": 1e-9,  # cubic millimeter
                "l": 0.001,  # liter
                "ml": 0.000001,  # milliliter
                "gal": 0.00378541,  # US gallon
                "qt": 0.000946353,  # US quart
                "pt": 0.000473176,  # US pint
                "fl_oz": 2.95735e-5,  # US fluid ounce
                "in3": 1.6387064e-5,  # cubic inch
                "ft3": 0.028316846592,  # cubic foot
            },
            "mass": {
                "kg": 1.0,  # kilogram (SI unit)
                "g": 0.001,  # gram
                "mg": 0.000001,  # milligram
                "lb": 0.45359237,  # pound
                "oz": 0.028349523125,  # ounce
                "ton": 907.18474,  # US ton
                "tonne": 1000.0,  # metric ton
            },
            "angle": {
                "rad": 1.0,  # radian (SI unit)
                "deg": math.pi / 180.0,  # degree
                "grad": math.pi / 200.0,  # gradian
                "turn": 2 * math.pi,  # turn
            },
        }

        # Special case for temperature (requires offset)
        temperature_conversions = {
            "c_to_f": lambda c: c * 9 / 5 + 32,
            "f_to_c": lambda f: (f - 32) * 5 / 9,
            "c_to_k": lambda c: c + 273.15,
            "k_to_c": lambda k: k - 273.15,
            "f_to_k": lambda f: (f - 32) * 5 / 9 + 273.15,
            "k_to_f": lambda k: (k - 273.15) * 9 / 5 + 32,
        }

        # Check if unit type is valid
        if unit_type not in conversion_factors and unit_type != "temperature":
            raise ValueError(f"Unknown unit type: {unit_type}")

        # Handle temperature conversions separately
        if unit_type == "temperature":
            from_unit = from_unit.lower()
            to_unit = to_unit.lower()

            conversion_key = f"{from_unit}_to_{to_unit}"
            if conversion_key in temperature_conversions:
                result = temperature_conversions[conversion_key](value)
                description = f"Converted {value} {from_unit.upper()} to {result} {to_unit.upper()}"
            else:
                raise ValueError(
                    f"Unsupported temperature conversion: {from_unit} to {to_unit}"
                )
        else:
            # Check if units are valid
            if from_unit not in conversion_factors[unit_type]:
                raise ValueError(f"Unknown source unit: {from_unit}")
            if to_unit not in conversion_factors[unit_type]:
                raise ValueError(f"Unknown target unit: {to_unit}")

            # Convert to SI unit, then to target unit
            si_value = value * conversion_factors[unit_type][from_unit]
            result = si_value / conversion_factors[unit_type][to_unit]
            description = f"Converted {value} {from_unit} to {result} {to_unit}"

        # Create result object
        conversion_result = {
            "value": value,
            "from_unit": from_unit,
            "to_unit": to_unit,
            "result": result,
            "unit_type": unit_type,
            "description": description,
        }

        return json.dumps(conversion_result)

    except Exception as e:
        return handle_error(e, "Unit Conversion")


# Main function
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Launch MCP servers with random port allocation"
    )
    parser.add_argument(
        "--port",
        type=int,
        help=f"Listening to port. Must be specified.",
    )
    args = parser.parse_args()

    logger.info("Starting Math Server")
    run_mcp_server(
        "Math Server",
        funcs=[
            mcpbasicmath,
            mcpstatistics,
            mcpgeometry,
            mcptrigonometry,
            mcpsolveequation,
            mcprandom,
            mcpconversion,
        ],
        port=args.port,
    )

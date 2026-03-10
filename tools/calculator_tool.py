"""
Calculator Tool
Performs accurate numeric calculations for factual verification.
"""

import math
import operator

# Allowed safe operators
SAFE_OPERATORS = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "**": operator.pow,
    "%": operator.mod
}
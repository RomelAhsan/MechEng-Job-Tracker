"""Signal word/phrase lists for the mechanical-relevance classifier.

Lifted verbatim from the SPEC "MECHANICAL ENGINEERING CLASSIFIER" section:
strong-title signals, skill signals, adjacent roles (never auto-rejected), and
negative fields (only ``not_relevant`` when title AND description are clearly
unrelated).
"""

from __future__ import annotations

# SPEC "Strong match signals" (title or description).
STRONG_TITLE_SIGNALS: tuple[str, ...] = (
    "Mechanical Engineering Intern",
    "Mechanical Engineering Co-op",
    "Mechanical Design Intern",
    "Mechanical Design Co-op",
    "Mechanical Engineer Intern",
    "Hardware Engineering Intern",
    "Hardware Co-op",
    "Product Design Intern",
    "Product Development Intern",
    "Manufacturing Engineering Intern",
    "Manufacturing Co-op",
    "Mechatronics Intern",
    "Robotics Intern",
    "Automotive Engineering Intern",
    "Aerospace Engineering Intern",
    "Thermal Engineering Intern",
    "HVAC Engineering Intern",
    "Test Engineering Intern",
    "R&D Engineering Intern",
)

# SPEC "Skill signals".
SKILL_SIGNALS: tuple[str, ...] = (
    "CAD",
    "SolidWorks",
    "CATIA",
    "NX",
    "Creo",
    "Fusion 360",
    "GD&T",
    "FEA",
    "CFD",
    "ANSYS",
    "Abaqus",
    "MATLAB",
    "Simulink",
    "DFM",
    "DFA",
    "tolerance analysis",
    "prototyping",
    "3D printing",
    "CNC",
    "root cause analysis",
    "test fixtures",
    "BOM",
    "PLM",
    "thermal analysis",
    "fluid mechanics",
    "heat transfer",
    "kinematics",
    "dynamics",
)

# SPEC "Adjacent roles" (classify as possible_match, never auto-reject).
ADJACENT_ROLE_SIGNALS: tuple[str, ...] = (
    "Engineering Intern",
    "Engineering Co-op",
    "Design Intern",
    "Product Intern",
    "R&D Intern",
    "Systems Engineering Intern",
    "Electrical Hardware Intern",
    "Process Engineering Intern",
    "Quality Engineering Intern",
    "Continuous Improvement Intern",
)

# SPEC "Negative signals" (unrelated fields).
NEGATIVE_FIELD_SIGNALS: tuple[str, ...] = (
    "Sales",
    "Marketing",
    "Finance",
    "Accounting",
    "Human Resources",
    "Legal",
    "Customer Support",
    "Recruiting",
)

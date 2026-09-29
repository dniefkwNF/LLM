"""``python -m pet_hospital_mcp`` entry point.

Equivalent to the ``pet-hospital-mcp`` console script; both call
:func:`pet_hospital_mcp.server.run`.
"""

from pet_hospital_mcp.server import run

if __name__ == "__main__":
    run()

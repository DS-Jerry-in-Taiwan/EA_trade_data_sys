"""Demo-only MT5 execution API.

Keep the Flask application import lazy so storage, normalization, and static
contract checks can run without pulling the web runtime into every module.
"""

__all__ = ["create_execution_app"]


def __getattr__(name):
    if name == "create_execution_app":
        from .app import create_execution_app

        return create_execution_app
    raise AttributeError(name)

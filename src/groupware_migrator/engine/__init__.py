"""Core migration engine components.

Keep this package initializer free of eager submodule imports to avoid circular
import chains when consumers import specific modules such as
``groupware_migrator.engine.idempotency``.
"""

__all__ = []

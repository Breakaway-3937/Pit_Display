"""
Shared lazy-proxy used by all module-level singletons (config, rotation,
judges_slides, cad_assets, db).

Each singleton module exposes a proxy that is safe to import at module level
anywhere, but raises clearly if used before its init_*() function is called
in main(). This module owns the forwarding logic in one place.

Usage in a singleton module:

    thing: _Thing = LazyProxy("thing", "init_thing")  # type: ignore[assignment]

    def init_thing() -> _Thing:
        real = _Thing()
        thing._install(real)
        return real
"""


class LazyProxy:
    """Forwards all attribute access to a real object once _install() is called."""

    def __init__(self, label: str, init_name: str):
        object.__setattr__(self, "_real", None)
        object.__setattr__(self, "_label", label)
        object.__setattr__(self, "_init_name", init_name)

    def _install(self, real) -> None:
        object.__setattr__(self, "_real", real)

    def _require(self, name: str):
        real = object.__getattribute__(self, "_real")
        if real is None:
            label = object.__getattribute__(self, "_label")
            init_name = object.__getattribute__(self, "_init_name")
            raise RuntimeError(
                f"{label}.{name} accessed before {init_name}() was called. "
                f"Call {init_name}() in main() after QApplication is created."
            )
        return real

    def __getattr__(self, name: str):
        return getattr(self._require(name), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(self._require(name), name, value)

def register_all_handlers() -> None:
    """Import every module that registers job handlers (kept lazy to avoid import cycles)."""
    from app.jobs import handlers  # noqa: F401

    for mod in (
        "app.services.research.jobs",
        "app.services.drafting.jobs",
        "app.services.replies.jobs",
    ):
        try:
            __import__(mod)
        except ModuleNotFoundError as e:  # modules are added stage by stage
            if e.name is None or not (mod == e.name or mod.startswith(e.name + ".")):
                raise

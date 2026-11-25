"""Python ports of WPILib Commands v3.

Pick exactly one variant for a robot program:

    from commands3.custom.asyncawait import Scheduler, Command, Mechanism, Coroutine, ...

Styles:   asyncawait | generators | asyncgen | greenlets
Runtimes: custom (commands3.custom.*) | asyncio (commands3.aio.*)
"""

VARIANTS = [
    f"commands3.{rt}.{style}"
    for rt in ("custom", "aio")
    for style in (
        "asyncawait", "generators", "asyncgen", "greenlets",
        "asyncawait_noco", "generators_noco", "asyncgen_noco", "greenlets_noco",
    )
]

"""Which tool set the engine runs. PACK unset: customer service, the exact objects from app.tools (unchanged path).
PACK=<name>: a config-only pack from packs/<name>/, validated at import; an invalid pack stops the server starting."""
from app import config
from app.tools import TOOL_MODELS, TOOL_SPECS, check_facts, execute

RUNTIME = None
if config.PACK:
    from app.packs.runtime import Runtime
    from app.packs.spec import load

    RUNTIME = Runtime(load(config.PACK, config.ROOT))
    TOOL_MODELS, TOOL_SPECS = RUNTIME.models, RUNTIME.specs  # type: ignore[assignment]
    execute, check_facts = RUNTIME.execute, RUNTIME.check_facts  # type: ignore[assignment]

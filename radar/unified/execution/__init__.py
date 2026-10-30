"""Yürütme adaptörleri. Bu sürümde yalnız PaperExecutionAdapter çalışır; OKXDemoAdapter yalnız sözleşmedir (stub) ve
çağrılırsa ExecutionModeError verir. Gerçek (canlı) emir adaptörü YOKTUR."""
from .base import ExecutionAdapter, Quote
from .okx_demo import OKXDemoAdapter
from .paper import PaperExecutionAdapter

__all__ = ["ExecutionAdapter", "Quote", "PaperExecutionAdapter", "OKXDemoAdapter", "make_adapter"]


def make_adapter(cfg, root=None) -> ExecutionAdapter:
    """Config'e göre adaptör. Paper dışı istek flags.execution_mode tarafından zaten paper'a düşürülür."""
    from ..flags import execution_mode
    mode, warning = execution_mode(cfg)
    if warning:
        print("[birleşik yürütme]", warning)
    return PaperExecutionAdapter(cfg, root=root)

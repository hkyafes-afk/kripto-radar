"""OKX demo adaptörü — YALNIZ SÖZLEŞME (stub).

Bu sürümde borsaya hiçbir bağlantı kurulmaz, anahtar okunmaz, emir gönderilmez. Her çağrı ExecutionModeError verir.
İleride kullanıcı OKX demo anahtarını GitHub Secrets'a koyup açıkça onaylarsa yalnız bu dosya doldurulur; strateji,
karar ve risk kodu değişmez. Canlı (gerçek para) adaptörü bu çalışmanın kapsamı dışındadır.
"""
from __future__ import annotations

from ..flags import ExecutionModeError
from ..models import ExecutionMode
from .base import ExecutionAdapter

_MSG = "OKX demo yürütmesi bu sürümde kapalı (yalnız paper). Gerçek ya da demo borsa emri gönderilmez."


class OKXDemoAdapter(ExecutionAdapter):
    mode = ExecutionMode.OKX_DEMO

    def __init__(self, *_, **__):
        raise ExecutionModeError(_MSG)

    def submit(self, *_, **__):  # pragma: no cover - kurucu zaten hata verir
        raise ExecutionModeError(_MSG)

    def cancel(self, *_, **__):  # pragma: no cover
        raise ExecutionModeError(_MSG)

    def amend_stop(self, *_, **__):  # pragma: no cover
        raise ExecutionModeError(_MSG)

    def on_quote(self, *_, **__):  # pragma: no cover
        raise ExecutionModeError(_MSG)

    def status(self, *_, **__):  # pragma: no cover
        raise ExecutionModeError(_MSG)

    def reconcile(self, *_, **__):  # pragma: no cover
        raise ExecutionModeError(_MSG)

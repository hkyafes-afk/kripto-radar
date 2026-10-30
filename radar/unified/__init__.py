"""Birleşik Baş Trader (geliştirme aşamasında).

Amaç: birbirini kopyalayan isimli ajanlar yerine tek merkezden karar veren yapı. Bu paket legacy sistemi (radar/team.py,
desk.py, ledger.py, engine.py) DEĞİŞTİRMEZ; `config.yaml → birlesik.karar_modu: legacy` iken canlı akışa hiç bağlanmaz.

Modüller (PR 1):
  models    ortak veri modelleri (MarketContext, Opportunity, CostEstimate, DecisionResult, RiskDecision, yürütme, ölçüm)
  coverage  faktör kapsamı (eksik / bayat / geçersiz zorunlu veri → paper risk yok, SHADOW)
  costs     CostEngine (ücret, spread, kayma, stop kayması, funding, kısmi dolum, belirsizlik tamponu → REJECT/SHADOW/PASS)
  flags     özellik bayrakları; yürütme modu bu sürümde yalnız PAPER

Sonraki adımlar ve bağımlılıklar: docs/birlesik/PLAN.md. Yarıda kalırsa: docs/birlesik/DEVAM.md.
"""

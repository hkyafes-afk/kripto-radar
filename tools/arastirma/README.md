# Tek ajan araştırması (30 Ekim 2026)

Sonuçlar ve yorum: `data/TEK_AJAN_ARASTIRMA.md`. Veri: `binance-veri` dalı (günlük mum + funding, batanlar dahil).

Tekrar üretmek (repo kökünde; `bv` = binance-veri dalının çalışma kopyası):

    git fetch --depth 1 origin binance-veri && git worktree add bv FETCH_HEAD
    python tools/arastirma/panel.py bv /tmp/panel.pkl          # geniş tablolar, zombi satırlar ve kripto olmayanlar ayıklanır
    python tools/arastirma/features.py /tmp/panel.pkl /tmp/feats.pkl
    python tools/arastirma/ic_train.py tools/arastirma /tmp/feats.pkl      # yalnız eğitim: etkenler
    python tools/arastirma/combo_train.py tools/arastirma /tmp/feats.pkl   # yalnız eğitim: rejim × trend, bileşik puan
    python tools/arastirma/sim.py tools/arastirma /tmp/panel.pkl /tmp/feats.pkl /tmp/eq.pkl [--test] [--stres]
    python tools/arastirma/team_test.py . bv /tmp/team.pkl     # mevcut ekip, aynı veri ve pencereler

Eğitim 2020-10-28 → 2025-01-06, saklı test 2025-01-07 → 2026-10-27. Tasarım kararları yalnız eğitimle alındı;
saklı test bir kez açıldı (`--test`).

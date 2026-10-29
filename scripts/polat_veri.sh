#!/usr/bin/env bash
# Polat'ın durumu ve günlüğü ayrı bir dalda (polat-veri) tutulur; ana koddan ve Kripto Radar'ın dallarından bağımsız.
#   bash scripts/polat_veri.sh pull          → polat_veri/ klasörüne indir (dal HİÇ yoksa boş başla)
#   bash scripts/polat_veri.sh push "mesaj"  → polat_veri/ içeriğini dala gönder (elle kullanım; Polat kendisi
#                                              nöbet kurallarıyla gönderir: polat/lease.py)
# Dal varken indirilemezse BOŞ BAŞLANMAZ: boş durumla açılan bir Polat gerçek defteri ezebilirdi. İş hata verir,
# sonraki bekçi yeniden dener.
set -euo pipefail
DIR="${POLAT_VERI_DIR:-polat_veri}"
BR="polat-veri"
REPO_URL="${POLAT_LEASE_REMOTE:-https://x-access-token:${GITHUB_TOKEN:-}@github.com/${GITHUB_REPOSITORY:-}.git}"

case "${1:-}" in
  pull)
    for i in 1 2 3; do
      rm -rf "$DIR"
      if git clone -q --depth 1 --branch "$BR" "$REPO_URL" "$DIR" 2>/dev/null; then
        git -C "$DIR" config gc.auto 0          # dakikada bir commit: arka plan gc kilitleri nabzı geciktirmesin
        echo "polat-veri indirildi."
        exit 0
      fi
      set +e
      git ls-remote --exit-code --heads "$REPO_URL" "$BR" >/dev/null 2>&1
      rc=$?
      set -e
      if [ "$rc" = "2" ]; then
        mkdir -p "$DIR" && cd "$DIR" && git init -q && git checkout -q -b "$BR" && git remote add origin "$REPO_URL"
        git config gc.auto 0
        echo "polat-veri dalı yok: boş başlıyor."
        exit 0
      fi
      echo "polat-veri indirilemedi (deneme $i/3)."
      sleep $((i * 5))
    done
    echo "::error::polat-veri dalı var ama indirilemedi; boş durumla başlamak yerine duruyorum."
    exit 1
    ;;
  push)
    cd "$DIR"
    git config user.name "polat"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    git add -A
    if git diff --cached --quiet; then echo "değişiklik yok"; exit 0; fi
    git commit -qm "🎯 polat: ${2:-kayıt}"
    for i in 1 2 3; do
      if git push -q origin "HEAD:$BR"; then echo "gönderildi"; exit 0; fi
      git pull -q --rebase origin "$BR" || git rebase --abort 2>/dev/null || true
      sleep $((i * 3))
    done
    echo "::warning::polat-veri gönderilemedi"
    ;;
  *) echo "kullanım: $0 pull|push [mesaj]"; exit 2 ;;
esac

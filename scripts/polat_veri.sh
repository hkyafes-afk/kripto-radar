#!/usr/bin/env bash
# Polat'ın durumu ve günlüğü ayrı bir dalda (polat-veri) tutulur; ana koddan ve Kripto Radar'ın dallarından bağımsız.
#   bash scripts/polat_veri.sh pull          → polat_veri/ klasörüne indir (dal yoksa boş başla)
#   bash scripts/polat_veri.sh push "mesaj"  → polat_veri/ içeriğini dala gönder
set -euo pipefail
DIR="${POLAT_VERI_DIR:-polat_veri}"
BR="polat-veri"
REPO_URL="https://x-access-token:${GITHUB_TOKEN:-}@github.com/${GITHUB_REPOSITORY:-}.git"

case "${1:-}" in
  pull)
    rm -rf "$DIR"
    if git clone -q --depth 1 --branch "$BR" "$REPO_URL" "$DIR" 2>/dev/null; then
      echo "polat-veri indirildi."
    else
      mkdir -p "$DIR" && cd "$DIR" && git init -q && git checkout -q -b "$BR" && git remote add origin "$REPO_URL"
      echo "polat-veri dalı yok: boş başlıyor."
    fi
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
      git pull -q --rebase origin "$BR" || true
      sleep $((i * 3))
    done
    echo "::warning::polat-veri gönderilemedi"
    ;;
  *) echo "kullanım: $0 pull|push [mesaj]"; exit 2 ;;
esac

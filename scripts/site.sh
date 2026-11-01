#!/usr/bin/env bash
# Dashboard + durum dosyaları gh-pages dalında tutulur.
# Her yayında dal tek commit'e sıfırlanır (force push) → repo büyümez.
#   bash scripts/site.sh pull         # gh-pages içeriğini ./site'a indir
#   bash scripts/site.sh push "mesaj" # ./site'ı gh-pages olarak yayınla
set -euo pipefail
SITE=site
cmd=${1:-pull}

case "$cmd" in
  pull)
    mkdir -p "$SITE"
    if git fetch -q --depth=1 origin gh-pages 2>/dev/null; then
      git archive FETCH_HEAD | tar -x -C "$SITE"
      echo "gh-pages alındı: $(ls -A "$SITE" | tr '\n' ' ')"
    else
      echo "gh-pages dalı yok (ilk çalıştırma) — sıfırdan başlanıyor."
    fi
    ;;
  push)
    msg=${2:-güncelleme}
    # Keep the dedicated UnifiedTrader dashboard in every Pages publication.
    # This is a static template copy; runtime data remains sourced from site/birlesik/*.json.
    if [ -f "radar/templates/birlesik.html" ]; then
      cp "radar/templates/birlesik.html" "$SITE/birlesik.html"
    fi
    if [ ! -f "$SITE/index.html" ] && [ ! -f "$SITE/state.json" ]; then
      echo "site içeriği yok, yayın atlandı."
      exit 0
    fi
    cd "$SITE"
    rm -rf .git
    git init -q -b gh-pages
    git config user.name "kripto-radar"
    git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
    touch .nojekyll
    git add -A
    git commit -qm "$msg $(date -u +%Y-%m-%dT%H:%MZ)"
    url=${PUSH_URL:-"https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"}
    git push -qf "$url" gh-pages
    echo "✓ gh-pages yayınlandı"
    ;;
  *)
    echo "kullanım: $0 pull|push [mesaj]"; exit 2 ;;
esac

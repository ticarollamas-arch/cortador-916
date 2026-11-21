#!/bin/sh
# Build da Vercel: copia a interface estática para ./public
# Funciona mesmo se o "Root Directory" do projeto estiver numa subpasta.
set -e
echo "pasta do build: $(pwd)"
D=""
for d in . .. ../..; do
  if [ -f "$d/index.html" ]; then D="$d"; break; fi
done
if [ -z "$D" ]; then
  echo "index.html nao encontrado"; ls -la; exit 1
fi
echo "pagina em: $D"
rm -rf public
mkdir -p public
cp "$D/index.html" "$D/favicon.svg" "$D/mp4fix.js" public/
echo "ok: public/ pronto"

#!/usr/bin/env bash
# Instalacion y verificacion del bot. Correr desde la raiz del repo:
#   bash scripts/setup.sh
set -euo pipefail

cd "$(dirname "$0")/.."
echo "=== Directorio: $(pwd) ==="

# --- Python ---
PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
        V=$("$c" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
        MAJOR=${V%%.*}; MINOR=${V##*.}
        if [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 11 ]; then PY="$c"; break; fi
    fi
done

if [ -z "$PY" ]; then
    echo "ERROR: hace falta Python 3.11 o superior."
    echo "  macOS:   brew install python@3.12"
    echo "  Ubuntu:  sudo apt install python3.12 python3.12-venv"
    echo "  Windows: https://www.python.org/downloads/ (marca 'Add to PATH')"
    exit 1
fi
echo "Python: $($PY --version)"

# --- Entorno virtual ---
if [ ! -d .venv ]; then
    echo "Creando entorno virtual..."
    "$PY" -m venv .venv
fi

if [ -f .venv/bin/activate ]; then
    VENV_PY=".venv/bin/python"          # macOS / Linux
else
    VENV_PY=".venv/Scripts/python.exe"  # Windows (Git Bash)
fi

echo "Instalando dependencias..."
"$VENV_PY" -m pip install -q --upgrade pip
"$VENV_PY" -m pip install -q -e ".[dev]"

# --- Verificacion ---
echo
echo "=== Tests ==="
"$VENV_PY" -m pytest -q

echo
echo "=== Demo (sin red, sin gastar cuota de API) ==="
"$VENV_PY" -m betbot.cli demo --no-store

# --- Configuracion ---
echo
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Creado .env — te falta poner tu ODDS_API_KEY dentro."
else
    echo ".env ya existe, no se toca."
fi

cat <<'NEXT'

=== LISTO ===

A partir de ahora, en esta carpeta, usa el python del entorno virtual:

  macOS/Linux:  .venv/bin/python -m betbot.cli doctor
  Windows:      .venv\Scripts\python -m betbot.cli doctor

Siguiente paso: consigue una key en https://the-odds-api.com
y ponla en el archivo .env (linea ODDS_API_KEY=).

Luego sigue RUNBOOK.md desde el paso 2.
NEXT

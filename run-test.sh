#!/usr/bin/env bash
#
# Movienotes deneme oturumu — tek komut.
#
#   ./run-test.sh                 # http://127.0.0.1:8765 ve Chrome açılır
#   ./run-test.sh --port 9000
#   ./run-test.sh --keep-cache    # taramaları çalıştırmalar arasında koru
#
# Giriş ekranı yalnızca bir Letterboxd kullanıcı adı sorar; parola yoktur.
# Yazdığın profil gerçekten taranır ve onboarding baştan oynar. Hiçbir şey
# kaydedilmez: Ctrl-C ile kapattığında geçici veri klasörü de silinir.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PORT=8765
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="$2"; ARGS+=("--port" "$2"); shift 2 ;;
    --port=*) PORT="${1#*=}"; ARGS+=("--port" "$PORT"); shift ;;
    -h|--help) sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

# Yorumlayıcıyı adına göre değil, uygulamayı gerçekten çalıştırabilmesine göre
# seç. macOS'ta `python3` çoğu zaman Xcode'un kendi 3.9'u oluyor: var, çalışıyor,
# ama bağımlılıkların hiçbiri onda kurulu değil — ve ortaya çıkan hata mesajı
# ("No module named 'pydantic_settings'") scripti bozuk gösteriyor.
PYTHON_OVERRIDE="${PYTHON:-}"
PYTHON=""
for candidate in "$PYTHON_OVERRIDE" .venv/bin/python venv/bin/python python python3 python3.12; do
  [[ -z "$candidate" ]] && continue
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c "import fastapi, uvicorn, pydantic_settings" >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
if [[ -z "$PYTHON" ]]; then
  echo "Bağımlılıkları kurulu bir Python bulunamadı." >&2
  echo "  pip install -r backend/requirements.txt" >&2
  echo "  (ya da PYTHON=/yol/python ./run-test.sh)" >&2
  exit 1
fi

URL="http://127.0.0.1:${PORT}"

open_browser() {
  # Sunucu yanıt verene kadar bekle. Sabit bir `sleep` yerine sağlık ucunu
  # yoklamak, ilk taramanın yavaş olduğu makinede "siteye ulaşılamıyor"
  # ekranıyla karşılaşmayı engelliyor.
  for _ in $(seq 1 60); do
    if curl -sf -o /dev/null --max-time 1 "${URL}/api/health"; then
      if [[ "$(uname)" == "Darwin" ]] && [[ -d "/Applications/Google Chrome.app" ]]; then
        open -a "Google Chrome" "$URL"
      elif command -v google-chrome >/dev/null 2>&1; then
        google-chrome "$URL" >/dev/null 2>&1 &
      elif [[ "$(uname)" == "Darwin" ]]; then
        open "$URL"
      else
        xdg-open "$URL" >/dev/null 2>&1 &
      fi
      return 0
    fi
    sleep 0.5
  done
  echo "  Sunucu açılmadı; $URL adresini elle açabilirsin." >&2
}

open_browser &
WAITER=$!
# Sunucu erken kapanırsa bekleyen işi de bırakma.
trap 'kill "$WAITER" 2>/dev/null || true' EXIT

# Ön planda ve `exec` olmadan çalışıyor: Ctrl-C ikisine birden gidiyor, python
# geçici klasörü kendi `finally` bloğunda siliyor ve kabuk onu bekliyor.
set +e
"$PYTHON" -m scripts.sandbox --no-browser "${ARGS[@]}"
STATUS=$?
# 130 = Ctrl-C. Kapatmanın olağan yolu; hata gibi raporlama.
[[ $STATUS -eq 130 ]] && STATUS=0
exit $STATUS

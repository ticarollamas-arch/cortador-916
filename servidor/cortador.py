#!/usr/bin/env python3
"""
Cortador — corta vídeos do YouTube e grava legendas com IA, tudo LOCAL.

Nada de API de terceiros: o vídeo é baixado direto (yt-dlp), a transcrição
roda na sua máquina (faster-whisper) e o corte/legenda é feito pelo ffmpeg.

Uso rápido:
  python cortador.py "https://youtube.com/watch?v=XXXX" --auto 3
  python cortador.py "https://youtube.com/watch?v=XXXX" --inicio 1:20 --fim 2:05
  python cortador.py video.mp4 --auto 5 --formato original
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- utilidades

def log(msg):
    print(f"[cortador] {msg}", flush=True)


def tempo_para_seg(t):
    """'1:20' -> 80.0 | '01:02:03' -> 3723.0 | '95' -> 95.0"""
    partes = [float(p) for p in str(t).split(":")]
    seg = 0.0
    for p in partes:
        seg = seg * 60 + p
    return seg


def seg_para_ass(s):
    s = max(0.0, s)
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h}:{m:02d}:{sec:05.2f}"


def nome_seguro(txt, limite=40):
    txt = re.sub(r"[^\w\s-]", "", txt, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_]+", "-", txt)[:limite] or "corte"


def checar_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        sys.exit("ffmpeg não encontrado. Instale: sudo apt install ffmpeg")


# ---------------------------------------------------------------- 1. baixar

def baixar(url, pasta, progresso=None):
    import yt_dlp

    def gancho(d):
        if progresso and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                progresso(d.get("downloaded_bytes", 0) / total)

    opcoes = {
        "progress_hooks": [gancho],
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(pasta / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }
    log("baixando vídeo...")
    with yt_dlp.YoutubeDL(opcoes) as ydl:
        info = ydl.extract_info(url, download=True)
    arquivo = pasta / f"{info['id']}.mp4"
    if not arquivo.exists():  # caso o container final seja outro
        arquivo = next(pasta.glob(f"{info['id']}.*"))
    return arquivo, info.get("title", info["id"])


# ---------------------------------------------------------------- 2. transcrever

def transcrever(video, modelo, idioma, inicio=None, fim=None, cache=None, progresso=None):
    """Retorna lista de palavras: [{'w': str, 's': float, 'e': float}]
    Tempos sempre absolutos (em relação ao vídeo inteiro)."""
    if cache and cache.exists():
        log("usando transcrição em cache")
        return json.loads(cache.read_text(encoding="utf-8"))

    from faster_whisper import WhisperModel

    # extrai só o áudio (e só o trecho, se for corte manual) -> bem mais rápido
    audio = video.with_suffix(".wav")
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if inicio is not None:
        cmd += ["-ss", str(inicio)]
    cmd += ["-i", str(video)]
    if fim is not None and inicio is not None:
        cmd += ["-t", str(fim - inicio)]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", str(audio)]
    subprocess.run(cmd, check=True)

    log(f"transcrevendo localmente (modelo '{modelo}')... pode demorar")
    model = WhisperModel(modelo, device="auto", compute_type="int8")
    segs, info = model.transcribe(
        str(audio),
        language=idioma,
        word_timestamps=True,
        vad_filter=True,
    )
    desloc = inicio or 0.0
    palavras = []
    for seg in segs:
        for w in seg.words or []:
            texto = w.word.strip()
            if texto:
                palavras.append({"w": texto, "s": w.start + desloc, "e": w.end + desloc})
        print(f"\r[cortador]   {seg.end + desloc:7.1f}s transcritos", end="", flush=True)
        if progresso and getattr(info, "duration", 0):
            progresso(min(seg.end / info.duration, 1.0))
    print()
    audio.unlink(missing_ok=True)

    if cache:
        cache.write_text(json.dumps(palavras, ensure_ascii=False), encoding="utf-8")
    return palavras


# ---------------------------------------------------------------- 3. escolher os melhores trechos

GANCHOS = {
    # palavras que costumam marcar momentos fortes (ajuste para o seu nicho)
    "segredo", "ninguém", "nunca", "sempre", "verdade", "dinheiro", "erro",
    "cuidado", "olha", "incrível", "absurdo", "pior", "melhor", "primeiro",
    "importante", "problema", "simples", "rápido", "grátis", "mentira",
    "descobri", "aprendi", "mudou", "por que", "como", "você", "atenção",
}


def energia_por_segundo(video):
    """Volume (RMS) de cada segundo do áudio, normalizado 0..1. Usa só ffmpeg+numpy."""
    import numpy as np
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(video), "-vn",
         "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
        capture_output=True, check=True,
    )
    amostras = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32)
    n = len(amostras) // 8000
    if n == 0:
        return np.zeros(1)
    rms = np.sqrt((amostras[: n * 8000].reshape(n, 8000) ** 2).mean(axis=1))
    return rms / (rms.max() or 1)


def frases(palavras, pausa=0.7):
    """Agrupa palavras em frases usando pontuação e pausas. Servem de 'pontos de corte'."""
    grupos, atual = [], []
    for i, p in enumerate(palavras):
        atual.append(p)
        prox = palavras[i + 1] if i + 1 < len(palavras) else None
        fim_frase = p["w"][-1:] in ".!?" or (prox and prox["s"] - p["e"] > pausa)
        if fim_frase or prox is None:
            grupos.append(atual)
            atual = []
    return grupos


def pontuar(trecho, energia):
    """Nota de um trecho candidato (explicável, sem caixa-preta)."""
    import numpy as np
    ini, fim = trecho[0]["s"], trecho[-1]["e"]
    dur = max(fim - ini, 1)
    texto = " ".join(p["w"] for p in trecho).lower()

    ritmo = min(len(trecho) / dur / 3.0, 1.0)              # fala densa (~3 palavras/s = 1.0)
    vol = float(np.mean(energia[int(ini): int(fim) + 1])) if len(energia) > int(ini) else 0
    ganchos = min(sum(texto.count(g) for g in GANCHOS) / 4, 1.0)
    emocao = min((texto.count("!") + texto.count("?")) / 3, 1.0)
    # bônus se o trecho começa com uma frase de impacto (pergunta/gancho nas 8 primeiras palavras)
    abertura = " ".join(p["w"] for p in trecho[:8]).lower()
    inicio_forte = 1.0 if ("?" in abertura or any(g in abertura for g in GANCHOS)) else 0.0

    nota = 0.30 * ritmo + 0.25 * vol + 0.25 * ganchos + 0.10 * emocao + 0.10 * inicio_forte
    motivo = (f"ritmo={ritmo:.2f} volume={vol:.2f} ganchos={ganchos:.2f} "
              f"emoção={emocao:.2f} abertura={inicio_forte:.0f}")
    return nota, motivo


def escolher_cortes(palavras, energia, qtd, dur_min, dur_max):
    blocos = frases(palavras)
    candidatos = []
    for i in range(len(blocos)):
        trecho = []
        for j in range(i, len(blocos)):
            trecho = trecho + blocos[j]
            dur = trecho[-1]["e"] - trecho[0]["s"]
            if dur > dur_max:
                break
            if dur >= dur_min:
                nota, motivo = pontuar(trecho, energia)
                candidatos.append((nota, trecho[0]["s"], trecho[-1]["e"], motivo, trecho))

    candidatos.sort(key=lambda c: c[0], reverse=True)
    escolhidos = []
    for c in candidatos:  # pega os melhores sem sobreposição
        if all(c[2] <= e[1] or c[1] >= e[2] for e in escolhidos):
            escolhidos.append(c)
        if len(escolhidos) == qtd:
            break
    return sorted(escolhidos, key=lambda c: c[1])


# ---------------------------------------------------------------- 4. legendas (estilo shorts)

def gerar_ass(palavras, ini_corte, fim_corte, destino, vertical, palavras_por_bloco=3):
    """Legenda .ass: blocos curtos em CAIXA ALTA, palavra falada destacada em amarelo."""
    if vertical:
        largura, altura, fonte, margem = 1080, 1920, 86, 560
    else:
        largura, altura, fonte, margem = 1920, 1080, 72, 90

    cab = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {largura}
PlayResY: {altura}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Padrao,Arial Black,{fonte},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,6,3,2,60,60,{margem},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    trecho = [p for p in palavras if p["s"] >= ini_corte - 0.05 and p["e"] <= fim_corte + 0.05]

    # agrupa em blocos de N palavras (quebra antes se houver pausa longa)
    blocos, atual = [], []
    for p in trecho:
        if atual and (len(atual) >= palavras_por_bloco or p["s"] - atual[-1]["e"] > 0.6):
            blocos.append(atual)
            atual = []
        atual.append(p)
    if atual:
        blocos.append(atual)

    linhas = []
    for b in blocos:
        for k, p in enumerate(b):
            ini = p["s"] - ini_corte
            fim = (b[k + 1]["s"] if k + 1 < len(b) else p["e"]) - ini_corte
            partes = []
            for m, q in enumerate(b):
                txt = q["w"].upper().replace("{", "").replace("}", "")
                partes.append(r"{\c&H00FFFF&}" + txt + r"{\c&HFFFFFF&}" if m == k else txt)
            linhas.append(f"Dialogue: 0,{seg_para_ass(ini)},{seg_para_ass(fim)},Padrao,,0,0,0,,{' '.join(partes)}")

    destino.write_text(cab + "\n".join(linhas) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 5. cortar + gravar legenda

def cortar(video, ini, fim, ass, saida, vertical):
    filtros = []
    if vertical:
        filtros.append("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920")
    if ass:
        filtros.append(f"ass={ass.name}")
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-ss", f"{ini:.3f}", "-i", str(video.resolve()), "-t", f"{fim - ini:.3f}"]
    if filtros:
        cmd += ["-vf", ",".join(filtros)]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", saida.name]
    # roda dentro da pasta de saída: evita problemas de escape no caminho da legenda
    subprocess.run(cmd, check=True, cwd=saida.parent)


# ---------------------------------------------------------------- principal

def main():
    ap = argparse.ArgumentParser(description="Corta vídeos e grava legendas com IA local.")
    ap.add_argument("fonte", help="URL do YouTube ou arquivo de vídeo local")
    ap.add_argument("--auto", type=int, metavar="N", help="escolhe sozinho os N melhores trechos")
    ap.add_argument("--inicio", help="início do corte manual (ex: 1:20)")
    ap.add_argument("--fim", help="fim do corte manual (ex: 2:05)")
    ap.add_argument("--min", type=float, default=25, help="duração mínima do corte automático (s)")
    ap.add_argument("--max", type=float, default=60, help="duração máxima do corte automático (s)")
    ap.add_argument("--formato", choices=["vertical", "original"], default="vertical")
    ap.add_argument("--modelo", default="small",
                    help="modelo Whisper: tiny, base, small, medium, large-v3 (maior = melhor e mais lento)")
    ap.add_argument("--idioma", default="pt", help="idioma da fala (pt, en, es...)")
    ap.add_argument("--sem-legenda", action="store_true", help="só corta, sem legenda")
    ap.add_argument("--saida", default="cortes", help="pasta de saída")
    a = ap.parse_args()

    if not a.auto and not (a.inicio and a.fim):
        ap.error("use --auto N  ou  --inicio X --fim Y")

    checar_ffmpeg()
    saida = Path(a.saida)
    saida.mkdir(parents=True, exist_ok=True)
    vertical = a.formato == "vertical"

    # 1. obter vídeo
    if re.match(r"https?://", a.fonte):
        pasta_dl = saida / "_downloads"
        pasta_dl.mkdir(exist_ok=True)
        video, titulo = baixar(a.fonte, pasta_dl)
    else:
        video = Path(a.fonte)
        if not video.exists():
            sys.exit(f"arquivo não encontrado: {video}")
        titulo = video.stem
    base = nome_seguro(titulo)

    # 2. definir cortes
    if a.inicio and a.fim:
        ini, fim = tempo_para_seg(a.inicio), tempo_para_seg(a.fim)
        palavras = [] if a.sem_legenda else transcrever(video, a.modelo, a.idioma, ini, fim)
        cortes = [(None, ini, fim, "corte manual", None)]
    else:
        cache = video.with_suffix(f".{a.modelo}.palavras.json")
        palavras = transcrever(video, a.modelo, a.idioma, cache=cache)
        if not palavras:
            sys.exit("nenhuma fala detectada no vídeo.")
        log("analisando os melhores momentos...")
        energia = energia_por_segundo(video)
        cortes = escolher_cortes(palavras, energia, a.auto, a.min, a.max)
        if not cortes:
            sys.exit("nenhum trecho com a duração pedida. Tente ajustar --min/--max.")

    # 3. gerar cada corte
    relatorio = []
    for n, (nota, ini, fim, motivo, _) in enumerate(cortes, 1):
        nome = f"{base}_{n:02d}"
        arq = saida / f"{nome}.mp4"
        ass = None
        if not a.sem_legenda:
            ass = saida / f"{nome}.ass"
            gerar_ass(palavras, ini, fim, ass, vertical)
        log(f"corte {n}/{len(cortes)}: {seg_para_ass(ini)} → {seg_para_ass(fim)}  ({motivo})")
        cortar(video, ini, fim, ass, arq, vertical)
        texto = " ".join(p["w"] for p in palavras if ini <= p["s"] <= fim)
        relatorio.append({"arquivo": arq.name, "inicio": round(ini, 2), "fim": round(fim, 2),
                          "nota": round(nota, 3) if nota is not None else None,
                          "por_que": motivo, "texto": texto})

    (saida / f"{base}_relatorio.json").write_text(
        json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"pronto! {len(cortes)} corte(s) em: {saida.resolve()}")


if __name__ == "__main__":
    main()

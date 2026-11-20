#!/usr/bin/env python3
"""
Servidor de processamento do Cortador 9:16.

  python app.py                 -> interface em http://localhost:8765
  CORTADOR_TOKEN=xyz python app.py   -> exige token (use ao expor numa VPS)

A interface também pode ficar hospedada na Vercel e apontar para este servidor.
Tudo roda nesta máquina. Nenhuma API de terceiros.
"""
import hmac
import json
import os
import re
import threading
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import cortador as c

RAIZ = Path(__file__).resolve().parent
HOST = os.environ.get("CORTADOR_HOST", "127.0.0.1")   # 0.0.0.0 para aceitar conexões externas
PORTA = int(os.environ.get("CORTADOR_PORTA", "8765"))
MODELO = os.environ.get("CORTADOR_MODELO", "small")    # tiny / base / small / medium / large-v3
IDIOMA = os.environ.get("CORTADOR_IDIOMA", "pt")
TOKEN = os.environ.get("CORTADOR_TOKEN", "")
# origens que podem chamar este servidor; "*" libera todas (o token continua valendo)
ORIGENS = [o.strip() for o in os.environ.get("CORTADOR_ORIGENS", "*").split(",") if o.strip()]
PASTA = RAIZ / "trabalhos"
WEB = RAIZ.parent / "web"
ESTATICOS = {"/": ("index.html", "text/html; charset=utf-8"),
             "/favicon.svg": ("favicon.svg", "image/svg+xml")}
DURACOES = {60: (45, 60), 90: (75, 90)}   # escolha -> (mínimo, máximo) em segundos

TRABALHOS = {}
TRAVA = threading.Lock()


def atualizar(jid, **campos):
    with TRAVA:
        TRABALHOS[jid].update(campos)


# ------------------------------------------------------------------ processamento

def processar(jid, fonte, duracao, qtd):
    pasta = PASTA / jid
    pasta.mkdir(parents=True, exist_ok=True)
    dmin, dmax = DURACOES[duracao]

    # cada etapa ocupa uma faixa da barra de progresso
    def faixa(ini, fim, etapa):
        return lambda f: atualizar(jid, etapa=etapa, pct=round(ini + (fim - ini) * f, 1))

    try:
        # 1. vídeo
        if re.match(r"https?://", fonte):
            faixa(0, 15, "Baixando vídeo")(0)
            video, titulo = c.baixar(fonte, pasta, faixa(0, 15, "Baixando vídeo"))
        else:
            video, titulo = Path(fonte), Path(fonte).stem
            if not video.exists():
                raise FileNotFoundError("Arquivo não encontrado.")
        atualizar(jid, titulo=titulo)

        # 2. transcrição local
        faixa(15, 70, "Transcrevendo com IA (local)")(0)
        palavras = c.transcrever(video, MODELO, IDIOMA,
                                 cache=video.with_suffix(f".{MODELO}.palavras.json"),
                                 progresso=faixa(15, 70, "Transcrevendo com IA (local)"))
        if not palavras:
            raise RuntimeError("Nenhuma fala detectada no vídeo.")

        # 3. melhores trechos
        atualizar(jid, etapa="Escolhendo os melhores momentos", pct=72)
        cortes = c.escolher_cortes(palavras, c.energia_por_segundo(video), qtd, dmin, dmax)
        if not cortes:
            raise RuntimeError(f"O vídeo não tem trechos de {dmin}–{dmax} s com fala contínua.")

        # 4. cortar 9:16 + legenda
        base = c.nome_seguro(titulo)
        prontos = []
        for n, (nota, ini, fim, motivo, _) in enumerate(cortes, 1):
            atualizar(jid, etapa=f"Cortando {n} de {len(cortes)}",
                      pct=round(75 + 25 * (n - 1) / len(cortes), 1))
            nome = f"{base}_{duracao}s_{n:02d}"
            ass = pasta / f"{nome}.ass"
            c.gerar_ass(palavras, ini, fim, ass, vertical=True)
            c.cortar(video, ini, fim, ass, pasta / f"{nome}.mp4", vertical=True)
            prontos.append({
                "arquivo": f"{nome}.mp4",
                "duracao": round(fim - ini),
                "inicio": c.seg_para_ass(ini).split(".")[0],
                "nota": round(nota * 100),
                "por_que": motivo,
                "texto": " ".join(p["w"] for p in palavras if ini <= p["s"] <= fim)[:220],
            })
            atualizar(jid, cortes=list(prontos))

        atualizar(jid, etapa="Pronto!", pct=100, fim=True)
    except Exception as e:
        traceback.print_exc()
        atualizar(jid, erro=str(e) or e.__class__.__name__, fim=True)


# ------------------------------------------------------------------ servidor

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        origem = self.headers.get("Origin")
        if origem and ("*" in ORIGENS or origem in ORIGENS):
            self.send_header("Access-Control-Allow-Origin", origem)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token, Range")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Expose-Headers", "Content-Disposition, Content-Range")
            # Chrome: site público (Vercel) chamando localhost/rede privada
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _autorizado(self, query):
        if not TOKEN:
            return True
        enviado = self.headers.get("X-Token") or parse_qs(query).get("t", [""])[0]
        return hmac.compare_digest(enviado.encode(), TOKEN.encode())

    def _json(self, obj, code=200):
        dados = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ESTATICOS:
            nome, tipo = ESTATICOS[u.path]
            arq = WEB / nome
            if not arq.is_file():
                return self.send_error(404)
            dados = arq.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)
            return
        if not self._autorizado(u.query):
            return self._json({"erro": "Token inválido ou ausente."}, 401)
        if u.path == "/saude":
            self._json({"ok": True, "modelo": MODELO, "idioma": IDIOMA})
        elif u.path == "/status":
            jid = parse_qs(u.query).get("id", [""])[0]
            with TRAVA:
                t = dict(TRABALHOS.get(jid, {"erro": "Trabalho não encontrado", "fim": True}))
            self._json(t)
        elif m := re.fullmatch(r"/(baixar|ver)/([0-9a-f]{12})/([\w.-]+\.mp4)", u.path):
            self._arquivo(m.group(2), m.group(3), anexo=m.group(1) == "baixar")
        else:
            self.send_error(404)

    def _arquivo(self, jid, nome, anexo):
        arq = (PASTA / jid / nome).resolve()
        if not arq.is_file() or arq.parent != (PASTA / jid).resolve():
            return self.send_error(404)
        tam = arq.stat().st_size
        ini, fim = 0, tam - 1
        intervalo = self.headers.get("Range")
        if intervalo and (m := re.match(r"bytes=(\d*)-(\d*)", intervalo)):   # p/ o player do navegador
            ini = int(m.group(1) or 0)
            fim = int(m.group(2)) if m.group(2) else tam - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {ini}-{fim}/{tam}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(fim - ini + 1))
        if anexo:
            self.send_header("Content-Disposition", f'attachment; filename="{nome}"')
        self.end_headers()
        with open(arq, "rb") as f:
            f.seek(ini)
            resta = fim - ini + 1
            try:
                while resta > 0:
                    bloco = f.read(min(1 << 16, resta))
                    if not bloco:
                        break
                    self.wfile.write(bloco)
                    resta -= len(bloco)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/iniciar":
            return self.send_error(404)
        if not self._autorizado(u.query):
            return self._json({"erro": "Token inválido ou ausente."}, 401)
        try:
            corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            fonte = str(corpo.get("url", "")).strip()
            duracao = int(corpo.get("duracao", 60))
            qtd = max(1, min(int(corpo.get("qtd", 3)), 10))
        except (ValueError, json.JSONDecodeError):
            return self._json({"erro": "Pedido inválido"}, 400)
        if not fonte:
            return self._json({"erro": "Cole o link do vídeo."}, 400)
        if duracao not in DURACOES:
            return self._json({"erro": "Duração deve ser 60 ou 90."}, 400)

        jid = uuid.uuid4().hex[:12]
        with TRAVA:
            TRABALHOS[jid] = {"etapa": "Na fila", "pct": 0, "cortes": [], "fim": False}
        threading.Thread(target=processar, args=(jid, fonte, duracao, qtd), daemon=True).start()
        self._json({"id": jid})


def main():
    PASTA.mkdir(exist_ok=True)
    c.checar_ffmpeg()
    if HOST not in ("127.0.0.1", "localhost") and not TOKEN:
        print("AVISO: servidor exposto sem CORTADOR_TOKEN — qualquer um poderá usar sua máquina.")
    srv = ThreadingHTTPServer((HOST, PORTA), Handler)
    url = f"http://localhost:{PORTA}"
    print(f"Cortador 9:16 rodando em {url}  (Ctrl+C para sair)")
    if HOST in ("127.0.0.1", "localhost") and not os.environ.get("CORTADOR_SEM_NAVEGADOR"):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nencerrado.")


if __name__ == "__main__":
    main()

<p align="center"><img src="assets/banner.svg" alt="Cortador 9:16 — cortes verticais com legenda por IA no navegador" width="100%"></p>

<p align="center">
  <a href="https://cortador-916.vercel.app"><img src="https://img.shields.io/badge/Abrir_o_site-cortador--916.vercel.app-22D3EE?logo=vercel&logoColor=white&labelColor=0B1220" alt="Abrir o site na Vercel"></a>
  <a href="https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2Fticarollamas-arch%2Fcortador-916&project-name=cortador-916"><img src="https://vercel.com/button" alt="Deploy with Vercel"></a>
</p>

# Cortador 9:16 — cortes verticais com legenda por IA no navegador

Site estático que transforma um vídeo longo em **cortes verticais 9:16 de 60 ou 90 segundos**, com **legenda gravada no vídeo**. O corte acontece **no navegador de quem usa**, inclusive no celular, com canvas + MediaRecorder, e o vídeo não sai do aparelho. A transcrição tem dois modos: **OpenAI Whisper** (rápido, via função da Vercel que guarda a chave) ou **Whisper no próprio aparelho** via transformers.js (grátis, mais lento).

Hospedado na **Vercel**: [cortador-916.vercel.app](https://cortador-916.vercel.app).

> Use em vídeos próprios ou de criadores que autorizaram os cortes. Publicar cortes de terceiros sem permissão viola direitos autorais e pode gerar *strike* no canal.

<p align="center"><img src="assets/interface.png" alt="Interface do Cortador 9:16 no celular com um corte pronto para download" width="320"></p>

## Recursos

| Etapa | O que faz | Como |
|---|---|---|
| Entrada | Vídeo enviado do aparelho (MP4, MOV, WEBM) | `<input type="file">`, sem upload |
| Transcrição | Texto com o tempo de cada palavra | OpenAI `whisper-1` via `api/transcrever.js`, ou Whisper no navegador |
| Escolha dos trechos | Pontua candidatos que começam e terminam em fim de frase | ritmo da fala, volume, palavras de gancho, perguntas e abertura |
| Corte | Recorte central 9:16 em 1080×1920 | canvas + `MediaRecorder` (MP4 H.264 no Chrome atual) |
| Legenda | Blocos curtos em caixa alta com a palavra falada em amarelo | desenhada quadro a quadro no canvas |

- **Explicável:** cada corte mostra a nota e os critérios que o fizeram ser escolhido.
- **Duração:** opção 60 s gera cortes de 45–60 s; opção 90 s, de 75–90 s.
- **Privado:** o vídeo nunca é enviado. No modo OpenAI só o áudio (16 kHz, em pedaços de ~2 min) vai para a transcrição.
- **Chave protegida:** a chave da OpenAI fica numa variável de ambiente da Vercel; o site exige um código de acesso.

## Como usar

1. Abra o site, toque em **Escolher vídeo do aparelho**.
2. Escolha **60 s** ou **90 s**, quantos cortes e a qualidade da IA.
3. Toque em **Começar** e deixe a tela aberta.
4. Toque em **⬇ Baixar MP4** em cada corte.

Por que não aceita link do YouTube: o navegador não pode baixar vídeos do YouTube (bloqueio de CORS) e a Vercel não roda downloads nem ffmpeg. Para usar link, veja o modo servidor abaixo.

## Deploy na Vercel

1. Clique em **Deploy with Vercel** no topo, ou importe o repositório em **vercel.com/new**. O `vercel.json` já define o build (`build.sh` copia a página para `public/`) e os cabeçalhos `COOP`/`COEP`.
2. Em **Settings → Environment Variables**, crie:

| Variável | Valor |
|---|---|
| `OPENAI_API_KEY` | chave criada em platform.openai.com/api-keys |
| `CODIGO_ACESSO` | um código que você inventa; digite o mesmo no site |

3. Faça **Redeploy** para as variáveis valerem. Confira em `/api/transcrever`: deve responder `{"ok":true}`.

Custo do modo OpenAI: cerca de US$ 0,006 por minuto de áudio (um vídeo de 20 min ≈ US$ 0,12).

## Modo servidor (opcional, aceita link do YouTube)

A pasta `servidor/` traz a versão em Python (yt-dlp + faster-whisper + ffmpeg), para rodar no PC ou numa VPS:

```bash
cd servidor
./iniciar.sh                                              # Windows: iniciar.bat
python cortador.py "https://youtu.be/XXXX" --auto 3 --max 60
```

## Limitações

- No modo no aparelho, o tempo de cada palavra é estimado dentro de cada frase, então o destaque pode adiantar ou atrasar um pouco (no modo OpenAI o tempo é por palavra).
- A gravação é em tempo real: um corte de 60 s leva 60 s, com a tela ligada.
- Vídeos muito longos (mais de ~20 min) podem esgotar a memória do celular ao ler o áudio.
- Em CPU, a transcrição pode levar mais que a duração do vídeo; com WebGPU é bem mais rápida.
- O recorte 9:16 é central: não segue o rosto de quem fala.
- Navegadores sem gravação em MP4 salvam em WEBM.

## Tecnologias

JavaScript (sem framework) · Vercel Functions · OpenAI Whisper API · transformers.js · Whisper (ONNX) · WebGPU/WebAssembly · Canvas · MediaRecorder · Vercel · Python (modo servidor)

## Licença

Distribuído sob a licença MIT. Veja [LICENSE](LICENSE).

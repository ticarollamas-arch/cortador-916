// Função da Vercel: recebe um pedaço de áudio WAV do navegador e repassa para a
// API de transcrição da OpenAI. A chave fica só aqui (variável de ambiente),
// nunca no site.
//
// Variáveis de ambiente (Vercel → Settings → Environment Variables):
//   OPENAI_API_KEY   chave da OpenAI
//   CODIGO_ACESSO    código que você inventa; o site precisa enviá-lo

const LIMITE_BYTES = 4 * 1024 * 1024; // a Vercel aceita no máximo 4,5 MB por pedido

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
  });

function iguais(a, b) {
  // comparação em tempo constante para não vazar o código por tempo de resposta
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

// O Whisper devolve as palavras sem pontuação; os segmentos têm. Junta as duas coisas
// para o site saber onde termina cada frase.
export function juntarPontuacao(palavras = [], segmentos = []) {
  const saida = palavras.map(p => ({ w: String(p.word || '').trim(), s: p.start, e: p.end })).filter(p => p.w);
  for (const seg of segmentos) {
    const final = String(seg.text || '').trim().match(/[.!?…]+$/);
    if (!final) continue;
    let ultima = -1;
    for (let i = 0; i < saida.length; i++) {
      if (saida[i].s >= seg.start - 0.05 && saida[i].s < seg.end) ultima = i;
    }
    if (ultima >= 0 && !/[.!?…]$/.test(saida[ultima].w)) saida[ultima].w += final[0].replace('…', '.');
  }
  return saida;
}

export async function POST(request) {
  const chave = process.env.OPENAI_API_KEY;
  const codigo = process.env.CODIGO_ACESSO;
  if (!chave || !codigo) {
    return json({ erro: 'Servidor sem configuração: defina OPENAI_API_KEY e CODIGO_ACESSO na Vercel.' }, 500);
  }
  if (!iguais(request.headers.get('x-codigo') || '', codigo)) {
    return json({ erro: 'Código de acesso inválido.' }, 401);
  }

  const audio = await request.arrayBuffer();
  if (!audio.byteLength) return json({ erro: 'Áudio vazio.' }, 400);
  if (audio.byteLength > LIMITE_BYTES) return json({ erro: 'Pedaço de áudio grande demais.' }, 413);

  const form = new FormData();
  form.append('file', new Blob([audio], { type: 'audio/wav' }), 'trecho.wav');
  form.append('model', 'whisper-1');
  form.append('language', (request.headers.get('x-idioma') || 'pt').slice(0, 5));
  form.append('response_format', 'verbose_json');
  form.append('timestamp_granularities[]', 'word');
  form.append('timestamp_granularities[]', 'segment');

  let r;
  try {
    r = await fetch('https://api.openai.com/v1/audio/transcriptions', {
      method: 'POST',
      headers: { Authorization: `Bearer ${chave}` },
      body: form,
    });
  } catch {
    return json({ erro: 'Não consegui falar com a OpenAI. Tente de novo.' }, 502);
  }
  const dados = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = dados?.error?.message || `OpenAI respondeu ${r.status}`;
    const status = r.status === 401 ? 500 : r.status === 429 ? 429 : 502;
    return json({ erro: r.status === 401 ? 'Chave da OpenAI inválida (confira OPENAI_API_KEY).' :
                        r.status === 429 ? 'Limite ou créditos da OpenAI esgotados: ' + msg : msg }, status);
  }
  return json({ palavras: juntarPontuacao(dados.words, dados.segments), duracao: dados.duration ?? null });
}

export function GET() {
  const pronto = !!(process.env.OPENAI_API_KEY && process.env.CODIGO_ACESSO);
  return json({ ok: pronto, motivo: pronto ? null : 'defina OPENAI_API_KEY e CODIGO_ACESSO na Vercel' });
}

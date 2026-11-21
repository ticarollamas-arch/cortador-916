// Converte o MP4 fragmentado que o MediaRecorder grava (cabeçalho com duração 0,
// vários pedaços moof/mdat) num MP4 comum com a duração correta e o índice no início
// (faststart). Não recodifica nada: só reorganiza as amostras.
// Sem isso, galeria, WhatsApp e Instagram costumam mostrar só os primeiros segundos.

const u32 = (d, o) => ((d[o] << 24) >>> 0) + (d[o + 1] << 16) + (d[o + 2] << 8) + d[o + 3];
const i32 = (d, o) => u32(d, o) | 0;
const u64 = (d, o) => u32(d, o) * 2 ** 32 + u32(d, o + 4);
const tipo = (d, o) => String.fromCharCode(d[o], d[o + 1], d[o + 2], d[o + 3]);

function caixas(d, ini = 0, fim = d.length) {
  const out = [];
  let o = ini;
  while (o + 8 <= fim) {
    let tam = u32(d, o), cab = 8;
    if (tam === 1) { tam = u64(d, o + 8); cab = 16; }
    else if (tam === 0) tam = fim - o;
    if (tam < cab || o + tam > fim) break;
    out.push({ t: tipo(d, o + 4), o, tam, cab, ini: o + cab, fim: o + tam });
    o += tam;
  }
  return out;
}
const filho = (d, cx, t) => caixas(d, cx.ini, cx.fim).find(c => c.t === t);
const filhos = (d, cx, t) => caixas(d, cx.ini, cx.fim).filter(c => c.t === t);
const bytes = (d, cx) => d.subarray(cx.o, cx.fim);

// ---------------------------------------------------------- escrita
function caixa(t, ...partes) {
  const corpo = partes.flat().filter(Boolean);
  const tam = 8 + corpo.reduce((a, p) => a + p.length, 0);
  const out = new Uint8Array(tam), dv = new DataView(out.buffer);
  dv.setUint32(0, tam);
  for (let i = 0; i < 4; i++) out[4 + i] = t.charCodeAt(i);
  let o = 8;
  for (const p of corpo) { out.set(p, o); o += p.length; }
  return out;
}
function cheia(t, versao, flags, corpo) {
  const cab = new Uint8Array(4);
  cab[0] = versao; cab[1] = (flags >> 16) & 255; cab[2] = (flags >> 8) & 255; cab[3] = flags & 255;
  return caixa(t, cab, corpo);
}
function numeros(lista, largura = 4) {
  const out = new Uint8Array(lista.length * largura), dv = new DataView(out.buffer);
  lista.forEach((v, i) => largura === 8 ? dv.setBigUint64(i * 8, BigInt(v)) : dv.setUint32(i * 4, v >>> 0));
  return out;
}

// duração (versão 0 = 32 bits, versão 1 = 64 bits) em tkhd/mdhd/mvhd
function comDuracao(d, cx, valor) {
  const b = bytes(d, cx).slice(), dv = new DataView(b.buffer), v = b[cx.cab];
  const base = cx.cab + 4;
  if (cx.t === 'tkhd') {
    if (v === 1) dv.setBigUint64(base + 24, BigInt(valor)); else dv.setUint32(base + 16, valor >>> 0);
  } else { // mvhd e mdhd: criação, modificação, escala, duração
    if (v === 1) dv.setBigUint64(base + 20, BigInt(valor)); else dv.setUint32(base + 12, valor >>> 0);
  }
  return b;
}
function escala(d, cx) { // timescale de mvhd/mdhd
  const v = d[cx.ini];
  return u32(d, cx.ini + 4 + (v === 1 ? 16 : 8));
}

// ---------------------------------------------------------- principal
export function desfragmentarMP4(entrada) {
  const d = entrada instanceof Uint8Array ? entrada : new Uint8Array(entrada);
  const topo = caixas(d);
  const moov = topo.find(c => c.t === 'moov'), ftyp = topo.find(c => c.t === 'ftyp');
  const moofs = topo.filter(c => c.t === 'moof');
  if (!moov || !moofs.length) return d;              // já é um MP4 comum

  const mvex = filho(d, moov, 'mvex');
  const padroes = {};
  for (const trex of mvex ? filhos(d, mvex, 'trex') : []) {
    const b = trex.ini + 4;
    padroes[u32(d, b)] = { dur: u32(d, b + 8), tam: u32(d, b + 12), flags: u32(d, b + 16) };
  }

  // lê todas as amostras de todos os fragmentos
  const trilhas = {};
  for (const trak of filhos(d, moov, 'trak')) {
    const tkhd = filho(d, trak, 'tkhd'), v = d[tkhd.ini];
    const id = u32(d, tkhd.ini + 4 + (v === 1 ? 16 : 8));
    trilhas[id] = { trak, amostras: [], blocos: [] };
  }
  for (const moof of moofs) {
    for (const traf of filhos(d, moof, 'traf')) {
      const tfhd = filho(d, traf, 'tfhd');
      const fl = u32(d, tfhd.ini) & 0xffffff;
      let p = tfhd.ini + 4;
      const id = u32(d, p); p += 4;
      const pad = padroes[id] || { dur: 0, tam: 0, flags: 0 };
      let base = moof.o;
      if (fl & 0x1) { base = u64(d, p); p += 8; }
      if (fl & 0x2) p += 4;
      const dDur = fl & 0x8 ? u32(d, (p += 4) - 4) : pad.dur;
      const dTam = fl & 0x10 ? u32(d, (p += 4) - 4) : pad.tam;
      const dFlags = fl & 0x20 ? u32(d, (p += 4) - 4) : pad.flags;
      const tr = trilhas[id];
      if (!tr) continue;
      const tfdt = filho(d, traf, 'tfdt');
      if (tfdt) tr.tempo = d[tfdt.ini] === 1 ? u64(d, tfdt.ini + 4) : u32(d, tfdt.ini + 4);
      tr.tempo = tr.tempo || 0;
      let cursor = base;
      for (const trun of filhos(d, traf, 'trun')) {
        const tf = u32(d, trun.ini) & 0xffffff, ver = d[trun.ini];
        let q = trun.ini + 4;
        const n = u32(d, q); q += 4;
        if (tf & 0x1) { cursor = base + i32(d, q); q += 4; }
        let primeiraFlags = null;
        if (tf & 0x4) { primeiraFlags = u32(d, q); q += 4; }
        const bloco = { ini: tr.amostras.length, n };
        for (let i = 0; i < n; i++) {
          const dur = tf & 0x100 ? u32(d, (q += 4) - 4) : dDur;
          const tam = tf & 0x200 ? u32(d, (q += 4) - 4) : dTam;
          let flags = tf & 0x400 ? u32(d, (q += 4) - 4) : (i === 0 && primeiraFlags !== null ? primeiraFlags : dFlags);
          let cto = 0;
          if (tf & 0x800) { cto = ver === 0 ? u32(d, q) : i32(d, q); q += 4; }
          tr.amostras.push({ pos: cursor, tam, dur, sync: !(flags & 0x10000), cto, t: tr.tempo });
          tr.tempo += dur;
          cursor += tam;
        }
        if (n) tr.blocos.push(bloco);
      }
    }
  }

  const ids = Object.keys(trilhas).map(Number).filter(id => trilhas[id].amostras.length);
  // a duração real de cada amostra é a distância até a próxima (respeita o tfdt de cada pedaço)
  for (const id of ids) {
    const a = trilhas[id].amostras;
    for (let i = 0; i < a.length - 1; i++) { const dt = a[i + 1].t - a[i].t; if (dt > 0) a[i].dur = dt; }
    trilhas[id].inicio = a[0].t;
  }
  const mvhd = filho(d, moov, 'mvhd'), escMovie = escala(d, mvhd);

  // monta o moov novo (com offsets provisórios) para saber o tamanho
  function montarMoov(offsetsPorTrilha) {
    let durMovie = 0;
    const traks = ids.map(id => {
      const { trak, amostras, blocos } = trilhas[id];
      const mdia = filho(d, trak, 'mdia'), mdhd = filho(d, mdia, 'mdhd'), minf = filho(d, mdia, 'minf');
      const stbl = filho(d, minf, 'stbl'), stsd = filho(d, stbl, 'stsd');
      const escTrilha = escala(d, mdhd);
      const durTrilha = amostras.reduce((a, s) => a + s.dur, 0);
      const durNoMovie = Math.round(durTrilha * escMovie / escTrilha);
      durMovie = Math.max(durMovie, durNoMovie);

      const stts = []; for (const s of amostras) { const u = stts[stts.length - 1]; if (u && u[1] === s.dur) u[0]++; else stts.push([1, s.dur]); }
      const temCto = amostras.some(s => s.cto);
      const ctts = []; if (temCto) for (const s of amostras) { const u = ctts[ctts.length - 1]; if (u && u[1] === s.cto) u[0]++; else ctts.push([1, s.cto]); }
      const todasSync = amostras.every(s => s.sync);
      const stss = todasSync ? null : amostras.map((s, i) => s.sync ? i + 1 : 0).filter(Boolean);
      const stsc = []; blocos.forEach((b, i) => { const u = stsc[stsc.length - 1]; if (!u || u[1] !== b.n) stsc.push([i + 1, b.n, 1]); });
      const offs = offsetsPorTrilha ? offsetsPorTrilha[id] : blocos.map(() => 0);
      const grande = offs.some(x => x > 0xffffffff);

      const novoStbl = caixa('stbl',
        bytes(d, stsd),
        cheia('stts', 0, 0, [numeros([stts.length]), numeros(stts.flat())]),
        temCto ? cheia('ctts', amostras.some(s => s.cto < 0) ? 1 : 0, 0, [numeros([ctts.length]), numeros(ctts.flat())]) : null,
        stss ? cheia('stss', 0, 0, [numeros([stss.length]), numeros(stss)]) : null,
        cheia('stsc', 0, 0, [numeros([stsc.length]), numeros(stsc.flat())]),
        cheia('stsz', 0, 0, [numeros([0, amostras.length]), numeros(amostras.map(s => s.tam))]),
        grande ? cheia('co64', 0, 0, [numeros([offs.length]), numeros(offs, 8)])
               : cheia('stco', 0, 0, [numeros([offs.length]), numeros(offs)]),
      );
      const atraso = Math.round(trilhas[id].inicio * escMovie / escTrilha);
      const edts = atraso > 0 ? caixa('edts', cheia('elst', 0, 0, numeros([2, atraso, 0xffffffff, 0x00010000, durNoMovie, 0, 0x00010000]))) : null;
      const novoMinf = caixa('minf', caixas(d, minf.ini, minf.fim).map(c => c.t === 'stbl' ? novoStbl : bytes(d, c)));
      const novoMdia = caixa('mdia', caixas(d, mdia.ini, mdia.fim).map(c =>
        c.t === 'mdhd' ? comDuracao(d, c, durTrilha) : c.t === 'minf' ? novoMinf : bytes(d, c)));
      const partes = caixas(d, trak.ini, trak.fim).filter(c => !(edts && c.t === 'edts')).map(c =>
        c.t === 'tkhd' ? comDuracao(d, c, durNoMovie + atraso) : c.t === 'mdia' ? novoMdia : bytes(d, c));
      if (edts) partes.splice(1, 0, edts);
      durMovie = Math.max(durMovie, durNoMovie + atraso);
      return caixa('trak', partes);
    });
    const resto = caixas(d, moov.ini, moov.fim).filter(c => !['mvhd', 'trak', 'mvex'].includes(c.t)).map(c => bytes(d, c));
    return caixa('moov', comDuracao(d, mvhd, durMovie), traks, resto);
  }

  // mdat novo: os blocos na mesma ordem em que estavam (já intercalados)
  const ordem = [];
  for (const id of ids) trilhas[id].blocos.forEach((b, i) => ordem.push({ id, i, pos: trilhas[id].amostras[b.ini].pos }));
  ordem.sort((a, b) => a.pos - b.pos);
  const tamDados = ordem.reduce((a, x) => { const b = trilhas[x.id].blocos[x.i];
    return a + trilhas[x.id].amostras.slice(b.ini, b.ini + b.n).reduce((s, a2) => s + a2.tam, 0); }, 0);
  const cabMdat = tamDados + 8 > 0xffffffff ? 16 : 8;

  const ftypB = ftyp ? bytes(d, ftyp) : caixa('ftyp', new TextEncoder().encode('isom\0\0\x02\0isomiso2avc1mp41'));
  const tamMoov = montarMoov(null).length;
  let cursor = ftypB.length + tamMoov + cabMdat;
  const offsets = {}; ids.forEach(id => offsets[id] = []);
  for (const x of ordem) {
    const b = trilhas[x.id].blocos[x.i];
    offsets[x.id][x.i] = cursor;
    for (let k = b.ini; k < b.ini + b.n; k++) cursor += trilhas[x.id].amostras[k].tam;
  }
  const moovB = montarMoov(offsets);
  if (moovB.length !== tamMoov) throw new Error('mp4fix: tamanho do moov mudou');

  const out = new Uint8Array(ftypB.length + moovB.length + cabMdat + tamDados);
  out.set(ftypB, 0); out.set(moovB, ftypB.length);
  let o = ftypB.length + moovB.length;
  const dv = new DataView(out.buffer);
  if (cabMdat === 16) { dv.setUint32(o, 1); out.set([109, 100, 97, 116], o + 4); dv.setBigUint64(o + 8, BigInt(tamDados + 16)); }
  else { dv.setUint32(o, tamDados + 8); out.set([109, 100, 97, 116], o + 4); }
  o += cabMdat;
  for (const x of ordem) {
    const b = trilhas[x.id].blocos[x.i];
    for (let k = b.ini; k < b.ini + b.n; k++) { const s = trilhas[x.id].amostras[k]; out.set(d.subarray(s.pos, s.pos + s.tam), o); o += s.tam; }
  }
  return out;
}

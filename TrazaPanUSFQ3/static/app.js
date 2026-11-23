/* TrazaPanUSFQ - interfaz. Un solo archivo, sin librerias externas.
   Secciones: utilidades · inicio · bodega principal · bodega secundaria · cocina · produccion y hoja · etiquetas · consulta · administrar */
'use strict';

const $vista = document.getElementById('vista');
const $quien = document.getElementById('quien');

// ------------------------------------------------------------------ utilidades
function h(etiqueta, attrs, ...hijos) {
  const el = document.createElement(etiqueta);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'value') el.value = v;
    else if (k === 'checked' || k === 'disabled' || k === 'selected') el[k] = !!v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  const poner = (x) => {
    if (x === null || x === undefined || x === false) return;
    if (Array.isArray(x)) x.forEach(poner);
    else el.append(x.nodeType ? x : document.createTextNode(String(x)));
  };
  hijos.forEach(poner);
  return el;
}

// Los registros se diferencian por área (no por persona): «quién» es el área de este dispositivo.
function quien() { return area() && AREAS[area()] ? AREAS[area()].nombre : ''; }
function pintarQuien() { $quien.hidden = true; }

// Contraseñas por área: requiere = el servidor pide contraseña; area = área con la sesión iniciada.
const sesion = { requiere: false, area: null };
const necesitaClave = () => sesion.requiere && area() && sesion.area !== area();

async function api(ruta, datos) {
  const opciones = datos === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(Object.assign({ persona: quien() }, datos)),
  };
  let resp;
  try { resp = await fetch('/api' + ruta, opciones); }
  catch (e) { throw new Error('No hay conexión con el servidor de TrazaPanUSFQ. Revisa el wifi.'); }
  const json = await resp.json().catch(() => ({ error: 'El servidor respondió algo inesperado.' }));
  if (resp.status === 401 && json.login) {
    // La sesión del área terminó (o nunca se inició): se vuelve a pedir la contraseña.
    if (sesion.area) { sesion.area = null; setTimeout(navegar, 0); }
    throw new Error('Escribe la contraseña del área para continuar.');
  }
  if (!resp.ok) throw new Error(json.error || 'Error ' + resp.status);
  return json;
}

function avisar(texto, tipo) {
  const el = h('div', { class: 'aviso-flotante ' + (tipo || '') }, texto);
  document.getElementById('avisos').append(el);
  setTimeout(() => el.remove(), tipo === 'error' ? 7000 : 3000);
}
// Ejecuta una accion y muestra el error si falla.
async function intentar(fn) {
  try { return await fn(); } catch (e) { avisar(e.message, 'error'); return undefined; }
}

// Ventana emergente. Tocar fuera NO la cierra: solo sus botones, o la X si no es obligatoria.
// obligatoria = true: hay que elegir uno de los botones (sin X ni tecla Esc).
function modal(titulo, contenido, botones, obligatoria) {
  return new Promise((resolver) => {
    const cerrar = (valor) => { document.removeEventListener('keydown', alTeclear); velo.remove(); resolver(valor); };
    const alTeclear = (e) => { if (e.key === 'Escape' && !obligatoria) cerrar(null); };
    // Si falta algo, el mensaje sale dentro de la ventana y no se pierde lo escrito.
    // b.accion: se ejecuta con la ventana abierta (por ejemplo, guardar); si falla, la ventana sigue abierta.
    const error = h('div', { class: 'error-modal', role: 'alert', hidden: true });
    const pulsar = async (b, boton) => {
      error.hidden = true;
      try {
        if (b.accion) {
          boton.disabled = true;
          try { const r = await b.accion(); cerrar(r === undefined ? true : r); } finally { boton.disabled = false; }
        } else cerrar(typeof b.valor === 'function' ? b.valor() : b.valor);
      } catch (ex) {
        error.textContent = ex.message; error.hidden = false;
        error.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
    };
    const caja = h('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true' },
      obligatoria ? null : h('button', { class: 'cerrar-modal', type: 'button', 'aria-label': 'Cancelar', title: 'Cancelar', onclick: () => cerrar(null) }, '×'),
      h('h2', null, titulo), contenido, error,
      (botones || []).map((b) => h('button', { class: 'boton ' + (b.clase || ''), type: 'button',
        onclick: (e) => pulsar(b, e.currentTarget) }, b.texto)));
    const velo = h('div', { class: 'velo' }, caja);
    document.addEventListener('keydown', alTeclear);
    document.body.append(velo);
  });
}
function confirmar(titulo, texto, textoSi) {
  return modal(titulo, h('p', null, texto), [{ texto: textoSi || 'Sí', clase: 'principal', valor: true }, { texto: 'Cancelar', valor: null }]);
}
// Campo de texto que se escribe en MAYÚSCULAS mientras se teclea (nombres de productos, ingredientes y unidades).
function campoMayusculas(valor) {
  const campo = h('input', { type: 'text', value: (valor || '').toUpperCase(), autocapitalize: 'characters', class: 'mayusculas',
    oninput: (e) => { const pos = e.target.selectionStart; const v = e.target.value.toUpperCase();
      if (v !== e.target.value) { e.target.value = v; e.target.setSelectionRange(pos, pos); } } });
  return campo;
}
function pedirTexto(titulo, etiqueta, valorInicial, opc) {
  const mayus = opc && opc.mayusculas;
  const campo = mayus ? campoMayusculas(valorInicial) : h('input', { type: 'text', value: valorInicial || '' });
  setTimeout(() => campo.focus(), 50);
  return modal(titulo, h('div', null, h('label', null, etiqueta), campo),
    [{ texto: 'Aceptar', clase: 'principal', valor: () => (mayus ? campo.value.trim().toUpperCase() : campo.value.trim()) }, { texto: 'Cancelar', valor: null }]);
}
// Sube la foto de un producto de bodega (la app intenta quitarle el fondo).
async function subirFotoProducto(id, archivo) {
  avisar('Subiendo la foto y quitando el fondo…');
  const resp = await fetch('/api/bodega/productos/' + id + '/foto?persona=' + encodeURIComponent(quien()), { method: 'POST', body: archivo });
  const r = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(r.error || 'No se pudo guardar la foto.');
  return r;
}

const dia = (f) => (f && /^\d{4}-\d{2}-\d{2}/.test(f) ? f.slice(8, 10) + '/' + f.slice(5, 7) + '/' + f.slice(0, 4) : (f || ''));
const hora = (f) => (f ? f.slice(8, 10) + '/' + f.slice(5, 7) + ' ' + f.slice(11, 16) : '');
const NOMBRE_ESTADO = { en_curso: 'En curso', por_completar: 'Por completar', completa: 'Completa', anulada: 'Anulada' };
const CLASE_ESTADO = { en_curso: 'aviso', por_completar: 'rojo', completa: 'verde', anulada: 'gris' };

const num = (x) => String(Math.round(Number(x) * 1000) / 1000);
// Empaques: se registra en paquetes, cubetas, botellas... y la app muestra lo que equivale (kg, litros, huevos).
const nombreEmpaque = (e, n) => { const s = String(e || '').toLowerCase(); return Number(n) === 1 ? (s === 'bidon' ? 'bidón' : s) : (s === 'bidon' ? 'bidones' : s + 's'); };
const cuantos = (e) => (['CUBETA', 'FUNDA', 'BOTELLA', 'CAJA'].includes(e) ? '¿Cuántas ' : '¿Cuántos ') + nombreEmpaque(e, 2);
const unidadDe = (l, n) => (l && l.empaque ? nombreEmpaque(l.empaque, n) : (l && l.unidad) || 'unidades');
function equivalencia(cant, e) {
  if (!e || !e.empaque || !e.contenido || cant === '' || cant == null || isNaN(Number(cant))) return '';
  let total = Number(cant) * e.contenido, u = e.unidad_contenido;
  if (u === 'g' && total >= 1000) { total /= 1000; u = 'kg'; }
  if (u === 'ml' && total >= 1000) { total /= 1000; u = 'lt'; }
  return num(total) + ' ' + u;
}
const textoEmpaque = (e) => (e && e.empaque ? '1 ' + nombreEmpaque(e.empaque, 1) + ' = ' + num(e.contenido) + ' ' + e.unidad_contenido : '');
// "3 paquetes (150 kg)"
const cantidadLote = (n, l) => num(n) + ' ' + unidadDe(l, n) + (l && l.empaque && equivalencia(n, l) ? ' (' + equivalencia(n, l) + ')' : '');
// Campo de cantidad en empaques: solo enteros y debajo lo que equivale.
function campoEmpaques(l, valor, alCambiar, placeholder) {
  const eq = h('div', { class: 'chico equivale' });
  const pintarEq = (v) => { eq.textContent = l.empaque && v ? '= ' + equivalencia(v, l) : ''; };
  const campo = h('input', { type: 'text', inputmode: l.empaque ? 'numeric' : 'decimal', autocomplete: 'off', value: valor || '', placeholder: placeholder || 'ej. 2',
    oninput: (ev) => {
      if (l.empaque) { const v = ev.target.value.replace(/\D/g, ''); if (v !== ev.target.value) ev.target.value = v; }
      pintarEq(ev.target.value); alCambiar(ev.target.value);
    } });
  pintarEq(valor);
  return { campo, eq };
}

// Semáforo de caducidad: rojo = caducado, amarillo = por caducar, verde = con buen tiempo. El texto dice lo mismo que el color.
function semaforo(l) {
  if (!l.semaforo) return null;
  const d = l.dias_caducidad;
  const texto = l.semaforo === 'rojo' ? 'Caducado el ' + dia(l.caducidad)
    : l.semaforo === 'amarillo' ? (d === 0 ? 'Caduca hoy' : d === 1 ? 'Caduca mañana' : 'Caduca en ' + d + ' días') + ' (' + dia(l.caducidad) + ')'
      : 'Caduca el ' + dia(l.caducidad);
  return h('span', { class: 'semaforo ' + l.semaforo }, h('i'), texto);
}

// Dar de baja un lote (o parte) por caducado o contaminado. lugar: principal, secundaria o cocina.
// disponible: lo que queda en ese lugar (se propone darlo todo de baja); en cocina la cantidad es opcional.
async function darDeBaja(l, lugar, disponible) {
  const u = unidadDe(l, 2);
  // En bodega secundaria también: "Registrado por error" (el lote no llegó; vuelve a bodega principal).
  const opciones = [['CADUCADO', 'Caducado'], ['CONTAMINADO', 'Contaminado']].concat(lugar === 'secundaria' ? [['ERROR', 'Registrado por error']] : []);
  let bloqueCantidad = null, notaError = null;
  const motivo = segmento(opciones, l.semaforo === 'rojo' ? 'CADUCADO' : '', (v) => {
    if (bloqueCantidad) bloqueCantidad.hidden = v === 'ERROR';
    if (notaError) notaError.hidden = v !== 'ERROR';
  });
  const cantidad = h('input', { type: 'text', inputmode: 'decimal', autocomplete: 'off', value: disponible != null ? num(Math.max(disponible, 0)) : '',
    placeholder: lugar === 'cocina' ? 'Opcional' : 'Solo números',
    oninput: (e) => { const v = soloNumero(e.target.value); if (v !== e.target.value) e.target.value = v; } });
  const comentario = h('input', { type: 'text', placeholder: 'Qué pasó (opcional)' });
  const nombres = { principal: 'bodega principal', secundaria: 'bodega secundaria', cocina: 'cocina' };
  const d = await modal('Dar de baja', h('div', null,
    h('p', { style: 'margin-top:0' }, h('b', null, l.ingrediente), ' | ', h('b', { class: 'mono' }, l.lote), ' ', semaforo(l)),
    h('label', null, 'Motivo'), motivo.el,
    notaError = h('p', { class: 'nota', hidden: true }, 'El lote sale de bodega secundaria y vuelve a quedar en bodega principal, como si nunca hubiera pasado.'),
    bloqueCantidad = h('div', null,
      h('label', null, '¿Cuántas ' + u + ' se sacan de ' + nombres[lugar] + '?'), cantidad,
      disponible != null ? h('div', { class: 'chico suave' }, 'En ' + nombres[lugar] + ' quedan ' + cantidadLote(Math.max(disponible, 0), l) + '.') : null,
      h('label', null, 'Comentario'), comentario)),
  [{ texto: 'Dar de baja', clase: 'peligro-lleno', accion: async () => {
      const datos = { lote_id: l.id, lugar, motivo: motivo.valor, cantidad: cantidad.value.trim(), comentario: comentario.value.trim() };
      if (!datos.motivo) throw new Error('Elige el motivo.');
      if (datos.motivo === 'ERROR') { await api('/secundaria/devolver', { lote_id: l.id }); return { devuelto: true }; }
      return api('/bajas', datos);
    } },
    { texto: 'Cancelar', valor: null }]);
  if (!d) return false;
  if (d.devuelto) {
    avisar('Ingreso del lote ' + l.lote + ' anulado: vuelve a bodega principal', 'bien');
    return true;
  }
  const r = d;
  avisar('Lote ' + l.lote + ' dado de baja' + (r.vacio ? '' : ' (parcial)'), 'bien');
  return true;
}

// Sección "semáforo" (bodega principal y bodega secundaria): orden por urgencia, resumen de colores y tarjeta corta.
const ORDEN_SEM = { rojo: 0, amarillo: 1, verde: 2 };
const ordenarSemaforo = (lotes) => [...lotes].sort((a, b) => (ORDEN_SEM[a.semaforo] ?? 3) - (ORDEN_SEM[b.semaforo] ?? 3)
  || String(a.caducidad || '9999').localeCompare(String(b.caducidad || '9999')) || String(a.ingrediente).localeCompare(String(b.ingrediente)));
function leyendaSemaforo(lotes) {
  const cuenta = (c) => lotes.filter((l) => l.semaforo === c).length;
  return h('p', { class: 'leyenda' },
    h('span', { class: 'semaforo rojo' }, h('i'), cuenta('rojo') + ' caducado'), ' ',
    h('span', { class: 'semaforo amarillo' }, h('i'), cuenta('amarillo') + ' por caducar'), ' ',
    h('span', { class: 'semaforo verde' }, h('i'), cuenta('verde') + ' con buen tiempo'));
}
// nombre y lote en negrilla; cantidad disponible y proveedor; caducidad con su color.
function tarjetaSemaforo(l, nombre, disponible, acciones, foto) {
  return h('div', { class: 'lote borde-' + (l.semaforo || 'sin') },
    foto !== undefined ? fotoProducto(foto, nombre, 'en-lote') : null,
    h('div', { class: 'datos' },
      h('div', { class: 'titulo-lote' }, h('b', null, nombre), h('span', { class: 'separador' }, '|'), h('b', { class: 'mono' }, l.lote)),
      h('div', { class: 'chico' }, disponible === false ? h('span', { class: 'suave' }, l.fecha_ingreso ? 'Ingresó ' + dia(l.fecha_ingreso) : '')
        : disponible != null ? ['Quedan ', h('b', null, cantidadLote(Math.max(disponible, 0), l))] : h('span', { class: 'suave' }, 'Cantidad sin registrar'),
        l.proveedor ? h('span', { class: 'suave' }, (disponible === false && !l.fecha_ingreso ? '' : ' · ') + l.proveedor) : null),
      h('div', null, semaforo(l) || h('span', { class: 'etq gris' }, 'Sin fecha de caducidad'))),
    acciones ? h('div', { class: 'acciones-lote' }, acciones) : null);
}
function buscadorLotes(caja, valor, alCambiar) {
  return h('input', { type: 'search', placeholder: 'Buscar por nombre o lote', value: valor,
    oninput: (e) => { const pos = e.target.selectionStart; alCambiar(e.target.value);
      const nuevo = caja.querySelector('input[type=search]'); if (nuevo) { nuevo.focus(); nuevo.setSelectionRange(pos, pos); } } });
}

function tarjetaLote(l, opc) {
  opc = opc || {};
  const cu = (x) => cantidadLote(x, l);
  const cad = l.caducidad && !l.semaforo ? 'caduca ' + dia(l.caducidad) : null;     // si hay semáforo, la fecha va ahí
  const detalle = opc.cocina ? (l.disponible_cocina != null ? 'Quedan ' + cu(Math.max(l.disponible_cocina, 0)) + ' en bodega secundaria' : '')
    : opc.breve ? [l.producto_bodega, l.proveedor, cad,
    opc.saldo && l.disponible != null ? 'quedan ' + cu(Math.max(l.disponible, 0)) : null].filter((x, i) => x && !(opc.saldo && i === 0)).join(' · ')
    : [l.producto_bodega, l.proveedor, l.fecha_ingreso && 'ingresó ' + dia(l.fecha_ingreso),
    l.recibido != null ? 'recibido ' + cu(l.recibido) : l.cantidad,
    l.trasladado ? 'pasó a secundaria ' + cu(l.trasladado) : null,
    l.disponible != null && !l.principal_vacio && (l.trasladado || l.a_cocina) ? 'quedan en principal ' + cu(l.disponible) : null,
    l.disponible_cocina != null ? 'quedan en secundaria ' + cu(Math.max(l.disponible_cocina, 0)) : null,
    l.a_cocina ? 'tomado por cocina ' + cu(l.a_cocina) : null,
    cad].filter(Boolean).join(' · ');
  const el = h('div', { class: 'lote' + (opc.elegible ? ' elegible' : '') + (opc.marcado ? ' marcado' : '') },
    opc.elegible ? h('div', { class: 'caja' }, opc.marcado ? '✓' : '') : null,
    h('div', { class: 'datos' },
      opc.conIngrediente ? h('div', { class: 'chico suave' }, l.ingrediente) : null,
      h('div', { class: 'codigo' }, l.lote, l.foto_recepcion ? h('button', { class: 'boton-enlace ver-etiqueta', type: 'button',
        onclick: (e) => { e.stopPropagation(); verFoto('/fotos/' + l.foto_recepcion, 'Lote ' + l.lote); } }, '📷 Ver etiqueta') : null),
      detalle ? h('div', { class: 'chico suave' }, detalle) : null,
      opc.cocina ? h('div', null, semaforo(l)) : h('div', null,
        semaforo(l), ' ',
        l.abierto_en ? h('span', { class: 'etq verde' }, 'Abierto en cocina desde ' + hora(l.abierto_en)) : null, ' ',
        l.origen === 'manual' ? h('span', { class: 'etq rojo' }, 'Anotado a mano') : null, ' ',
        l.origen === 'interno' ? h('span', { class: 'etq gris' }, 'Hecho en la panadería') : null, ' ',
        l.revisar ? h('span', { class: 'etq aviso' }, 'Por revisar') : null, ' ',
        l.agotado ? h('span', { class: 'etq gris' }, 'Agotado') : null),
      l.revisar && l.nota && opc.conNota ? h('div', { class: 'chico suave' }, l.nota) : null),
    opc.acciones || null);
  if (opc.elegible && opc.alTocar) el.addEventListener('click', opc.alTocar);
  return el;
}

// Al abrir un lote nuevo, pregunta que paso con los que ya estaban abiertos del mismo ingrediente.
// Nombre del recipiente: "recipiente 2 de HARINA DE TRIGO", "recipiente de SAL" o "empaque de MANTEQUILLA".
const nombreRecipiente = (g) => (g.n_recipientes === 0 ? 'empaque' : g.n_recipientes > 1 ? 'recipiente ' + g.recipiente : 'recipiente');
const listaLotes = (lotes) => lotes.map((l) => l.lote).join(', ');

// Despues de poner un lote nuevo: ¿lo anterior sigue ahi (se mezclo) o el recipiente estaba vacio?
function preguntaSeAcabo(g, cancelable) {
  const empaque = g.n_recipientes === 0;
  const cosa = empaque ? 'empaque' : 'recipiente';
  const cual = g.n_recipientes > 1 ? 'El recipiente ' + g.recipiente + ' de ' + g.ingrediente : 'El ' + cosa + ' anterior de ' + g.ingrediente;
  return modal('¿' + cual + ' se acabó?',
    h('div', null, h('p', null, 'Lote anterior: ', h('b', { class: 'mono' }, listaLotes(g.anteriores))),
      h('p', { class: 'suave' }, 'Lote nuevo: ', h('b', { class: 'mono' }, listaLotes(g.nuevos)))),
    [{ texto: 'Sí, empezamos con un ' + cosa + ' nuevo', clase: 'principal', valor: 'vacio' },
      { texto: empaque ? 'No, seguimos usando los dos lotes' : 'No, mezclamos ambos lotes', valor: 'mezcla' }], !cancelable);
}

// Antes de registrar en cocina: pregunta por lo que ya estaba en cada recipiente. null = se canceló con la X (no se guarda nada).
async function preguntarAntesDeAbrir(ing, items, lotes) {
  const { grupos } = await api('/cocina/recipientes');
  const decisiones = [];
  const porRecipiente = new Map();
  items.forEach((it) => { const r = it.recipiente || 1; porRecipiente.set(r, (porRecipiente.get(r) || []).concat(it.lote_id)); });
  for (const [recipiente, ids] of porRecipiente) {
    const g = grupos.find((x) => x.ingrediente_id === ing.id && x.recipiente === recipiente);
    if (!g) continue;
    const yaAbiertos = new Set(g.lotes.map((l) => l.id));
    const nuevos = lotes.filter((l) => ids.includes(l.id) && !yaAbiertos.has(l.id));
    const anteriores = g.lotes.filter((l) => !ids.includes(l.id));
    if (!nuevos.length || !anteriores.length) continue;
    const r = await preguntaSeAcabo(Object.assign({}, g, { anteriores, nuevos }), true);
    if (!r) return null;
    decisiones.push({ recipiente, respuesta: r, anteriores });
  }
  return decisiones;
}

async function preguntarAnteriores(grupos, yaRespondidos) {
  for (const g of grupos || []) {
    let quedan = yaRespondidos && yaRespondidos.has(g.recipiente) ? g.nuevos.length : g.lotes.length;
    if (g.anteriores.length && !yaRespondidos) {
      const r = await preguntaSeAcabo(g, false);
      if (r === 'vacio') {
        await intentar(() => api('/cocina/vaciar', { ingrediente_id: g.ingrediente_id, recipiente: g.recipiente, lote_ids: g.anteriores.map((l) => l.id) }));
        quedan = g.nuevos.length;
      }
    }
    if (g.n_recipientes !== 0 && quedan > 3) await avisoMezcla(g, quedan);
  }
}

function avisoMezcla(g, n) {
  return modal('Muchos lotes mezclados',
    h('div', null, h('p', null, 'El ' + nombreRecipiente(g) + ' de ', h('b', null, g.ingrediente), ' tiene ahora ', h('b', null, n + ' lotes mezclados'), '.'),
      h('p', { class: 'suave' }, 'Lo recomendado es vaciarlo y limpiarlo antes de volver a llenarlo: si hay un problema con cualquiera de estos lotes, habría que retirar todo lo producido mientras estuvo en el recipiente.')),
    [{ texto: 'Entendido', clase: 'principal', valor: true }], true);
}


function reducirFoto(archivo) {   // achica la foto a 1600 px para no llenar el disco
  return new Promise((resolver) => {
    const img = new Image();
    img.onload = () => {
      const escala = Math.min(1, 1600 / Math.max(img.width, img.height));
      const lienzo = h('canvas', { width: Math.round(img.width * escala), height: Math.round(img.height * escala) });
      lienzo.getContext('2d').drawImage(img, 0, 0, lienzo.width, lienzo.height);
      lienzo.toBlob((b) => resolver(b), 'image/jpeg', 0.8);
    };
    img.onerror = () => resolver(null);
    img.src = URL.createObjectURL(archivo);
  });
}

// Foto: abre la cámara (la del celular, o la de la computadora si tiene) o permite subir un archivo.
function campoFoto() {
  let archivo = null;
  let flujo = null;
  const subir = h('input', { type: 'file', accept: 'image/*', hidden: true });
  const camaraCel = h('input', { type: 'file', accept: 'image/*', capture: 'environment', hidden: true });
  const video = h('video', { autoplay: true, playsinline: true, muted: true, class: 'camara-video' });
  const zonaCamara = h('div', { class: 'camara', hidden: true });
  const vista = h('div', { class: 'foto-elegida', hidden: true });
  const botones = h('div', { class: 'fila-botones', style: 'margin-top:.3rem' });
  const apagar = () => { if (flujo) flujo.getTracks().forEach((t) => t.stop()); flujo = null; zonaCamara.hidden = true; botones.hidden = false; };
  const poner = (blob) => {
    archivo = blob;
    apagar();
    vista.replaceChildren(h('img', { src: URL.createObjectURL(blob), alt: 'Foto del empaque' }),
      h('button', { class: 'boton chico', type: 'button', onclick: () => { archivo = null; vista.hidden = true; } }, 'Quitar foto'));
    vista.hidden = false;
  };
  subir.addEventListener('change', () => { if (subir.files[0]) poner(subir.files[0]); subir.value = ''; });
  camaraCel.addEventListener('change', () => { if (camaraCel.files[0]) poner(camaraCel.files[0]); camaraCel.value = ''; });
  const abrirCamara = async () => {
    // En la computadora (localhost) se usa la cámara en vivo; en el celular, la cámara del teléfono.
    const movil = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
    if (movil || !window.isSecureContext || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { camaraCel.click(); return; }
    try {
      flujo = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
    } catch (e) {
      avisar('No se pudo abrir la cámara. Puedes subir la foto como archivo.');
      return;
    }
    video.srcObject = flujo;
    zonaCamara.hidden = false; botones.hidden = true;
  };
  const capturar = () => {
    const lienzo = h('canvas', { width: video.videoWidth || 1280, height: video.videoHeight || 720 });
    lienzo.getContext('2d').drawImage(video, 0, 0, lienzo.width, lienzo.height);
    lienzo.toBlob((b) => { if (b) poner(b); }, 'image/jpeg', 0.85);
  };
  zonaCamara.append(video, h('div', { class: 'fila-botones' },
    h('button', { class: 'boton principal', type: 'button', onclick: capturar }, 'Tomar la foto'),
    h('button', { class: 'boton', type: 'button', onclick: apagar }, 'Cerrar cámara')));
  botones.append(
    h('button', { class: 'boton', type: 'button', onclick: abrirCamara }, '📷 Tomar foto'),
    h('button', { class: 'boton', type: 'button', onclick: () => subir.click() }, 'Subir archivo'));
  return { el: h('div', null, botones, zonaCamara, vista, subir, camaraCel), get archivo() { return archivo; }, apagar,
    limpiar: () => { archivo = null; vista.hidden = true; apagar(); } };
}
// Ver una foto guardada (etiqueta del lote, requisición).
function verFoto(url, titulo) {
  return modal(titulo, h('div', { class: 'ver-foto' }, h('img', { src: url, alt: titulo }),
    h('a', { class: 'boton chico', href: url, target: '_blank', rel: 'noopener' }, 'Abrir en grande')), [{ texto: 'Cerrar', valor: null }]);
}
// Sube las fotos elegidas de una recepción.
async function subirFotosRecepcion(id, fotos) {
  for (const [tipo, f] of Object.entries(fotos)) {
    if (!f.archivo) continue;
    const resp = await fetch('/api/recepciones/' + id + '/foto?tipo=' + tipo + '&persona=' + encodeURIComponent(quien()), { method: 'POST', body: f.archivo });
    if (!resp.ok) { const r = await resp.json().catch(() => ({})); throw new Error('La recepción se guardó, pero la foto no: ' + (r.error || 'error')); }
  }
}

// "No encuentro el lote en la lista"
async function loteManual(ing, destino) {
  const codigo = h('input', { type: 'text', autocapitalize: 'characters', autocomplete: 'off' });
  const nota = h('input', { type: 'text', placeholder: 'Opcional' });
  const foto = campoFoto();
  let cant = '';
  const { campo: campoCant, eq } = campoEmpaques(ing, '', (v) => { cant = v; }, 'ej. 2');
  const caducidad = h('input', { type: 'date' });
  const datos = await modal('Lote que no está en la lista',
    h('div', null,
      h('div', { class: 'nota' }, 'Úsalo solo si de verdad no aparece. Queda «por revisar» hasta que bodega principal registre la recepción con el mismo código.'),
      h('p', null, 'Ingrediente: ', h('b', null, ing.nombre)),
      h('label', null, 'Código de lote, exactamente como está en el empaque'), codigo,
      h('label', null, ing.empaque ? cuantos(ing.empaque) + ' entran ahora? (solo lo que tienes aquí)' : '¿Cuánto entra ahora? (solo lo que tienes aquí)'), campoCant, eq,
      h('label', null, 'Fecha de caducidad (la del empaque)'), caducidad,
      h('label', null, 'Foto del empaque donde se ve el lote'), foto.el,
      h('label', null, 'Comentario'), nota),
    [{ texto: 'Registrar', clase: 'principal', accion: () => api('/lotes/manual', { ingrediente_id: ing.id, lote: codigo.value.trim(),
      nota: nota.value.trim(), destino, cantidad: cant, caducidad: caducidad.value }) },
      { texto: 'Cancelar', valor: null }]);
  foto.apagar();
  if (!datos) return false;
  const r = datos;
  if (foto.archivo) {
    const blob = await reducirFoto(foto.archivo);
    if (blob) await intentar(async () => {
      const resp = await fetch('/api/lotes/' + r.lote_id + '/foto', { method: 'POST', headers: { 'Content-Type': 'image/jpeg' }, body: blob });
      if (!resp.ok) throw new Error('El lote se registró, pero la foto no se pudo guardar.');
    });
  }
  avisar('Lote registrado y marcado por revisar. Se avisó a ' + (destino === 'cocina' ? 'bodega secundaria.' : 'bodega principal.'), 'bien');
  revisarAvisos(true);
  await preguntarAnteriores(r.anteriores);
  return true;
}

function encabezado(titulo, subtitulo) {
  return [h('a', { class: 'volver no-imprimir', href: '#/' }, h('span', { class: 'flecha-volver', 'aria-hidden': 'true' }, '←'), 'Volver al inicio'), h('h1', null, titulo),
    subtitulo ? h('p', { class: 'suave' }, subtitulo) : null];
}
// Pone en cada celda el titulo de su columna, para mostrar las tablas como fichas en el celular.
function rotular(raiz) {
  raiz.querySelectorAll('table').forEach((t) => {
    const titulos = [...t.querySelectorAll(':scope > thead th')].map((th) => th.textContent);
    t.querySelectorAll(':scope > tbody > tr').forEach((tr) => {
      const celdas = [...tr.children];
      if (celdas.length === titulos.length) celdas.forEach((td, i) => { if (titulos[i]) td.setAttribute('data-t', titulos[i]); });
    });
  });
}
function llenar(el, ...nodos) {
  el.replaceChildren();
  nodos.flat(Infinity).forEach((n) => { if (n) el.append(n.nodeType ? n : document.createTextNode(String(n))); });
  rotular(el);
}
function pintar(...nodos) { llenar($vista, ...nodos); window.scrollTo(0, 0); }

// ------------------------------------------------------------------ inicio
// Areas de trabajo. Cada dispositivo recuerda su area; mas adelante cada area tendra su contrasena.
const AREAS = {
  bodega: { nombre: 'Bodega principal', texto: 'Recepción de proveedores e inventario', secciones: ['bodega', 'inventario'] },
  produccion: { nombre: 'Producción', texto: 'Bodega secundaria, hojas de producción, etiquetas, despachos, consultas y administración',
    secciones: ['secundaria', 'hojas', 'etiquetas', 'despachos', 'consulta', 'admin'] },
  cocina: { nombre: 'Cocina', texto: 'Abrir lotes y avisar qué pan empieza y termina', secciones: ['cocina', 'produccion'] },
};
let areaMemoria = '';
function area() {
  let a = areaMemoria;
  try { a = localStorage.getItem('trazapan_area') || areaMemoria; } catch (e) { /* sin almacenamiento */ }
  return AREAS[a] ? a : '';
}
function fijarArea(a) {
  areaMemoria = a;
  try { if (a) localStorage.setItem('trazapan_area', a); else localStorage.removeItem('trazapan_area'); } catch (e) { /* queda en memoria */ }
  pintarArea();
}
const $area = h('button', { class: 'chip-persona', type: 'button', onclick: () => cambiarArea() });
// Botón de volver en la pantalla del área: regresa a elegir el área.
function botonCambiarArea() {
  return h('button', { class: 'volver', type: 'button', onclick: () => cambiarArea() },
    h('span', { class: 'flecha-volver', 'aria-hidden': 'true' }, '←'), 'Cambiar de área');
}
// Cambiar de área: si hay contraseñas, se cierra la sesión para que la otra área escriba la suya.
async function cambiarArea() {
  if (sesion.requiere) { try { await fetch('/api/salir', { method: 'POST' }); } catch (e) { /* sin conexión */ } sesion.area = null; }
  fijarArea(''); location.hash = '#/'; navegar();
}
$quien.before($area);
function pintarArea() { $area.textContent = area() ? AREAS[area()].nombre + ' ⇄' : ''; $area.hidden = !area(); revisarAvisos(); }

// Avisos para bodega principal y producción (por ejemplo, un lote anotado a mano). Se revisan cada 30 segundos.
const AREAS_CON_AVISOS = ['bodega', 'produccion'];
let avisosPendientes = [];
let avisosVistos = null;
const $campana = h('button', { class: 'chip-persona chip-aviso', type: 'button', hidden: true, onclick: () => verAvisos() });
$quien.before($campana);
async function revisarAvisos(silencio) {   // silencio: no mostrar como nuevo lo que acaba de crear este dispositivo
  if (!AREAS_CON_AVISOS.includes(area()) || necesitaClave()) { avisosPendientes = []; $campana.hidden = true; return; }
  let lista;
  try { lista = await api('/avisos?area=' + area()); } catch (e) { return; }
  avisosPendientes = lista;
  $campana.textContent = '🔔 ' + lista.length;
  $campana.hidden = !lista.length;
  const nuevos = lista.filter((a) => avisosVistos && !avisosVistos.has(a.id));
  if (nuevos.length && silencio !== true) avisar('Nuevo aviso: ' + nuevos[0].texto);
  avisosVistos = new Set(lista.map((a) => a.id));
  const caja = document.getElementById('avisos-inicio');
  if (caja) pintarAvisosInicio(caja);
}
setInterval(revisarAvisos, 30000);
function pintarAvisosInicio(caja) {
  const n = avisosPendientes.length;
  caja.replaceChildren(...(n ? [h('button', { class: 'nota rojo aviso-inicio', type: 'button', onclick: () => verAvisos() },
    h('b', null, '🔔 ' + (n === 1 ? '1 aviso por revisar' : n + ' avisos por revisar')), h('div', { class: 'chico' }, avisosPendientes[0].texto))] : []));
}
// Texto del aviso con el código de lote y el nombre del ingrediente en negrilla
function textoAviso(a) {
  const esc = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const terminos = [a.ingrediente, a.lote].filter(Boolean).sort((x, y) => y.length - x.length);
  if (!terminos.length) return a.texto;
  const re = new RegExp('(?<![\\w-])(' + terminos.map(esc).join('|') + ')(?![\\w-])', 'g');
  return a.texto.split(re).map((parte, i) => (i % 2 ? h('b', null, parte) : parte));
}

async function verAvisos() {
  const cont = h('div', null);
  const dibujar = () => {
    cont.replaceChildren(...(avisosPendientes.length ? avisosPendientes.map((a) => {
      const resp = h('input', { type: 'text', placeholder: 'Qué pasó (opcional)' });
      return h('div', { class: 'tarjeta', style: 'margin:.6rem 0' },
        h('p', { style: 'margin-top:0' }, textoAviso(a)),
        h('div', { class: 'chico suave' }, hora(a.creado_en)),
        a.foto ? h('p', null, h('a', { href: '/fotos/' + a.foto, target: '_blank' }, 'Ver foto del empaque')) : null,
        a.registrar_recepcion ? [
          h('p', { style: 'margin:.6rem 0 .3rem' }, h('b', null, '¿Hay más de este lote en bodega principal?')),
          h('div', { class: 'acciones-aviso' },
            h('button', { class: 'boton principal', type: 'button', onclick: () => intentar(async () => {
              if (await registrarDesdeAviso(a, true)) { await revisarAvisos(); dibujar(); } }) }, 'Sí, hay más'),
            h('button', { class: 'boton', type: 'button', onclick: () => intentar(async () => {
              if (await registrarDesdeAviso(a, false)) { await revisarAvisos(); dibujar(); } }) }, 'No, solo llegó eso'))]
          : a.registrar_entrada ? h('button', { class: 'boton principal', type: 'button', style: 'margin-top:.6rem;width:100%', onclick: () => intentar(async () => {
            if (await registrarEntradaAviso(a)) { await revisarAvisos(); dibujar(); } }) }, 'Registrar lo que entró')
          : [resp,
            h('button', { class: 'boton ok', type: 'button', style: 'margin-top:.5rem', onclick: () => intentar(async () => {
              await api('/avisos/' + a.id + '/revisado', { comentario: resp.value.trim() });
              avisar('Aviso marcado como revisado', 'bien');
              await revisarAvisos();
              dibujar();
            }) }, 'Ya lo revisé')]);
    }) : [h('p', { class: 'suave' }, 'No hay avisos pendientes.')]));
  };
  await revisarAvisos();
  dibujar();
  await modal('Avisos de ' + AREAS[area()].nombre.toLowerCase(), cont, [{ texto: 'Cerrar', valor: null }]);
}

async function vistaInicio() {
  if (!area()) {
    pintar(h('div', { class: 'nombre-app nombre-inicio' }, 'Traza', h('b', null, 'Pan'), 'USFQ'),
      h('h1', null, 'Hola, Bienvenido a TrazaPanUSFQ!'),
      h('p', { class: 'suave' }, '¿En qué área estás?'),
      h('div', { class: 'mosaicos areas' }, Object.entries(AREAS).map(([id, a]) => h('button', { class: 'mosaico', type: 'button',
        onclick: () => { fijarArea(id); navegar(); } }, h('b', null, a.nombre), h('small', null, a.texto)))));
    return;
  }
  const e = await api('/estado');
  const pendientes = e.alias_por_asignar + e.lotes_revisar;
  const mosaico = (ruta, titulo, texto, numero, clase) => h('a', { class: 'mosaico', href: ruta },
    numero ? h('span', { class: 'num ' + (clase || '') }, numero) : null, h('b', null, titulo), h('small', null, texto));
  const SECCIONES = {
    bodega: () => mosaico('#/bodega', 'Bodega principal', 'Registrar lo que llega de los proveedores', e.recepciones_hoy ? e.recepciones_hoy + ' hoy' : 0, 'verde'),
    inventario: () => {
      const inv = e.inventario_principal;
      const toca = !inv || inv.dias >= e.dias_inventario;
      return mosaico('#/inventario', 'Inventario', 'Revisar los lotes de la bodega y la caducidad',
        !inv ? 'Sin inventario' : inv.dias === 0 ? 'Hecho hoy' : 'Hace ' + inv.dias + ' día' + (inv.dias === 1 ? '' : 's'), toca ? 'amarillo' : 'verde');
    },
    secundaria: () => mosaico('#/secundaria', 'Bodega secundaria', 'Registrar los lotes que llegan desde bodega principal'),
    cocina: () => mosaico('#/cocina', 'Cocina', 'Abrir un lote nuevo o avisar que se terminó', e.abiertos, 'verde'),
    produccion: () => mosaico('#/produccion', 'Producción', 'Avisar qué pan empieza y cuándo termina', e.en_curso ? e.en_curso + ' en curso' : 0),
    hojas: () => mosaico('#/hojas', 'Hojas de producción', 'Ver lo que se está produciendo y completar las hojas',
      e.por_completar ? e.por_completar + ' por completar' : 0, 'rojo'),
    etiquetas: () => mosaico('#/etiquetas', 'Etiquetas', 'Lotes del día para imprimir en la Zebra'),
    despachos: () => mosaico('#/despachos', 'Despachos', 'Registrar a dónde sale cada lote de pan',
      e.congelador ? e.congelador + ' en congelador' : 0, 'verde'),
    consulta: () => mosaico('#/consulta', 'Consultar', 'Rastrear un lote de pan o de ingrediente'),
    admin: () => mosaico('#/admin', 'Administrar', 'Importar Excel, pendientes, recetas y personas', pendientes, 'rojo'),
  };
  const a = AREAS[area()];
  pintar(
    h('div', { class: 'nombre-app nombre-inicio' }, 'Traza', h('b', null, 'Pan'), 'USFQ'),
    botonCambiarArea(),
    h('h1', null, 'Hola, Bienvenido a TrazaPanUSFQ!'),
    h('p', { class: 'suave' }, 'Área: ', h('b', null, a.nombre)),
    AREAS_CON_AVISOS.includes(area()) ? h('div', { id: 'avisos-inicio' }) : null,
    h('div', { class: 'mosaicos' }, a.secciones.map((sec) => SECCIONES[sec]())));
  const caja = document.getElementById('avisos-inicio');
  if (caja) { pintarAvisosInicio(caja); revisarAvisos(); }
}

// ------------------------------------------------------------------ bodega principal
const normal = (t) => String(t || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toUpperCase()
  .replace(/[^A-Z0-9]+/g, ' ').replace(/(?<=[A-Z])(?=[0-9])|(?<=[0-9])(?=[A-Z])/g, ' ').replace(/\s+/g, ' ').trim();
const iniciales = (nombre) => String(nombre || '').trim().split(/\s+/).filter(Boolean).map((p) => p[0]).join('').toUpperCase().slice(0, 3);
// Detecta celdas con varios lotes juntos ("284907 - 287803", "5294681/ 5306721", "L#28 Y L#30").
const variosLotes = (t) => /\s[-/&]\s*\S|\S\s*[/&]\s|\s[yY]\s|[,;]/.test(t || '');

// Deja solo dígitos y un separador decimal (punto o coma).
// Paginador: muestra la lista de N en N con «← Más recientes · 1–5 de 23 · Más antiguos →» (nunca todo de golpe).
function paginador(total, pagina, porPagina, alCambiar, nombre) {
  const paginas = Math.max(1, Math.ceil(total / porPagina));
  if (paginas <= 1) return null;
  const desde = (pagina - 1) * porPagina;
  return h('div', { class: 'paginador' },
    h('button', { class: 'boton chico', type: 'button', disabled: pagina <= 1, onclick: () => alCambiar(pagina - 1) }, '← Más recientes'),
    h('span', null, (nombre || 'Registros') + ' ' + (desde + 1) + '–' + Math.min(desde + porPagina, total) + ' de ' + total),
    h('button', { class: 'boton chico', type: 'button', disabled: pagina >= paginas, onclick: () => alCambiar(pagina + 1) }, 'Más antiguos →'));
}
const trozo = (lista, n, porPagina) => lista.slice((n - 1) * porPagina, n * porPagina);

const soloNumero = (t) => {
  const limpio = String(t).replace(/[^0-9.,]/g, '');
  const i = limpio.search(/[.,]/);
  return i < 0 ? limpio : limpio.slice(0, i + 1) + limpio.slice(i + 1).replace(/[.,]/g, '');
};

function paginaHorizontal(si) {
  const previa = document.getElementById('pagina-horizontal');
  if (previa) previa.remove();
  if (si) document.head.append(h('style', { id: 'pagina-horizontal' }, '@page { size: A4 landscape; margin: 10mm; }'));
}

function segmento(opciones, inicial, alCambiar) {
  let valor = inicial || '';
  const el = h('div', { class: 'segmento' });
  const dibujar = () => el.replaceChildren(...opciones.map(([v, texto, clase]) => h('button', {
    type: 'button', class: valor === v ? 'activo ' + (clase || '') : '',
    onclick: () => { valor = v; dibujar(); if (alCambiar) alCambiar(v); } }, texto)));
  dibujar();
  return { el, get valor() { return valor; }, set valor(v) { valor = v || ''; dibujar(); } };
}

// Foto de un producto (sin fondo). Si no hay foto, un círculo con la inicial.
function fotoProducto(url, nombre, clase) {
  return url ? h('img', { class: 'foto-prod ' + (clase || ''), src: url, alt: '', loading: 'lazy' })
    : h('span', { class: 'foto-prod sin-foto ' + (clase || '') }, String(nombre || '?').trim().charAt(0));
}

// Campo que solo acepta valores de una lista fija: se escribe una parte para filtrar y se elige.
// opciones() devuelve la lista actual; texto(o) el nombre que se muestra.
function listaFija({ opciones, texto, placeholder, vacio, aviso, alElegir, inicial, alAgregar, foto }) {
  const input = h('input', { type: 'text', autocomplete: 'off', spellcheck: 'false', placeholder: placeholder || '' });
  // Con fotos, la lista se muestra como cuadrícula de productos y el elegido lleva su foto en el campo.
  const lista = h('div', { class: 'combo-lista' + (foto ? ' rejilla-fotos' : ''), hidden: true });
  const miniatura = h('span', { class: 'combo-foto', hidden: true });
  const pintarMiniatura = () => {
    if (!foto) return;
    miniatura.replaceChildren(...(elegido ? [fotoProducto(foto(elegido), texto(elegido))] : []));
    miniatura.hidden = !elegido; input.classList.toggle('con-foto', !!elegido);
  };
  const avisoEl = h('div', { class: 'chico', style: 'color:var(--alerta)' });
  let elegido = null;
  let activo = -1;
  const coincidencias = () => {
    const n = elegido !== null ? '' : normal(input.value);    // con un valor ya elegido se muestra toda la lista
    return opciones().filter((o) => !n || normal(texto(o)).includes(n));
  };
  const mostrar = () => {
    const ops = coincidencias();
    activo = Math.min(activo, ops.length - 1);
    lista.replaceChildren(...(ops.length ? ops.map((o, i) => h('button', { type: 'button', class: i === activo ? 'activo' : '',
      onpointerdown: (e) => e.preventDefault(), onclick: () => elegir(o) }, foto ? [fotoProducto(foto(o), texto(o)), h('span', null, texto(o))] : texto(o)))
      : [h('div', { class: 'combo-vacio' }, vacio), alAgregar ? h('button', { type: 'button', class: 'combo-agregar',
        onpointerdown: (e) => e.preventDefault(), onclick: () => { lista.hidden = true; alAgregar(); } }, '+ Agregar «' + input.value.trim().toUpperCase() + '» a la lista') : null]));
    lista.hidden = false;
  };
  const elegir = (o) => {
    elegido = o;
    input.value = o === null ? '' : texto(o);
    lista.hidden = true;
    avisoEl.textContent = '';
    pintarMiniatura();
    if (o !== null && alElegir) alElegir(o);
  };
  input.addEventListener('input', () => { elegido = null; activo = -1; pintarMiniatura(); mostrar(); });
  input.addEventListener('focus', mostrar);
  input.addEventListener('keydown', (e) => {
    const ops = coincidencias();
    if (e.key === 'ArrowDown') { activo = Math.min(activo + 1, ops.length - 1); mostrar(); e.preventDefault(); }
    else if (e.key === 'ArrowUp') { activo = Math.max(activo - 1, 0); mostrar(); e.preventDefault(); }
    else if (e.key === 'Enter') { e.preventDefault(); const o = ops[activo] || (ops.length === 1 ? ops[0] : null); if (o) elegir(o); }
    else if (e.key === 'Escape') lista.hidden = true;
  });
  input.addEventListener('blur', () => {
    lista.hidden = true;
    if (elegido !== null || !input.value.trim()) return;
    const exacto = opciones().find((o) => normal(texto(o)) === normal(input.value));
    if (exacto) elegir(exacto); else avisoEl.textContent = aviso;
  });
  if (inicial) { elegido = inicial; input.value = texto(inicial); }
  pintarMiniatura();
  return {
    input, aviso: avisoEl,
    el: h('div', { class: 'combo' }, miniatura, input, lista),
    get elegido() { return elegido; },
    elegir, limpiar: () => { elegido = null; input.value = ''; avisoEl.textContent = ''; pintarMiniatura(); },
  };
}

// Formulario con los campos del registro BPM-SFFS-RRMP001. Se usa para registrar y para corregir.
function formRecepcion(cat, ini, editar) {
  ini = ini || {};
  const c = {
    fecha: h('input', { type: 'date', value: ini.fecha_ingreso || cat.hoy }),
    producto: editar ? h('input', { type: 'text', value: ini.producto, disabled: true }) : null,
    proveedor: h('input', { type: 'text', autocomplete: 'off', value: ini.proveedor || '' }),
    lote: h('input', { type: 'text', class: 'mono', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false',
      value: ini.lote_texto || '', disabled: !!(editar && ini.en_uso) }),
    cantidad: h('input', { type: 'text', inputmode: 'decimal', autocomplete: 'off', value: ini.cantidad || '', placeholder: 'Solo números',
      oninput: (e) => { const v = soloNumero(e.target.value); if (v !== e.target.value) e.target.value = v; } }),
    caducidad: h('input', { type: 'date', value: /^\d{4}-\d{2}-\d{2}$/.test(ini.caducidad || '') ? ini.caducidad : '' }),
    aprobado: h('input', { type: 'text', autocomplete: 'off', value: editar ? (ini.aprobado_por || '') : '' }),
    observaciones: h('input', { type: 'text', value: ini.observaciones || '' }),
  };
  const algoMal = () => apariencia.valor === 'NO OK' || transporte.valor === 'NO OK';
  const revisar = () => { cajaDecision.hidden = !algoMal(); };
  const apariencia = segmento([['OK', 'OK', 'verde'], ['NO OK', 'NO OK', 'rojo']], ini.apariencia, revisar);
  const transporte = segmento([['OK', 'OK', 'verde'], ['NO OK', 'NO OK', 'rojo']], ini.transporte, revisar);
  const decision = segmento([['aceptado', 'Se recibió con observación'], ['rechazado', 'Se rechazó', 'rojo']],
    ini.rechazado ? 'rechazado' : (ini.apariencia === 'NO OK' || ini.transporte === 'NO OK') ? 'aceptado' : '');
  const cajaDecision = h('div', { class: 'nota rojo' }, h('b', null, 'Algo no está OK. ¿Qué se hizo con el producto?'),
    h('div', { style: 'margin-top:.4rem' }, decision.el),
    h('div', { class: 'chico', style: 'margin-top:.3rem' }, 'Si se rechazó, el lote queda en el registro pero no pasa a la panadería. Escribe la observación.'));
  revisar();

  // Unidad: solo de la lista de unidades (al corregir se conserva la que ya tenía, aunque no esté en la lista).
  const unidad = listaFija({
    opciones: () => cat.unidades, texto: (u) => u, placeholder: 'Escribe o elige',
    vacio: 'No está en la lista.', aviso: 'Elige una unidad de la lista (o agrégala como unidad nueva).',
    inicial: ini.unidad || null, alAgregar: () => nuevaUnidad(),
  });
  // Producto: solo se puede elegir de la lista de bodega principal (se escribe una parte para filtrar).
  const prod = editar ? null : listaFija({
    opciones: () => cat.productos, texto: (p) => p.producto, foto: (p) => p.foto, placeholder: 'Toca para ver los productos o escribe parte del nombre',
    vacio: 'No está en la lista.', aviso: 'Elige un producto de la lista (o agrégalo como producto nuevo).', alAgregar: () => nuevoProducto(),
    alElegir: (p) => {
      ponerEmpaque(p);
      if (!p.empaque && !unidad.elegido && p.unidad && cat.unidades.includes(p.unidad)) unidad.elegir(p.unidad);
    },
  });
  if (prod) c.producto = prod.input;
  // Con empaque definido (1 paquete = 50 kg) la unidad es fija y la cantidad se cuenta en empaques enteros.
  let emp = null;
  const unidadFija = h('div', { class: 'unidad-fija', hidden: true });
  const cajaUnidadLibre = h('div', null, unidad.el, unidad.aviso,
    h('button', { class: 'boton-enlace', type: 'button', onclick: () => nuevaUnidad() }, '+ Agregar unidad nueva'));
  const eqCantidad = h('div', { class: 'chico equivale' });
  const pintarEq = () => { eqCantidad.textContent = emp && c.cantidad.value ? '= ' + equivalencia(c.cantidad.value, emp) : ''; };
  // La unidad nunca se escribe: sale del empaque del producto (Administrar > Productos de bodega > Empaque).
  const ponerEmpaque = (p) => {
    emp = p && p.empaque ? p : null;
    unidadFija.hidden = false;
    unidadFija.classList.toggle('vacia', !emp);
    unidadFija.replaceChildren(...(emp ? [h('b', null, nombreEmpaque(emp.empaque, 2).toUpperCase()), h('span', { class: 'chico suave' }, textoEmpaque(emp))]
      : editar && ini.unidad ? [h('b', null, ini.unidad)]
        : p ? [h('span', { class: 'chico', style: 'color:var(--alerta)' }, 'Este producto no tiene empaque. Defínelo en Administrar > Productos de bodega.')]
          : [h('span', { class: 'chico suave' }, 'Se pone sola al elegir el producto')]));
    c.cantidad.placeholder = emp ? 'Número de ' + nombreEmpaque(emp.empaque, 2) : 'Solo números';
    c.cantidad.inputMode = emp ? 'numeric' : 'decimal';
    pintarEq();
  };
  c.cantidad.addEventListener('input', () => {
    if (emp) { const v = c.cantidad.value.replace(/\D/g, ''); if (v !== c.cantidad.value) c.cantidad.value = v; }
    pintarEq();
  });
  const nuevoProducto = () => intentar(async () => {
    const campo = campoMayusculas(prod.elegido ? '' : c.producto.value.trim());
    const foto = campoFoto();
    // Cuánto trae un empaque: así todos registran en paquetes y la app calcula los kg, litros o unidades.
    const empaque = h('select', null, cat.empaques.map((e) => h('option', { value: e }, nombreEmpaque(e, 1))));
    const contenido = h('input', { type: 'text', inputmode: 'decimal', placeholder: 'ej. 1', style: 'width:6rem',
      oninput: (e) => { const v = soloNumero(e.target.value); if (v !== e.target.value) e.target.value = v; } });
    const unidadC = h('select', null, cat.unidades_contenido.map((u) => h('option', { value: u }, u)));
    setTimeout(() => campo.focus(), 50);
    const datos = await modal('Agregar producto nuevo a la lista', h('div', null,
      h('label', null, 'Nombre del producto (por ejemplo: CHOCOLATE COBERTURA (1KG))'), campo,
      h('label', null, '¿Cuánto trae cada empaque?'),
      h('div', { class: 'en-linea empaque-linea' }, h('b', null, '1'), empaque, h('b', null, '='), contenido, unidadC),
      h('div', { class: 'chico suave' }, 'Ejemplo: 1 botella = 500 ml · 1 paquete = 50 kg · 1 cubeta = 30 huevos'),
      h('label', null, 'Foto del producto (opcional)'), foto.el),
    [{ texto: 'Agregar', clase: 'principal', accion: () => {
      const d = { nombre: campo.value.trim().toUpperCase(), empaque: empaque.value, contenido: contenido.value.trim(), unidad_contenido: unidadC.value };
      if (!d.nombre) throw new Error('Escribe el nombre del producto.');
      if (!d.contenido) throw new Error('Escribe cuánto trae cada ' + nombreEmpaque(d.empaque, 1) + '.');
      return api('/bodega/productos', d);
    } }, { texto: 'Cancelar', valor: null }]);
    foto.apagar();
    if (!datos) return;
    const r = datos;
    if (foto.archivo) {
      try { await subirFotoProducto(r.id, foto.archivo); } catch (e) { avisar('El producto se agregó, pero la foto no se pudo guardar: ' + e.message); }
    }
    Object.assign(cat, await api('/bodega/catalogo'));
    const p = cat.productos.find((x) => x.producto === r.producto);
    if (p) prod.elegir(p);
    avisar('Agregado a la lista: ' + r.producto, 'bien');
  });
  const nuevaUnidad = () => intentar(async () => {
    const nombre = await pedirTexto('Agregar unidad nueva a la lista', 'Nombre de la unidad, como debe quedar en la lista (por ejemplo: GALON)',
      unidad.elegido ? '' : unidad.input.value.trim().toUpperCase(), { mayusculas: true });
    if (!nombre) return;
    const r = await api('/bodega/unidades', { nombre });
    cat.unidades = r.unidades;
    unidad.elegir(r.unidad);
    avisar('Unidad agregada a la lista: ' + r.unidad, 'bien');
  });

  const campo = (etiqueta, nodo, clase) => h('div', { class: 'campo ' + (clase || '') }, h('label', null, etiqueta), nodo);
  const fotos = { lote: campoFoto(), requisicion: campoFoto() };
  const el = h('div', { class: 'form-recepcion' },
    campo('Fecha de ingreso', c.fecha),
    campo('Producto', editar ? c.producto : [prod.el, prod.aviso,
      h('button', { class: 'boton-enlace', type: 'button', onclick: nuevoProducto }, '+ Agregar producto nuevo')], 'doble'),
    campo('Proveedor', c.proveedor, 'ancho-movil'),
    campo('Lote', [c.lote, editar && ini.en_uso ? h('div', { class: 'chico suave' }, 'Ya se usó en panadería: no se puede cambiar.') : null], 'doble'),
    campo('Cantidad', [c.cantidad, eqCantidad]),
    campo('Unidad de medida', unidadFija),
    campo('Apariencia visual del producto', apariencia.el), campo('Condiciones de transporte', transporte.el),
    campo('Aprobado por', c.aprobado), campo('Fecha de caducidad', c.caducidad),
    h('div', { class: 'campo completo' }, cajaDecision),
    campo('Observaciones', c.observaciones, 'completo'),
    h('div', { class: 'campo completo fotos-recepcion' },
      h('div', null, h('label', null, 'Foto del lote (etiqueta)'),
        fotos.lote.el,
        editar && ini.foto_lote ? h('button', { class: 'boton-enlace', type: 'button', onclick: () => verFoto('/fotos/' + ini.foto_lote, 'Lote ' + ini.lote_texto) }, 'Ver la foto guardada (puedes reemplazarla)') : null),
      h('div', null, h('label', null, 'Foto de la requisición'),
        fotos.requisicion.el,
        editar && ini.foto_requisicion ? h('button', { class: 'boton-enlace', type: 'button', onclick: () => verFoto('/fotos/' + ini.foto_requisicion, 'Requisición') }, 'Ver la foto guardada (puedes reemplazarla)') : null)));

  ponerEmpaque(editar ? cat.productos.find((p) => p.producto === ini.producto) || null : null);
  const leer = () => ({
    fecha: c.fecha.value, producto: editar ? ini.producto : (prod.elegido ? prod.elegido.producto : ''), proveedor: c.proveedor.value.trim(), lote: c.lote.value.trim(),
    cantidad: c.cantidad.value.trim(), unidad: emp ? emp.empaque : (editar ? ini.unidad || '' : ''), caducidad: c.caducidad.value,
    apariencia: apariencia.valor, transporte: transporte.valor, decision: algoMal() ? decision.valor : '',
    aprobado_por: c.aprobado.value.trim(), observaciones: c.observaciones.value.trim(),
  });
  const limpiar = (mismoProducto) => {
    c.lote.value = ''; c.cantidad.value = ''; c.caducidad.value = ''; c.observaciones.value = ''; decision.valor = '';
    fotos.lote.limpiar();
    if (!mismoProducto) fotos.requisicion.limpiar();      // otro lote de la misma entrega: la requisición es la misma
    if (!mismoProducto) {
      prod.limpiar(); unidad.limpiar(); c.proveedor.value = ''; apariencia.valor = ''; transporte.valor = '';
      ponerEmpaque(null);
    }
    pintarEq();
    revisar();
    if (mismoProducto) c.lote.focus();
  };
  const elegirProducto = (nombre) => { const p = cat.productos.find((x) => x.producto === nombre); if (p && prod) prod.elegir(p); return p; };
  return { el, leer, limpiar, fotos, campos: c, elegirProducto };
}

// Lote que cocina anotó a mano: bodega secundaria registra cuánto entró (y la caducidad).
async function registrarEntradaAviso(a) {
  const e = a.empaque ? a : null;
  const ya = Math.max(Number(a.trasladado) || 0, Number(a.tomado_cocina) || 0);
  const cuanto = (n) => (e ? cantidadLote(n, e) : num(n));
  let cant = String(ya);
  const { campo, eq } = campoEmpaques(a, cant, (v) => { cant = v; }, e ? 'Número de ' + nombreEmpaque(e.empaque, 2) : 'Solo números');
  const cad = h('input', { type: 'date', value: /^\d{4}-\d{2}-\d{2}$/.test(a.caducidad || '') ? a.caducidad : '' });
  const ok = await modal('Registrar el lote ' + a.lote + ' en bodega secundaria', h('div', null,
    h('div', { class: 'nota' }, 'Cocina ya tomó ', h('b', null, cuanto(a.tomado_cocina)), '. Escribe cuánto entró a bodega secundaria de este lote (si cocina se llevó todo, deja el mismo número).'),
    h('div', { class: 'campo' }, h('label', null, 'Cuánto entró', e ? ' (' + nombreEmpaque(e.empaque, 2) + ')' : ''), campo, eq),
    h('div', { class: 'campo' }, h('label', null, 'Fecha de caducidad'), cad)),
    [{ texto: 'Guardar', clase: 'principal', accion: async () => {
      if (!cant || Number(cant) < ya) throw new Error('No puede ser menos de lo que cocina ya tomó (' + cuanto(ya) + ').');
      if (!cad.value) throw new Error('Falta la fecha de caducidad.');
      await api('/avisos/' + a.id + '/entrada', { cantidad: cant, caducidad: cad.value });
      return true;
    } }, { texto: 'Cancelar', valor: null }]);
  if (!ok) return false;
  avisar(a.tiene_recepcion ? 'Listo. Se avisó a bodega principal.' : 'Listo. Se avisó a bodega principal para que registre la recepción.', 'bien');
  return true;
}

// Desde el aviso de un lote anotado a mano en bodega secundaria: bodega principal registra la recepción.
// hayMas = quedan más en bodega principal (se escribe el total que llegó); si no, el total es lo que ya pasó a secundaria.
async function registrarDesdeAviso(a, hayMas) {
  const cat = await api('/bodega/catalogo');
  const f = formRecepcion(cat, { lote_texto: a.lote, caducidad: a.caducidad, fecha_ingreso: cat.hoy });
  const p = f.elegirProducto(a.producto);
  const ya = Number(a.trasladado) || 0;
  const e = p && p.empaque ? p : null;
  const cuanto = (n) => (e ? cantidadLote(n, e) : num(n));
  f.campos.lote.disabled = true;           // mismo código que anotó bodega secundaria
  const info = h('div', { class: 'nota' });
  const pintar = () => {
    const total = Number(f.campos.cantidad.value);
    info.replaceChildren(...(hayMas
      ? [h('div', null, 'Ya pasaron a bodega secundaria: ', h('b', null, cuanto(ya)), '.'),
        f.campos.cantidad.value ? (total < ya ? h('div', { style: 'color:var(--alerta)' }, 'El total no puede ser menor que lo que ya pasó.')
          : h('div', null, 'Quedan en bodega principal: ', h('b', null, cuanto(total - ya)), '.')) : h('div', { class: 'chico' }, 'Escribe abajo cuántos llegaron en total.')]
      : [h('div', null, 'Llegaron solo los que ya están en bodega secundaria: ', h('b', null, cuanto(ya)), '. En bodega principal no queda nada.')]));
  };
  if (!hayMas) { f.campos.cantidad.value = String(ya); f.campos.cantidad.disabled = true; f.campos.cantidad.dispatchEvent(new Event('input')); }
  f.campos.cantidad.addEventListener('input', pintar);
  pintar();
  const datos = await modal('Registrar la recepción del lote ' + a.lote, h('div', null, info, f.el),
    [{ texto: 'Guardar recepción', clase: 'principal', accion: async () => {
      const d = f.leer();
      if (hayMas && Number(d.cantidad) <= ya) throw new Error('Si hay más en bodega principal, el total tiene que ser mayor que ' + cuanto(ya) + '.');
      return api('/recepciones', Object.assign(d, { aviso_id: a.id }));
    } }, { texto: 'Cancelar', valor: null }]);
  Object.values(f.fotos).forEach((x) => x.apagar());
  if (!datos) return false;
  const r = datos;
  await subirFotosRecepcion(r.id, f.fotos);
  avisar('Recepción registrada · lote ' + r.lote_texto, 'bien');
  return true;
}

// Corregir una recepción de bodega principal (fecha, proveedor, cantidad, caducidad, etc.). El motivo queda en el historial.
async function corregirRecepcion(cat, r) {
  const f = formRecepcion(cat, r, true);
  const motivo = h('input', { type: 'text', placeholder: !r.caducidad ? 'Opcional si solo agregas la caducidad' : 'ej. la cantidad estaba mal escrita' });
  const conFotos = () => Object.values(f.fotos).some((x) => x.archivo);
  const datos = await modal('Corregir recepción', h('div', null, f.el, h('label', null, 'Motivo de la corrección (queda registrado)'), motivo),
    [{ texto: 'Guardar corrección', clase: 'principal', accion: async () => {
      try {
        await api('/recepciones/' + r.id + '/editar', Object.assign(f.leer(), { motivo: motivo.value.trim() }));
      } catch (e) {
        if (!(conFotos() && /No cambiaste nada/.test(e.message))) throw e;      // solo se agregó una foto
      }
      return true;
    } }, { texto: 'Cancelar', valor: null }]);
  Object.values(f.fotos).forEach((x) => x.apagar());
  if (!datos) return false;
  await subirFotosRecepcion(r.id, f.fotos);
  avisar(conFotos() ? 'Guardado (con foto)' : 'Corrección guardada', 'bien');
  return true;
}

// Desde el semáforo: corregir la recepción de un lote (si llegó en varias entregas, se elige cuál).
async function editarIngresoLote(l) {
  const [cat, recs] = await Promise.all([api('/bodega/catalogo'), api('/lotes/' + l.id + '/recepciones')]);
  if (!recs.length) throw new Error('Este lote no tiene una recepción de bodega principal para corregir.');
  let r = recs[0];
  if (recs.length > 1) {
    r = await modal('¿Qué entrega quieres corregir?', h('div', null,
      h('p', { style: 'margin-top:0' }, h('b', null, l.producto_bodega || l.ingrediente), ' | ', h('b', { class: 'mono' }, l.lote),
        ' llegó en ' + recs.length + ' entregas.')),
    recs.map((x) => ({ texto: dia(x.fecha_ingreso) + ' · ' + [x.cantidad, x.unidad].filter(Boolean).join(' ') + (x.caducidad ? ' · cad. ' + dia(x.caducidad) : ' · sin caducidad'), valor: x }))
      .concat([{ texto: 'Cancelar', valor: null }]));
    if (!r) return false;
  }
  return corregirRecepcion(cat, r);
}

async function vistaBodega() {
  const cat = await api('/bodega/catalogo');
  let mes = cat.hoy.slice(0, 7);
  const form = formRecepcion(cat, null, false);
  const registro = h('div', null);
  const tituloMes = h('p', { class: 'solo-imprimir' });
  // El registro del mes se ve de 15 en 15 (al imprimir salen todos).
  const POR_PAGINA = 15;
  let pagina = 1;
  let lista = [];

  const cargar = () => intentar(async () => {
    lista = await api('/recepciones?mes=' + mes);
    pintarRegistro();
  });
  const pintarRegistro = () => {
    const paginas = Math.max(1, Math.ceil(lista.length / POR_PAGINA));
    pagina = Math.min(Math.max(pagina, 1), paginas);
    const desde = (pagina - 1) * POR_PAGINA;
    const ir = (n) => { pagina = n; pintarRegistro(); registro.scrollIntoView({ block: 'start', behavior: 'smooth' }); };
    const paginador = paginas > 1 ? h('div', { class: 'paginador no-imprimir' },
      h('button', { class: 'boton chico', type: 'button', disabled: pagina === 1, onclick: () => ir(pagina - 1) }, '← Anteriores'),
      h('span', null, 'Registros ' + (desde + 1) + '–' + Math.min(desde + POR_PAGINA, lista.length) + ' de ' + lista.length),
      h('button', { class: 'boton chico', type: 'button', disabled: pagina === paginas, onclick: () => ir(pagina + 1) }, 'Siguientes →')) : null;
    const [a, m] = mes.split('-');
    tituloMes.textContent = 'Mes: ' + ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre',
      'octubre', 'noviembre', 'diciembre'][Number(m) - 1] + ' ' + a;
    llenar(registro, lista.length ? h('div', { class: 'tabla-caja' }, h('table', { class: 'registro-bpm' },
      h('thead', null, h('tr', null, ['Fecha ingreso', 'Producto', 'Proveedor', 'Lote', 'Cantidad', 'Unidad de medida',
        'Apariencia visual producto*', 'Condiciones de transporte**', 'Aprobado por', 'Observaciones'].map((t) => h('th', null, t)),
      h('th', { class: 'no-imprimir' }, ''))),
      h('tbody', null, lista.map((r, i) => {
        const fila = filaRecepcion(r);
        if (i < desde || i >= desde + POR_PAGINA) fila.classList.add('fuera-pagina');
        return fila;
      }))))
      : h('p', { class: 'suave' }, 'No hay recepciones registradas en este mes.'), paginador);
  };
  const filaRecepcion = (r) => h('tr', { class: r.anulado ? 'anulada no-imprimir' : '' },
    h('td', null, dia(r.fecha_ingreso)),
    h('td', null, r.producto),
    h('td', null, r.proveedor),
    h('td', { class: 'mono' }, h('b', null, r.lote_texto), r.caducidad ? h('div', { class: 'chico suave' }, 'Cad. ' + dia(r.caducidad)) : null),
    h('td', null, r.cantidad), h('td', null, r.unidad), h('td', null, r.apariencia), h('td', null, r.transporte),
    h('td', null, r.aprobado_por),
    h('td', null, r.anulado ? h('span', { class: 'etq gris' }, 'ANULADA: ' + r.motivo_anulacion) : null,
      r.rechazado ? h('span', { class: 'etq rojo' }, 'RECHAZADO') : null, ' ', r.observaciones,
      r.nota ? h('div', { class: 'chico suave no-imprimir' }, r.nota) : null,
      r.foto_lote || r.foto_requisicion ? h('div', { class: 'fotos-fila no-imprimir' },
        r.foto_lote ? h('button', { class: 'boton-enlace', type: 'button', onclick: () => verFoto('/fotos/' + r.foto_lote, 'Lote ' + r.lote_texto) }, '📷 Lote') : null,
        r.foto_requisicion ? h('button', { class: 'boton-enlace', type: 'button', onclick: () => verFoto('/fotos/' + r.foto_requisicion, 'Requisición · ' + r.producto) }, '📄 Requisición') : null) : null),
    h('td', { class: 'no-imprimir' }, r.anulado ? null : h('div', { style: 'display:flex;gap:.3rem;flex-wrap:wrap' },
      h('button', { class: 'boton chico', type: 'button', onclick: () => corregir(r) }, 'Corregir'),
      h('button', { class: 'boton chico peligro', type: 'button', onclick: () => anular(r) }, 'Anular'))));

  const corregir = (r) => intentar(async () => { if (await corregirRecepcion(cat, r)) cargar(); });
  const anular = (r) => intentar(async () => {
    const motivo = await pedirTexto('Anular: ' + r.producto + ' · ' + r.lote_texto, 'Motivo (queda registrado)');
    if (!motivo) return;
    await api('/recepciones/' + r.id + '/anular', { motivo });
    avisar('Recepción anulada', 'bien');
    cargar();
  });
  const guardar = (mismoProducto) => intentar(async () => {
    const d = form.leer();
    if (variosLotes(d.lote)) {
      const seguir = await modal('¿Es un solo lote?', h('div', null,
        h('p', null, 'El código ', h('b', { class: 'mono' }, d.lote), ' parece tener más de un lote.'),
        h('p', { class: 'suave' }, 'Cada lote va en su propia fila. Si son varios, registra el primero con «Guardar y registrar otro lote del mismo producto».')),
      [{ texto: 'Corregir', clase: 'principal', valor: false }, { texto: 'Es un solo código, guardarlo así', valor: true }]);
      if (!seguir) return;
    }
    const r = await api('/recepciones', d);
    await subirFotosRecepcion(r.id, form.fotos);
    avisar('Guardado: ' + r.producto + ' · lote ' + r.lote_texto, 'bien');
    if (r.aviso) avisar(r.aviso);
    Object.assign(cat, await api('/bodega/catalogo'));
    form.limpiar(mismoProducto);
    cargar();
  });

  const F = cat.formato;
  pintar(encabezado('Bodega principal'),
    h('div', { class: 'tarjeta no-imprimir' }, h('h2', { style: 'margin-top:0' }, 'Nueva recepción'), form.el,
      h('div', { class: 'fila-botones' },
        h('button', { class: 'boton principal', type: 'button', onclick: () => guardar(false) }, 'Guardar recepción'),
        h('button', { class: 'boton', type: 'button', onclick: () => guardar(true) }, 'Guardar y registrar otro lote del mismo producto'))),
    h('div', { class: 'solo-imprimir' }, h('table', { class: 'cab-bpm' }, h('tbody', null,
      h('tr', null, h('td', { rowspan: '3', class: 'logo' }, h('img', { src: '/static/logo.png', alt: F.empresa + ' ' + F.subempresa })),
        h('td', null, F.procedimiento), h('td', null, 'CÓDIGO:'), h('td', null, F.codigo)),
      h('tr', null, h('td', { rowspan: '2' }, F.registro), h('td', null, 'VERSIÓN:'), h('td', null, F.version)),
      h('tr', null, h('td', null, 'FECHA ELAB:'), h('td', null, F.fecha_elab))))),
    h('div', { class: 'en-linea no-imprimir', style: 'margin-top:1.6rem' },
      h('div', null, h('label', null, 'Registro del mes'), h('input', { type: 'month', value: mes, onchange: (e) => { if (e.target.value) { mes = e.target.value; pagina = 1; cargar(); } } })),
      h('button', { class: 'boton', type: 'button', onclick: () => window.print() }, 'Imprimir'),
      h('button', { class: 'boton', type: 'button', onclick: () => { location.href = '/api/recepciones.xlsx?mes=' + mes; } }, 'Descargar Excel')),
    tituloMes, registro);
  $vista.querySelectorAll('h1, h1 + p').forEach((e) => e.classList.add('no-imprimir'));
  $vista.classList.add('ancho');
  paginaHorizontal(true);
  await cargar();
}

// ------------------------------------------------------------------ paso a otras áreas (bodega principal)
// Bodega principal registra lo que pasa a pastelería y carnicería (lo de panadería lo registra bodega secundaria).
// Pestañas: pasar a otra área (producto → lote → área y cantidad), semáforo de caducidad e historial.
// ------------------------------------------------------------------ inventario
// Cada mes se revisa la bodega: todos los lotes empiezan como «Está»; los que se tocan quedan como «Ya no está»
// y al guardar salen de esa bodega. Así la lista no crece para siempre (bodega principal no anota lo que va a otras áreas).
function textoUltimoInventario(ult, diasInv) {
  if (!ult) return h('div', { class: 'nota' }, 'Todavía no se ha hecho ningún inventario de esta bodega.');
  const toca = ult.dias >= diasInv;
  return h('div', { class: 'nota' + (toca ? '' : ' verde') }, 'Último inventario: ', h('b', null, hora(ult.fecha_hora)), ' · ' + (ult.persona || ''),
    ' (' + (ult.dias === 0 ? 'hoy' : 'hace ' + ult.dias + ' día' + (ult.dias === 1 ? '' : 's')) + ')', toca ? '. Ya toca hacer el de este mes.' : '');
}

async function panelInventario(lugar, caja, volver) {
  const [datos, ings] = await Promise.all([api('/inventarios?lugar=' + lugar), api('/ingredientes')]);
  const cat = lugar === 'principal' ? await api('/bodega/catalogo') : null;
  const nombreDe = (l) => l.producto_bodega || l.ingrediente;      // el mismo nombre que en bodega principal
  const fotoDe = (l) => (cat ? (cat.productos.find((p) => p.producto === l.producto_bodega) || {}).foto : null)
    || (ings.find((i) => i.id === l.ingrediente_id) || {}).foto || null;
  const noEsta = new Set();
  let filtro = '';
  const grupos = new Map();
  datos.lotes.forEach((l) => { const n = nombreDe(l); grupos.set(n, (grupos.get(n) || []).concat(l)); });
  const nombres = [...grupos.keys()].sort((a, b) => a.localeCompare(b));
  const lista = h('div', null);
  const botonGuardar = h('button', { class: 'boton principal', type: 'button', onclick: () => guardar() });
  const contar = () => {
    botonGuardar.textContent = 'Guardar inventario · ' + datos.lotes.length + ' lote' + (datos.lotes.length === 1 ? '' : 's')
      + (noEsta.size ? ' · ' + noEsta.size + ' ya no está' + (noEsta.size === 1 ? '' : 'n') : '');
  };
  const filaLote = (l) => {
    const fuera = noEsta.has(l.id);
    return h('button', { type: 'button', class: 'inv-lote ' + (fuera ? 'no-esta' : 'esta'), onclick: () => {
      fuera ? noEsta.delete(l.id) : noEsta.add(l.id); pintarLista(); contar(); } },
      h('span', { class: 'inv-marca', 'aria-hidden': 'true' }, fuera ? '✕' : '✓'),
      h('span', { class: 'inv-datos' }, h('b', { class: 'mono' }, l.lote),
        h('span', { class: 'chico suave' }, [l.proveedor, l.fecha_ingreso ? 'ingresó ' + dia(l.fecha_ingreso) : null].filter(Boolean).join(' · '))),
      semaforo(l), h('span', { class: 'inv-estado' }, fuera ? 'Ya no está' : 'Está'));
  };
  const pintarLista = () => {
    const n = normal(filtro);
    const visibles = nombres.filter((nom) => !n || normal(nom + ' ' + grupos.get(nom).map((l) => l.lote).join(' ')).includes(n));
    llenar(lista, visibles.length ? visibles.map((nom) => h('div', { class: 'tarjeta inv-grupo' },
      h('div', { class: 'inv-titulo' }, fotoProducto(fotoDe(grupos.get(nom)[0]), nom, 'en-lote'), h('b', null, nom)),
      grupos.get(nom).map(filaLote))) : h('p', { class: 'suave' }, 'Ningún producto coincide con la búsqueda.'));
  };
  const guardar = () => intentar(async () => {
    const cerrar = datos.lotes.filter((l) => noEsta.has(l.id));
    const ok = await confirmar('Guardar inventario de ' + (lugar === 'principal' ? 'bodega principal' : 'bodega secundaria'),
      cerrar.length ? 'Estos lotes ya no están y salen de la lista: ' + cerrar.map((l) => nombreDe(l) + ' | ' + l.lote).join(', ') + '.'
        : 'Todos los lotes siguen en la bodega.', 'Guardar inventario');
    if (!ok) return;
    const r = await api('/inventarios', { lugar, items: datos.lotes.map((l) => ({ lote_id: l.id, esta: !noEsta.has(l.id) })) });
    avisar('Inventario guardado: ' + r.revisados + ' lotes revisados' + (r.cerrados ? ', ' + r.cerrados + ' cerrados' : ''), 'bien');
    volver();
  });
  llenar(caja,
    textoUltimoInventario(datos.ultimo, datos.dias_inventario),
    datos.lotes.length ? [
      h('p', { class: 'suave', style: 'margin:.2rem 0 .6rem' }, 'Revisa la bodega. Toca los lotes que ya no están.'),
      datos.lotes.length > 6 ? h('input', { type: 'search', placeholder: 'Buscar producto o lote', oninput: (e) => { filtro = e.target.value; pintarLista(); } }) : null,
      lista, h('div', { style: 'height:5rem' }), h('div', { class: 'pie-fijo' }, h('div', null, botonGuardar))]
      : h('p', { class: 'suave' }, 'No hay lotes en esta bodega.'));
  pintarLista(); contar();
}

async function panelHistorialInventario(lugar, caja, recargar) {
  const datos = await api('/inventarios?lugar=' + lugar);
  const reabrir = (c) => intentar(async () => {
    if (!await confirmar('Volver a poner el lote ' + c.lote, 'Se marcó por error como «ya no está». Vuelve a la lista de la bodega.', 'Sí, volver a poner')) return;
    await api('/inventarios/lotes/' + c.id + '/reabrir', {});
    avisar('Lote ' + c.lote + ' de nuevo en la bodega', 'bien'); recargar();
  });
  // Historial de 15 en 15.
  let pag = 1;
  const pintarHist = () => llenar(caja,
    h('div', { class: 'fila-botones', style: 'margin-top:0' }, h('button', { class: 'boton', type: 'button',
      onclick: () => { location.href = '/api/inventarios.xlsx'; } }, 'Descargar Excel')),
    datos.inventarios.length ? trozo(datos.inventarios, pag, 15).map((inv) => h('div', { class: 'tarjeta' },
      h('div', { style: 'display:flex;justify-content:space-between;gap:.6rem;flex-wrap:wrap' },
        h('b', null, hora(inv.fecha_hora) + ' · ' + (inv.persona || '')),
        h('span', { class: 'chico suave' }, inv.revisados + ' revisados · ' + inv.cerrados + ' ya no estaban')),
      inv.cerrados_lotes.length ? h('div', { style: 'margin-top:.5rem' }, inv.cerrados_lotes.map((c) => h('div', { class: 'fila-cerrado' },
        h('span', null, h('b', null, c.producto || c.ingrediente), ' | ', h('b', { class: 'mono' }, c.lote)),
        c.reabierto_en ? h('span', { class: 'etq gris' }, 'Vuelto a poner ' + hora(c.reabierto_en))
          : h('button', { class: 'boton chico', type: 'button', onclick: () => reabrir(c) }, 'Se marcó por error')))) : null))
      : h('p', { class: 'suave' }, 'Todavía no hay inventarios.'),
    paginador(datos.inventarios.length, pag, 15, (n) => { pag = n; pintarHist(); caja.scrollIntoView({ block: 'start', behavior: 'smooth' }); }, 'Inventarios'));
  pintarHist();
}

// Bodega principal: Inventario · Semáforo · Historial
async function vistaInventario(pestana) {
  pestana = ['inventario', 'semaforo', 'historial'].includes(pestana) ? pestana : 'inventario';
  const ir = (id) => { history.replaceState(null, '', '#/inventario/' + id); vistaInventario(id); };
  const caja = h('div', null);
  let rojos = 0;
  let lotes = [];
  if (pestana === 'semaforo') { lotes = (await api('/bodega/lotes')).lotes; rojos = lotes.filter((l) => l.semaforo === 'rojo').length; }
  const pest = [['inventario', 'Inventario'], ['semaforo', 'Semáforo'], ['historial', 'Historial']];
  pintar(encabezado('Inventario de bodega principal'),
    h('div', { class: 'pestanas' }, pest.map(([id, nombre]) => h('button', { type: 'button', class: id === pestana ? 'activa' : '', onclick: () => ir(id) }, nombre))),
    caja);
  if (pestana === 'inventario') return panelInventario('principal', caja, () => ir('historial'));
  if (pestana === 'historial') return panelHistorialInventario('principal', caja, () => ir('historial'));
  const cat = await api('/bodega/catalogo');
  const fotoDe = (nombre) => (cat.productos.find((p) => p.producto === nombre) || {}).foto || null;
  let filtro = '';
  const pintarSem = () => {
    const n = normal(filtro);
    const visibles = lotes.filter((l) => !n || normal([l.producto_bodega, l.ingrediente, l.lote, l.proveedor].join(' ')).includes(n));
    llenar(caja, lotes.length ? leyendaSemaforo(lotes) : null,
      lotes.length > 6 ? buscadorLotes(caja, filtro, (v) => { filtro = v; pintarSem(); }) : null,
      lotes.length ? (visibles.length ? visibles.map((l) => tarjetaSemaforo(l, l.producto_bodega || l.ingrediente, false, [
        h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => { if (await editarIngresoLote(l)) ir('semaforo'); }) }, 'Editar ingreso'),
        h('button', { class: 'boton chico peligro', type: 'button', onclick: () => intentar(async () => { if (await darDeBaja(l, 'principal', null)) ir('semaforo'); }) }, 'Dar de baja')],
        fotoDe(l.producto_bodega))) : h('p', { class: 'suave' }, 'Ningún lote coincide con la búsqueda.'))
        : h('p', { class: 'suave' }, 'No hay lotes en bodega principal.'));
  };
  pintarSem();
}

// ------------------------------------------------------------------ bodega secundaria
// Pestañas: registrar ingreso (lo que llega de bodega principal) y semáforo de lo que hay en bodega secundaria.
async function vistaSecundaria(pestana) {
  pestana = ['semaforo', 'inventario', 'historial'].includes(pestana) ? pestana : 'ingreso';
  const [ings, lotesSec] = await Promise.all([api('/ingredientes'), api('/lotes?donde=secundaria')]);
  const deProveedor = ings.filter((i) => i.tipo === 'lote' && i.activo);
  const enBodega = ordenarSemaforo(lotesSec);
  // Semáforo de lo que hay en bodega secundaria: arriba lo caducado y lo que caduca antes.
  const cajaSec = h('div', null);
  let filtro = '';
  const pintarSec = () => {
    const n = normal(filtro);
    const visibles = enBodega.filter((l) => !n || normal([l.ingrediente, l.producto_bodega, l.lote, l.proveedor].join(' ')).includes(n));
    llenar(cajaSec,
      enBodega.length ? leyendaSemaforo(enBodega) : null,
      enBodega.length > 6 ? buscadorLotes(cajaSec, filtro, (v) => { filtro = v; pintarSec(); }) : null,
      enBodega.length ? (visibles.length ? visibles.map((l) => tarjetaSemaforo(l, l.producto_bodega || l.ingrediente, l.disponible_cocina, [
        l.semaforo ? null : h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => {
          if (await editarIngresoLote(l)) vistaSecundaria('semaforo'); }) }, 'Poner caducidad'),
        h('button', { class: 'boton chico peligro', type: 'button', onclick: () => intentar(async () => {
          if (await darDeBaja(l, 'secundaria', l.disponible_cocina)) vistaSecundaria('semaforo'); }) }, 'Dar de baja')],
        (ings.find((i) => i.id === l.ingrediente_id) || {}).foto || null))
        : h('p', { class: 'suave' }, 'Ningún lote coincide con la búsqueda.'))
        : h('p', { class: 'suave' }, 'Todavía no hay lotes registrados.'));
  };
  const rojos = enBodega.filter((l) => l.semaforo === 'rojo').length;
  const pest = [['ingreso', 'Registrar ingreso'], ['semaforo', ['Semáforo', rojos ? h('span', { class: 'num rojo', title: rojos + ' caducado(s)' }, rojos) : null]],
    ['inventario', 'Inventario'], ['historial', 'Historial']];
  const cajaInv = h('div', null);
  pintar(encabezado('Bodega secundaria', 'Registra aquí cada lote que llega desde bodega principal.'),
    h('div', { class: 'pestanas' }, pest.map(([id, nombre]) => h('button', { type: 'button', class: (id === pestana ? 'activa' : '') + (id === 'semaforo' && rojos ? ' con-rojo' : ''),
      onclick: () => { history.replaceState(null, '', '#/secundaria/' + id); vistaSecundaria(id); } }, nombre))),
    pestana === 'ingreso' ? [
      h('h2', { style: 'margin-top:.4rem' }, '¿Qué ingrediente llegó?'),
      // Solo se puede elegir un ingrediente que tenga lotes en bodega principal (el número no se muestra).
      h('div', { class: 'rejilla' }, deProveedor.map((i) => h('button', { class: 'mosaico con-foto' + (i.n_principal ? '' : ' vacio'), type: 'button',
        disabled: !i.n_principal, onclick: () => intentar(() => elegirIngreso(i)) }, fotoProducto(i.foto, i.nombre), h('b', null, i.nombre)))),
      h('p', { class: 'chico suave' }, 'Aquí se registra todo lo que llega a la panadería, secos y refrigerados (mantequilla, huevos, leche…).')]
      : pestana === 'semaforo' ? cajaSec : cajaInv);
  if (pestana === 'semaforo') pintarSec();
  const irSec = (id) => { history.replaceState(null, '', '#/secundaria/' + id); vistaSecundaria(id); };
  if (pestana === 'inventario') await panelInventario('secundaria', cajaInv, () => irSec('historial'));
  if (pestana === 'historial') await panelHistorialInventario('secundaria', cajaInv, () => irSec('historial'));
}

async function elegirIngreso(ing) {
  const lotes = await api('/lotes?donde=principal&ingrediente_id=' + ing.id);
  const elegidos = new Map();          // lote_id -> { cantidad, vacio }
  let pag = 1;
  let enfocar = null;
  const bloqueCantidad = (l) => {
    const e = elegidos.get(l.id);
    const { campo, eq } = campoEmpaques(l, e.cantidad, (v) => { e.cantidad = v; });
    if (enfocar === l.id) setTimeout(() => campo.focus(), 50);
    return h('div', { class: 'tarjeta', style: 'margin-top:-.3rem;border:2px solid var(--ok)' },
      h('label', { style: 'margin-top:0' }, l.empaque ? cuantos(l.empaque) + ' de este lote pasan a bodega secundaria?'
        : '¿Cuánto de este lote pasa a bodega secundaria? (' + unidadDe(l, 2) + ')'), campo, eq,
      l.disponible != null ? h('div', { class: 'chico suave' }, 'En bodega principal quedan ' + cantidadLote(Math.max(l.disponible, 0), l) + '.') : null,
      l.disponible != null
        ? null
        : h('label', { style: 'display:flex;gap:.5rem;align-items:center;font-weight:400' },
          h('input', { type: 'checkbox', checked: e.vacio, onchange: (ev) => { e.vacio = ev.target.checked; } }),
          'Ya no queda más de este lote en bodega principal'));
  };
  const dibujar = () => {
    const y = window.scrollY;
    const visibles = trozo(lotes, pag, 5);
    pintar(h('a', { class: 'volver', href: '#/secundaria', onclick: (e) => { e.preventDefault(); vistaSecundaria(); } }, [h('span', { class: 'flecha-volver', 'aria-hidden': 'true' }, '←'), 'Volver a Bodega secundaria']),
      h('h1', null, ing.nombre),
      h('p', { class: 'suave' }, 'Toca el lote que llegó (compara con el empaque) y escribe cuánto de ese lote pasó a bodega secundaria.'),
      visibles.length ? visibles.map((l) => [
        tarjetaLote(l, { elegible: true, breve: true, marcado: elegidos.has(l.id), conNota: true, alTocar: () => {
          if (elegidos.has(l.id)) elegidos.delete(l.id);
          else { elegidos.set(l.id, { cantidad: '', vacio: false }); enfocar = l.id; }
          dibujar();
        } }),
        elegidos.has(l.id) ? bloqueCantidad(l) : null])
        : h('div', { class: 'nota' }, 'No hay lotes de este ingrediente en bodega principal.'),
      paginador(lotes.length, pag, 5, (n) => { pag = n; dibujar(); window.scrollTo(0, 0); }, 'Lotes'),
      elegidos.size && [...elegidos.keys()].some((id) => !visibles.some((l) => l.id === id))
        ? h('p', { class: 'chico suave', style: 'text-align:center' }, 'Tienes lotes elegidos en otras páginas.') : null,
      h('div', { class: 'fila-botones' }, h('button', { class: 'boton chico peligro', type: 'button',
        onclick: async () => { if (await loteManual(ing, 'secundaria')) vistaSecundaria(); } }, 'No encuentro el lote en la lista')),
      h('div', { class: 'pie-fijo' }, h('div', null, h('button', { class: 'boton ok ancho', type: 'button', disabled: !elegidos.size,
        onclick: () => intentar(async () => {
          const items = [...elegidos].map(([lote_id, e]) => ({ lote_id, cantidad: e.cantidad, vacio: e.vacio }));
          const caducados = lotes.filter((l) => elegidos.has(l.id) && l.semaforo === 'rojo');
          if (caducados.length && !await confirmar('Lote caducado',
            (caducados.length === 1 ? 'El lote ' + caducados[0].lote + ' está caducado' : 'Los lotes ' + listaLotes(caducados) + ' están caducados')
            + '. ¿Seguro que quieres registrarlo en bodega secundaria?', 'Sí, registrarlo igual')) return;
          await api('/secundaria/ingreso', { items });
          avisar(items.length + ' lote(s) registrados en bodega secundaria', 'bien'); vistaSecundaria();
        }) }, elegidos.size ? 'Registrar ' + elegidos.size + ' lote(s) en bodega secundaria' : 'Elige al menos un lote'))));
    window.scrollTo(0, y);
    enfocar = null;
  };
  dibujar();
}

// ------------------------------------------------------------------ cocina
// Recipiente con lotes mezclados: toda la mezcla caduca con el lote que caduca primero (si uno está caducado, todo lo está).
const esMezcla = (g) => g.n_recipientes !== 0 && g.tipo !== 'interno' && g.lotes.length > 1;
function estadoMezcla(g) {
  const conFecha = g.lotes.filter((l) => l.semaforo);
  return conFecha.length ? ordenarSemaforo(conFecha)[0] : {};
}
// Semáforo de cocina: un renglón por recipiente mezclado y uno por cada lote que está solo (o en su propio empaque).
function unidadesSemaforoCocina(grupos) {
  const salida = [];
  for (const g of grupos) {
    if (esMezcla(g)) {
      const peor = estadoMezcla(g);
      salida.push({ g, lotes: g.lotes, semaforo: peor.semaforo, caducidad: peor.caducidad, dias_caducidad: peor.dias_caducidad, peor, ingrediente: g.ingrediente });
    } else g.lotes.forEach((l) => salida.push({ g, lotes: [l], semaforo: l.semaforo, caducidad: l.caducidad, dias_caducidad: l.dias_caducidad, ingrediente: g.ingrediente }));
  }
  return ordenarSemaforo(salida);
}

async function vistaCocina(pestana) {
  pestana = pestana === 'semaforo' ? 'semaforo' : 'cocina';
  const [ings, rec] = await Promise.all([api('/ingredientes'), api('/cocina/recipientes')]);
  const usables = ings.filter((i) => i.tipo !== 'no_aplica');
  // Subreceta (relleno, glacé, masa madre): sigue en uso (guardada en el refrigerador) hasta que se acaba o se hace una nueva.
  const cerrarSub = (g, l) => intentar(async () => {
    if (!await confirmar('Se terminó ' + g.ingrediente, 'El lote ' + l.lote + ' se cierra y ya no se anotará en los panes.', 'Sí, se terminó')) return;
    await api('/cocina/vaciar', { ingrediente_id: g.ingrediente_id, recipiente: g.recipiente, lote_ids: [l.id], terminado: true });
    avisar('Lote cerrado', 'bien'); vistaCocina();
  });
  const estadoGrupo = (g) => (esMezcla(g) ? estadoMezcla(g) : ordenarSemaforo(g.lotes.filter((l) => l.semaforo))[0] || {});
  const tarjetaRecipiente = (g) => h('div', { class: 'tarjeta tarjeta-sem borde-' + (estadoGrupo(g).semaforo || 'sin') },
    h('div', { style: 'display:flex;justify-content:space-between;gap:.6rem;align-items:center;flex-wrap:wrap' },
      h('div', null, h('b', null, g.ingrediente), ' ', g.tipo === 'interno' ? null : h('span', { class: 'etq gris' },
        g.n_recipientes === 0 ? 'en su empaque' : g.n_recipientes > 1 ? 'Recipiente ' + g.recipiente : 'Recipiente'),
      g.n_recipientes !== 0 && g.lotes.length > 1 ? [' ', h('span', { class: 'etq ' + (g.exceso ? 'rojo' : 'aviso') }, g.lotes.length + ' lotes mezclados')] : null,
      esMezcla(g) && estadoMezcla(g).semaforo ? h('div', { class: 'chico', style: 'margin-top:.3rem' }, 'Toda la mezcla: ', semaforo(estadoMezcla(g))) : null),
      esMezcla(g) ? h('button', { class: 'boton chico peligro', type: 'button',
        onclick: () => bajaRecipiente({ g, lotes: g.lotes, ingrediente: g.ingrediente, semaforo: estadoMezcla(g).semaforo }) }, 'Dar de baja todo') : null),
    g.exceso ? h('div', { class: 'nota rojo' }, 'Más de ' + rec.maximo + ' lotes mezclados: conviene vaciar y limpiar este recipiente antes de poner el siguiente lote.') : null,
    g.lotes.map((l) => h('div', { style: 'display:flex;justify-content:space-between;gap:.6rem;align-items:center;margin-top:.45rem' },
      h('div', null, h('span', { class: 'chip-lote' }, l.lote), l.origen === 'manual' ? h('span', { class: 'etq rojo' }, 'a mano') : null,
        h('span', { class: 'chico suave' }, ' desde ' + hora(l.abierto_en)), ' ', semaforo(l)),
      h('div', { class: 'fila-botones', style: 'margin:0' },
        g.tipo === 'interno' && l.semaforo !== 'rojo' ? h('button', { class: 'boton chico', type: 'button', onclick: () => cerrarSub(g, l) }, 'Se terminó')
 : null,
        h('button', { class: 'boton chico peligro', type: 'button', onclick: () => intentar(async () => {
          if (await darDeBaja(Object.assign({ ingrediente: g.ingrediente }, l), 'cocina', null)) vistaCocina(); }) }, 'Dar de baja')))));
  // Ingredientes de bodega (llegan de bodega secundaria). Las subrecetas no se abren aquí: su lote queda en uso
  // desde que se empieza su producción hasta que se termina, se da de baja o se reemplaza por una nueva tanda.
  const deBodega = usables.filter((i) => i.tipo !== 'interno' && (i.activo || i.n_abiertos));
  const enUso = (i) => i.n_abiertos ? h('span', { class: 'num verde' }, i.n_abiertos + ' en uso') : null;
  const fotoDe = (id) => (ings.find((i) => i.id === id) || {}).foto || null;
  const unidades = unidadesSemaforoCocina(rec.grupos);
  const bajaRecipiente = (u) => intentar(async () => {
    const motivo = segmento([['CADUCADO', 'Caducado'], ['CONTAMINADO', 'Contaminado']], u.semaforo === 'rojo' ? 'CADUCADO' : '');
    const comentario = h('input', { type: 'text', placeholder: 'Qué pasó (opcional)' });
    const d = await modal('Dar de baja todo el recipiente', h('div', null,
      h('p', { style: 'margin-top:0' }, h('b', null, u.ingrediente), ' | ', h('b', { class: 'mono' }, listaLotes(u.lotes))),
      h('p', { class: 'chico suave' }, 'Están mezclados: se dan de baja los ' + u.lotes.length + ' lotes y dejan de anotarse en los panes.'),
      h('label', null, 'Motivo'), motivo.el, h('label', null, 'Comentario'), comentario),
    [{ texto: 'Dar de baja los ' + u.lotes.length + ' lotes', clase: 'peligro-lleno', accion: () => {
      if (!motivo.valor) throw new Error('Elige el motivo.');
      return { motivo: motivo.valor, comentario: comentario.value.trim() };
    } },
      { texto: 'Cancelar', valor: null }]);
    if (!d) return;
    for (const l of u.lotes) await api('/bajas', { lote_id: l.id, lugar: 'cocina', motivo: d.motivo, cantidad: '', comentario: d.comentario });
    avisar('Recipiente dado de baja', 'bien'); vistaCocina();
  });
  const ordenGrupos = [...rec.grupos].sort((a, b) => (ORDEN_SEM[estadoGrupo(a).semaforo] ?? 3) - (ORDEN_SEM[estadoGrupo(b).semaforo] ?? 3)
    || String(estadoGrupo(a).caducidad || '9999').localeCompare(String(estadoGrupo(b).caducidad || '9999')) || a.ingrediente.localeCompare(b.ingrediente));
  pintar(encabezado('Cocina', 'Antes de abrir un empaque nuevo, regístralo aquí. Lo que está abierto es lo que se anota en las hojas.'),
    h('h2', null, 'Abrir un lote: ¿de qué ingrediente?'),
    h('div', { class: 'rejilla' }, deBodega.map((i) => h('button', { class: 'mosaico con-foto foto-chica' + (i.n_secundaria ? '' : ' vacio'), type: 'button',
      onclick: () => intentar(() => elegirApertura(i)) },
    enUso(i), fotoProducto(i.foto, i.nombre), h('b', null, i.nombre)))),
    h('h2', null, 'En uso ahora en cocina ', h('span', { class: 'num verde' }, rec.grupos.reduce((n, g) => n + g.lotes.length, 0))),
    h('p', { class: 'chico suave' }, 'Estos lotes se anotan en cada pan que se empiece. Cuando pongas un lote nuevo, la app pregunta si el anterior se acabó.'),
    rec.grupos.some((g) => g.lotes.some((l) => l.semaforo === 'rojo'))
      ? h('div', { class: 'nota rojo' }, 'Hay un lote caducado en uso: dalo de baja. Mientras siga abierto, no se pueden empezar los panes que lo llevan.') : null,
    rec.grupos.length ? [leyendaSemaforo(unidades), ordenGrupos.map(tarjetaRecipiente)] : h('p', { class: 'suave' }, 'No hay ningún lote en uso.'));
}

async function elegirApertura(ing) {
  const conCantidad = ing.tipo === 'lote';            // las subrecetas no llevan cantidad
  // Cocina solo puede tomar lo que hay en bodega secundaria: un lote ya tomado completo está en "En uso ahora en cocina".
  const lotes = conCantidad
    ? (await api('/lotes?donde=secundaria&ingrediente_id=' + ing.id)).filter((l) => l.disponible_cocina == null || l.disponible_cocina > 0)
    : await api('/lotes?donde=abribles&ingrediente_id=' + ing.id);
  const lugar = 'bodega secundaria';
  const elegidos = new Map();                         // lote_id -> { cantidad }
  let enfocar = null;
  const sinSaldo = (l) => conCantidad && l.disponible_cocina != null && l.disponible_cocina <= 0;
  const nRec = ing.recipientes || 0;
  const bloqueCantidad = (l) => {
    const e = elegidos.get(l.id);
    const { campo, eq } = campoEmpaques(l, e.cantidad, (v) => { e.cantidad = v; }, 'ej. 1');
    if (enfocar === l.id) setTimeout(() => campo.focus(), 50);
    const rec = nRec > 1 ? segmento(Array.from({ length: nRec }, (_, i) => [String(i + 1), 'Recipiente ' + (i + 1)]),
      e.recipiente ? String(e.recipiente) : '', (v) => { e.recipiente = Number(v); }) : null;
    return h('div', { class: 'tarjeta', style: 'margin-top:-.3rem;border:2px solid var(--ok)' },
      rec ? [h('label', { style: 'margin-top:0' }, '¿En qué recipiente lo pones?'), rec.el] : null,
      h('label', { style: rec ? '' : 'margin-top:0' }, l.empaque ? cuantos(l.empaque) + ' de este lote toma cocina?'
        : '¿Cuánto de este lote toma cocina? (' + unidadDe(l, 2) + ')'), campo, eq,
      h('div', { class: 'chico suave' }, l.disponible_cocina != null
        ? 'En ' + lugar + ' hay ' + cantidadLote(l.disponible_cocina, l) + '.'
        : 'No se sabe cuánto hay en ' + lugar + ' de este lote (se pasó antes de registrar cantidades).'));
  };
  const dibujar = () => {
    const y = window.scrollY;
    pintar(h('a', { class: 'volver', href: '#/cocina', onclick: (e) => { e.preventDefault(); vistaCocina(); } }, [h('span', { class: 'flecha-volver', 'aria-hidden': 'true' }, '←'), 'Volver a Cocina']),
      h('h1', null, ing.nombre),
      h('p', { class: 'suave' }, ing.tipo === 'interno' ? 'Lotes guardados de esta subreceta. Toca el que vas a usar.'
          : 'Toca el lote que tomas de bodega secundaria y escribe cuánto.'),
      lotes.length ? lotes.map((l) => {
        if (sinSaldo(l) || (!conCantidad && l.abierto_en)) return tarjetaLote(l);
        if (l.semaforo === 'rojo') return [tarjetaLote(l, { cocina: conCantidad }), h('div', { class: 'nota rojo', style: 'margin-top:-.3rem' },
          'Caducado: no se puede usar. ' + (ing.tipo === 'interno' ? 'Dalo por terminado.' : 'Dalo de baja en Bodega secundaria.'))];
        const terminar = ing.tipo === 'interno' ? h('button', { class: 'boton chico', type: 'button', onclick: (e) => { e.stopPropagation(); intentar(async () => {
          if (!await confirmar('Se terminó ' + ing.nombre, 'El lote ' + l.lote + ' se cierra y ya no aparecerá para abrir.', 'Sí, se terminó')) return;
          await api('/lotes/agotar', { lote_id: l.id }); avisar('Lote cerrado', 'bien'); elegirApertura(ing);
        }); } }, 'Se terminó') : null;
        return [tarjetaLote(l, { elegible: true, cocina: conCantidad, marcado: elegidos.has(l.id), conNota: !conCantidad, acciones: terminar, alTocar: () => {
          if (elegidos.has(l.id)) elegidos.delete(l.id);
          else { elegidos.set(l.id, { cantidad: '' }); enfocar = l.id; }
          dibujar();
        } }), conCantidad && elegidos.has(l.id) ? bloqueCantidad(l) : null];
      })
        : h('div', { class: 'nota' }, ing.tipo === 'interno' ? 'No hay lotes de esta subreceta. Inicia su producción en «Producción».'
          : 'No hay lotes de este ingrediente en bodega secundaria.'),
      ing.tipo === 'lote' ? h('div', { class: 'fila-botones' }, h('button', { class: 'boton chico peligro', type: 'button',
        onclick: async () => { if (await loteManual(ing, 'cocina')) vistaCocina(); } }, 'No encuentro el lote en la lista')) : null,
      h('div', { class: 'pie-fijo' }, h('div', null, h('button', { class: 'boton ok ancho', type: 'button', disabled: !elegidos.size,
        onclick: () => intentar(async () => {
          if (nRec > 1 && ing.tipo !== 'interno' && [...elegidos.values()].some((e) => !e.recipiente)) throw new Error('Elige en qué recipiente pones cada lote.');
          const items = [...elegidos].map(([lote_id, e]) => ({ lote_id, cantidad: e.cantidad, recipiente: e.recipiente || 1 }));
          for (const it of items) {
            const c = Number(String(it.cantidad || '').replace(',', '.'));
            if (conCantidad && !(c > 0)) throw new Error('Escribe cuánto tomas de cada lote (solo el número).');
          }
          const decisiones = await preguntarAntesDeAbrir(ing, items, lotes);
          if (!decisiones) return;                       // X: no se registra nada
          const r = await api('/cocina/abrir', { items });
          for (const d of decisiones.filter((x) => x.respuesta === 'vacio'))
            await api('/cocina/vaciar', { ingrediente_id: ing.id, recipiente: d.recipiente, lote_ids: d.anteriores.map((l) => l.id) });
          avisar('Registrado en cocina', 'bien');
          await preguntarAnteriores(r.anteriores, new Set(decisiones.filter((x) => x.respuesta === 'vacio').map((x) => x.recipiente))); vistaCocina();
        }) }, elegidos.size ? 'Registrar ' + elegidos.size + ' lote(s) en cocina' : 'Elige el lote que vas a usar'))));
    window.scrollTo(0, y);
    enfocar = null;
  };
  dibujar();
}

// ------------------------------------------------------------------ produccion
function tarjetaProduccion(p) {
  return h('a', { class: 'tarjeta', href: '#/hoja/' + p.id, style: 'display:block;text-decoration:none;color:inherit' },
    h('div', { style: 'display:flex;justify-content:space-between;gap:.6rem;align-items:start' },
      h('div', null, h('div', { class: 'nombre-hoja' }, p.producto), h('div', { class: 'mono lote-hoja' }, p.lote_final),
        h('div', { class: 'chico suave' }, 'Inició ' + hora(p.iniciado_en) + ' · ' + (p.iniciado_por || ''))),
      h('div', { style: 'text-align:right' }, h('span', { class: 'etq ' + CLASE_ESTADO[p.estado] }, NOMBRE_ESTADO[p.estado]),
        p.estado === 'en_curso' && p.masa_lista_en ? h('div', null, h('span', { class: 'etq gris' }, 'Masa en reposo')) : null,
        p.faltantes ? h('div', null, h('span', { class: 'etq rojo' }, 'Faltan ' + p.faltantes + ' lote(s)')) : null)));
}

// Cocina: los chefs solo avisan que empieza y que termina la produccion de un pan.
async function vistaProduccion() {
  const [productos, enCurso] = await Promise.all([api('/productos'), api('/producciones?estado=en_curso')]);
  // Vista previa: ingredientes de la hoja con los lotes que ya quedaron anotados.
  const hojas = await Promise.all(enCurso.map((p) => api('/producciones/' + p.id)));
  const ingredientesDe = (hoja) => h('div', { class: 'vista-ingredientes' }, hoja.lineas.map((li) => {
    const vigentes = li.lotes.filter((l) => !l.quitado_en);
    return h('div', { class: 'fila-ing' + (li.falta_lote && !li.despues_reposo ? ' falta' : '') },
      h('span', { class: 'nombre-ing' }, li.etiqueta),
      li.tipo === 'no_aplica' ? h('span', { class: 'suave' }, '—')
        : li.no_utilizado ? h('span', { class: 'suave' }, 'No se usó')
        : vigentes.length ? h('span', null, vigentes.map((l) => h('span', { class: 'chip-lote' }, l.lote)))
        : li.despues_reposo ? h('span', { class: 'suave' }, 'Al terminar')
        : h('span', { class: 'sin-lote' }, 'Sin lote'));
  }));
  const masaLista = (p) => intentar(async () => {
    if (!await confirmar('Masa lista: ' + p.producto, 'La masa queda en reposo. Los lotes que se abran desde ahora ya no se anotan en esta masa' +
      ' (solo lo que se pone al terminar, como el relleno). Mañana toca «Terminó».', 'Sí, masa lista')) return;
    await api('/producciones/' + p.id + '/masa-lista', {});
    avisar(p.producto + ': masa en reposo', 'bien'); vistaProduccion();
  });
  const tarjetaEnCurso = (p, i) => h('div', { class: 'tarjeta' },
    h('div', { style: 'display:flex;justify-content:space-between;gap:.8rem;align-items:center;flex-wrap:wrap' },
      h('div', null, h('div', { class: 'nombre-en-curso' }, p.producto), h('div', { class: 'mono' }, p.lote_final),
        h('div', { class: 'chico suave' }, 'Empezó ' + hora(p.iniciado_en) + ' · ' + (p.iniciado_por || '')),
        p.masa_lista_en ? h('span', { class: 'etq aviso', style: 'margin-top:.3rem' }, 'En reposo desde ' + hora(p.masa_lista_en)) : null),
      p.lleva_reposo && !p.masa_lista_en ? h('button', { class: 'boton principal', type: 'button', onclick: () => masaLista(p) }, 'Masa lista')
      : h('button', { class: 'boton ok', type: 'button', onclick: () => intentar(async () => {
        if (!await confirmar('Terminó ' + p.producto, 'Producción ' + p.lote_final + '. El encargado completará la hoja.', 'Sí, terminó')) return;
        await api('/producciones/' + p.id + '/terminar', {});
        avisar('Listo: ' + p.producto + ' terminó', 'bien'); vistaProduccion();
      }) }, 'Terminó')),
    hojas[i] && hojas[i].lineas.length ? ingredientesDe(hojas[i]) : null,
    h('button', { class: 'boton chico', type: 'button', style: 'margin-top:.6rem;border:0;padding-left:0;color:var(--tinta-suave)',
      onclick: () => intentar(async () => {
        const motivo = await pedirTexto('Anular ' + p.producto + ' ' + p.lote_final, '¿Qué pasó? (por ejemplo: me equivoqué de pan)');
        if (!motivo) return;
        await api('/producciones/' + p.id + '/anular', { motivo }); avisar('Anulada'); vistaProduccion();
      }) }, 'Me equivoqué de pan'));
  pintar(encabezado('Producción'),
    h('h2', null, 'En producción ahora ', h('span', { class: 'num verde' }, enCurso.length)),
    enCurso.length ? enCurso.map(tarjetaEnCurso) : h('p', { class: 'suave' }, 'No hay ningún pan en producción.'),
    h('h2', null, 'Empezar producción de…'),
    // Un pan que ya está en producción no se puede empezar otra vez hasta que termine.
    h('div', { class: 'rejilla' }, productos.filter((p) => p.activo && !p.armado).map((p) => {
      const enMarcha = enCurso.some((x) => x.producto_id === p.id && !x.masa_lista_en);
      return h('button', { class: 'mosaico' + (p.lineas.length && !enMarcha ? '' : ' vacio'), disabled: enMarcha,
      type: 'button', onclick: (ev) => intentar(async () => {
        if (ev.currentTarget) ev.currentTarget.disabled = true;
        // Subreceta: antes de empezar, qué pasa con lo que quedaba de la anterior (abierto o guardado).
        const previos = p.produce_ingrediente_id ? await api('/lotes?donde=abribles&ingrediente_id=' + p.produce_ingrediente_id) : [];
        let anteriores;
        if (previos.length) {
          anteriores = await preguntarSubrecetaAnterior(p, previos);
          if (!anteriores) { vistaProduccion(); return; }
        } else if (!await confirmar('Empezar ' + p.nombre, 'Se anotarán los lotes que están abiertos en cocina en este momento.', 'Empezar')) { vistaProduccion(); return; }
        const hoja = await api('/producciones', anteriores ? { producto_id: p.id, anteriores } : { producto_id: p.id });
        avisar('Empezó ' + p.nombre + ' · lote ' + hoja.lote_final, 'bien');
        await preguntarAnteriores(hoja.anteriores);
        vistaProduccion();
      }) }, h('b', null, p.nombre), h('small', null, enMarcha ? 'En producción' : p.produce ? 'Subreceta' : (p.lineas.length ? 'Lote …' + p.codigo : 'Sin receta todavía')));
    })));
}

// Antes de empezar una subreceta: ¿lo que quedaba de la anterior se terminó, se guarda aparte o se mezcla con la nueva?
async function preguntarSubrecetaAnterior(p, previos, respuestas) {
  respuestas = respuestas || new Map();
  const filas = previos.map((l) => {
    // Solo dos casos reales: se hace más porque se acabó lo anterior, o porque no alcanza y se mezcla con lo nuevo.
    if (l.semaforo === 'rojo') respuestas.set(l.id, 'terminado');      // caducado: no se puede mezclar
    const seg = segmento(l.semaforo === 'rojo' ? [['terminado', 'Se terminó (caducado)']] : [['terminado', 'Se terminó'], ['mezcla', 'Se mezcla con el nuevo']], respuestas.get(l.id) || '',
      (v) => { respuestas.set(l.id, v); });
    return h('div', { class: 'tarjeta', style: 'margin:.5rem 0' },
      h('div', null, h('b', { class: 'mono' }, l.lote), ' ', semaforo(l), ' ', l.abierto_en ? h('span', { class: 'etq verde' }, 'abierto en cocina') : h('span', { class: 'etq gris' }, 'guardado')),
      h('div', { style: 'margin-top:.4rem' }, seg.el));
  });
  const ok = await modal('Antes de empezar ' + p.nombre, h('div', null,
    h('p', { style: 'margin-top:0' }, 'Todavía hay ' + (previos.length === 1 ? 'un lote anterior' : previos.length + ' lotes anteriores') + '. ¿Qué pasa con ' + (previos.length === 1 ? 'él' : 'cada uno') + '?'),
    filas,
    h('p', { class: 'chico suave' }, 'Si se mezcla, el lote nuevo vence cuando vence el más antiguo.')),
  [{ texto: 'Empezar', clase: 'principal', valor: true }, { texto: 'Cancelar', valor: null }]);
  if (!ok) return null;
  if (previos.some((l) => !respuestas.has(l.id))) {
    avisar('Responde qué pasa con cada lote anterior antes de empezar.');
    return preguntarSubrecetaAnterior(p, previos, respuestas);
  }
  return previos.map((l) => ({ lote_id: l.id, accion: respuestas.get(l.id) }));
}

// Produccion: el encargado ve lo que se esta produciendo y completa las hojas terminadas.
async function vistaHojas() {
  const [inicio, productos] = await Promise.all([api('/producciones?pagina=1'), api('/productos')]);
  const mezclas = productos.filter((p) => p.armado && p.activo);
  const grupo = (estado) => inicio.abiertas.filter((p) => p.estado === estado);
  const textos = {
    en_curso: 'Se están produciendo ahora. Se pueden completar cuando cocina avise que terminaron.',
    por_completar: 'Cocina ya terminó: falta llenar cantidades, horneado y unidades, y cerrar la hoja.',
    completa: '',
  };
  // Completas: de 5 en 5; cada página se pide al servidor (no se carga todo el historial).
  const cajaCompletas = h('div', null);
  const pintarCompletas = (r) => llenar(cajaCompletas,
    r.completas.length ? r.completas.map(tarjetaProduccion) : h('p', { class: 'suave' }, 'Ninguna.'),
    paginador(r.total_completas, r.pagina, r.por_pagina, (n) => intentar(async () => {
      pintarCompletas(await api('/producciones?pagina=' + n));
      cajaCompletas.scrollIntoView({ block: 'start', behavior: 'smooth' });
    }), 'Hojas'));
  pintarCompletas(inicio);
  pintar(encabezado('Hojas de producción'),
    h('div', { class: 'fila-botones', style: 'margin-top:0' }, h('button', { class: 'boton', type: 'button',
      onclick: () => { location.href = '/api/producciones.xlsx'; } }, 'Descargar Excel de todas las hojas')),
    mezclas.length ? [h('h2', null, 'Armar Mix Bread'),
      h('div', { class: 'rejilla' }, mezclas.map((p) => h('button', { class: 'mosaico mosaico-mix', type: 'button', onclick: () => intentar(() => vistaArmarMix(p.id)) },
        h('b', null, p.nombre), h('small', null, p.componentes.map((c) => c.nombre).join(' · ') || 'Sin panes definidos'))))] : null,
    ['por_completar', 'en_curso', 'completa'].map((estado) => {
      const g = grupo(estado);
      const cuantas = estado === 'completa' ? inicio.total_completas : g.length;
      return [h('h2', null, NOMBRE_ESTADO[estado] + ' ', h('span', { class: 'num' + (estado === 'por_completar' && g.length ? ' rojo' : '') }, cuantas)),
        textos[estado] ? h('p', { class: 'chico suave' }, textos[estado]) : null,
        estado === 'completa' ? cajaCompletas : g.length ? g.map(tarjetaProduccion) : h('p', { class: 'suave' }, 'Ninguna.')];
    }));
}

// El encargado arma un Mix Bread con unidades de lotes de pan ya producidos.
async function vistaArmarMix(productoId) {
  const r = await api('/mix/' + productoId);
  const elegidos = new Map();                 // origen_id -> { unidades }
  let enfocar = null;
  const paquetes = h('input', { type: 'text', inputmode: 'decimal', placeholder: 'ej. 20' });
  const etiquetas = h('input', { type: 'number', min: '0', value: '1' });
  const obs = h('input', { type: 'text' });
  const venceTxt = h('div', { class: 'nota verde', hidden: true });
  const todos = r.grupos.flatMap((g) => g.lotes);
  const contenido = h('div', null);
  // De cada pan se ven los 2 lotes más recientes; «Ver más» los muestra de 5 en 5 (hasta una semana antes).
  const VISIBLES = 2;
  const paginaDe = new Map();        // producto_id -> página (sin página = solo los 2 más recientes)
  const tarjetaPan = (l) => {
    const marcado = elegidos.has(l.id);
    const sinSaldo = l.disponible != null && l.disponible <= 0;
    const detalle = ['elaborado ' + dia(l.fecha), NOMBRE_ESTADO[l.estado].toLowerCase(),
      l.unidades ? 'producidos ' + l.unidades : null, l.unidades_lote ? 'para su lote ' + l.unidades_lote : null,
      l.usado_mix ? 'ya en Mix Bread ' + num(l.usado_mix) : null, 'vence ' + dia(l.fecha_vencimiento)].filter(Boolean).join(' · ');
    const tarjeta = h('div', { class: 'lote' + (sinSaldo ? '' : ' elegible') + (marcado ? ' marcado' : '') },
      sinSaldo ? null : h('div', { class: 'caja' }, marcado ? '✓' : ''),
      h('div', { class: 'datos' }, h('div', { class: 'codigo' }, l.lote_final), h('div', { class: 'chico suave' }, detalle),
        l.disponible != null ? h('span', { class: 'etq ' + (sinSaldo ? 'gris' : 'verde') }, 'Disponibles: ' + num(Math.max(l.disponible, 0)))
          : h('span', { class: 'etq aviso' }, 'Todavía no se anotó cuántos se produjeron')));
    if (!sinSaldo) tarjeta.addEventListener('click', () => {
      if (marcado) elegidos.delete(l.id); else { elegidos.set(l.id, { unidades: '' }); enfocar = l.id; }
      dibujar();
    });
    if (!marcado) return tarjeta;
    const e = elegidos.get(l.id);
    const campo = h('input', { type: 'text', inputmode: 'decimal', autocomplete: 'off', value: e.unidades, placeholder: 'ej. 10',
      oninput: (ev) => { e.unidades = ev.target.value; } });
    if (enfocar === l.id) setTimeout(() => campo.focus(), 50);
    return [tarjeta, h('div', { class: 'tarjeta', style: 'margin-top:-.3rem;border:2px solid var(--ok)' },
      h('label', { style: 'margin-top:0' }, '¿Cuántas unidades de este lote entran al Mix Bread?'), campo)];
  };
  const dibujar = () => {
    const y = window.scrollY;
    llenar(contenido, r.grupos.map((g) => {
      const pag = paginaDe.get(g.producto_id);
      const visibles = pag ? trozo(g.lotes, pag, 5) : g.lotes.slice(0, VISIBLES);
      const otrosElegidos = g.lotes.filter((l) => elegidos.has(l.id) && !visibles.includes(l)).length;
      return [h('h2', null, g.producto),
        g.lotes.length ? visibles.map(tarjetaPan) : h('p', { class: 'suave' }, 'No hay lotes de este pan de la última semana.'),
        !pag && g.lotes.length > VISIBLES ? h('button', { class: 'boton chico', type: 'button', onclick: () => { paginaDe.set(g.producto_id, 1); dibujar(); } }, 'Ver más')
          : pag ? [paginador(g.lotes.length, pag, 5, (n) => { paginaDe.set(g.producto_id, n); dibujar(); }, 'Lotes'),
            h('button', { class: 'boton chico', type: 'button', onclick: () => { paginaDe.delete(g.producto_id); dibujar(); } }, 'Ver menos')] : null,
        otrosElegidos ? h('p', { class: 'chico suave' }, otrosElegidos + ' lote(s) elegido(s) de ' + g.producto + ' en otras páginas.') : null];
    }));
    const fechas = todos.filter((l) => elegidos.has(l.id)).map((l) => l.fecha_vencimiento).sort();
    venceTxt.hidden = !fechas.length;
    venceTxt.textContent = fechas.length ? 'Este Mix Bread vencerá el ' + dia(fechas[0]) + ': la fecha más temprana de los panes elegidos.' : '';
    window.scrollTo(0, y);
    enfocar = null;
  };
  pintar(h('a', { class: 'volver', href: '#/hojas', onclick: (e) => { e.preventDefault(); vistaHojas(); } }, [h('span', { class: 'flecha-volver', 'aria-hidden': 'true' }, '←'), 'Volver a Hojas de producción']),
    h('h1', null, 'Armar ' + r.producto.nombre),
    h('p', { class: 'suave' }, 'Toca los lotes de pan que entran y escribe cuántas unidades de cada uno.'),
    contenido, venceTxt,
    h('div', { class: 'tarjeta' }, h('div', { class: 'en-linea' },
      h('div', null, h('label', null, 'Paquetes de Mix Bread armados'), paquetes), h('div', null, h('label', null, 'Etiquetas a imprimir'), etiquetas)),
    h('label', null, 'Observaciones'), obs),
    h('div', { class: 'fila-botones' }, h('button', { class: 'boton ok', type: 'button', onclick: () => intentar(async () => {
      if (!elegidos.size) throw new Error('Toca al menos un lote de pan.');
      const items = [...elegidos].map(([origen_id, e]) => ({ origen_id, unidades: e.unidades }));
      const hoja = await api('/mix/' + productoId, { items, unidades: paquetes.value, n_etiquetas: etiquetas.value, observaciones: obs.value });
      avisar('Mix Bread registrado: lote ' + hoja.lote_final, 'bien');
      location.hash = '#/hoja/' + hoja.id;
    }) }, 'Registrar Mix Bread')));
  dibujar();
}

function vistaHojaMix(p) {
  pintar(h('img', { class: 'logo-impreso', src: '/static/logo.png', alt: '' }),
    h('a', { class: 'volver no-imprimir', href: '#/hojas' }, '← Hojas de producción'),
    h('div', { class: 'tarjeta hoja-cab' },
      h('div', null, h('div', { class: 'chico suave' }, 'REGISTRO DE ARMADO'), h('h1', null, p.producto),
        h('div', { class: 'lote-final' }, p.lote_final),
        h('div', { class: 'chico suave' }, 'Armado ' + dia(p.fecha) + ' · Vence ' + dia(p.fecha_vencimiento) + ' (la fecha más temprana de sus panes)')),
      h('div', { style: 'text-align:right' }, h('span', { class: 'etq ' + CLASE_ESTADO[p.estado] }, NOMBRE_ESTADO[p.estado]),
        h('div', { class: 'chico suave' }, 'Armó ' + hora(p.iniciado_en), h('br'), p.iniciado_por || ''))),
    h('div', { class: 'tabla-caja' }, h('table', null,
      h('thead', null, h('tr', null, ['Pan', 'Lote', 'Unidades', 'Elaboración', 'Vence'].map((t) => h('th', null, t)))),
      h('tbody', null, p.componentes.map((c) => h('tr', null, h('td', null, h('b', null, c.producto)),
        h('td', { class: 'mono' }, h('a', { href: '#/hoja/' + c.origen_id }, h('b', null, c.lote_final))),
        h('td', null, num(c.unidades)), h('td', null, dia(c.fecha)), h('td', null, dia(c.fecha_vencimiento))))))),
    h('div', { class: 'tarjeta' }, h('p', null, h('b', null, 'Paquetes armados: '), p.unidades || '—', ' · ', h('b', null, 'Etiquetas: '), String(p.n_etiquetas)),
      p.observaciones ? h('p', null, h('b', null, 'Observaciones: '), p.observaciones) : null),
    h('div', { class: 'fila-botones no-imprimir' }, h('button', { class: 'boton', type: 'button', onclick: () => window.print() }, 'Imprimir'),
      p.estado !== 'anulada' ? h('button', { class: 'boton chico peligro', type: 'button', onclick: () => intentar(async () => {
        const motivo = await pedirTexto('Anular este Mix Bread', 'Motivo (las unidades de los panes vuelven a quedar disponibles)');
        if (!motivo) return;
        await api('/producciones/' + p.id + '/anular', { motivo }); avisar('Mix Bread anulado'); vistaHoja(p.id);
      }) }, 'Anular') : null));
}

async function vistaHoja(id) {
  const p = await api('/producciones/' + id);
  if (p.armado) return vistaHojaMix(p);
  const editable = p.estado === 'por_completar';      // en curso: solo lectura hasta que cocina avise que termino
  const sub = !!p.produce_ingrediente_id;              // subreceta (relleno, glacé, masa madre): sin horneado
  const unidadesSub = sub ? (await api('/bodega/catalogo')).unidades : [];
  const campos = {};
  const recoger = () => ({
    unidades: campos.unidades.value, unidades_lote: sub ? '' : campos.unidades_lote.value, horneado: sub ? '' : campos.horneado.value,
    observaciones: campos.observaciones.value, n_etiquetas: campos.n_etiquetas.value,
    unidad_sub: sub ? campos.unidad_sub.value : undefined,
    lineas: p.lineas.map((li) => ({ id: li.id, cantidad: campos['c' + li.id].value,
      no_utilizado: campos['n' + li.id] ? campos['n' + li.id].checked : !!li.no_utilizado })),
  });
  const guardar = (cerrar) => intentar(async () => {
    if (cerrar) {
      // Para cerrar la hoja, todo lleno menos observaciones: se marcan en rojo los campos vacíos.
      const obligatorios = sub ? [] : [campos.unidades, campos.unidades_lote, campos.horneado, campos.n_etiquetas];
      const vacios = obligatorios.filter((c) => c && (!String(c.value || '').trim() || (c === campos.n_etiquetas && !Number(c.value))));
      document.querySelectorAll('.campo-vacio').forEach((c) => c.classList.remove('campo-vacio'));
      vacios.forEach((c) => c.classList.add('campo-vacio'));
      if (vacios.length) { vacios[0].focus(); throw new Error('Para cerrar la hoja llena todos los campos (las observaciones son opcionales).'); }
    }
    await api('/producciones/' + id + '/guardar', Object.assign(recoger(), { cerrar }));
    avisar(cerrar ? 'Hoja completa' : 'Guardado', 'bien'); vistaHoja(id);
  });

  const agregarLote = (li) => intentar(async () => {
    await api('/producciones/' + id + '/guardar', recoger());
    const lotes = (await api('/lotes?donde=todos&ingrediente_id=' + li.ingrediente_id)).slice(0, 25);
    const elegido = await modal('Agregar lote a ' + li.etiqueta,
      h('div', null, h('p', { class: 'suave' }, 'Solo se puede elegir de la lista; no se escribe a mano.'),
        lotes.length ? lotes.map((l) => tarjetaLote(l, { elegible: true,
          alTocar: (e) => { const v = e.currentTarget.closest('.velo'); v.dataset.elegido = l.id; v.querySelector('.principal').click(); } }))
          : h('p', null, 'No hay lotes de este ingrediente. Regístralo primero en Bodega secundaria o Cocina.')),
      [{ texto: 'Agregar el lote tocado', clase: 'principal', valor: () => Number(document.querySelector('.velo').dataset.elegido) || null },
        { texto: 'Cancelar', valor: null }]);
    if (elegido) { await api('/producciones/' + id + '/agregar_lote', { linea_id: li.id, lote_id: elegido }); vistaHoja(id); }
  });
  const quitarLote = (l) => intentar(async () => {
    const motivo = await pedirTexto('Quitar el lote ' + l.lote, '¿Por qué se quita? (queda registrado)');
    if (!motivo) return;
    await api('/producciones/' + id + '/guardar', recoger());
    await api('/producciones/' + id + '/quitar_lote', { pl_id: l.pl_id, motivo }); vistaHoja(id);
  });

  const filaLinea = (li) => {
    const vigentes = li.lotes.filter((l) => !l.quitado_en);
    // Cantidad utilizada: solo números. Si se marca «No se utilizó», el campo se vacía y se bloquea.
    const cant = h('input', { type: 'text', inputmode: 'decimal', autocomplete: 'off', value: li.no_utilizado ? '' : (li.cantidad || ''),
      placeholder: li.no_utilizado ? '' : 'ej. 10', disabled: !editable || !!li.no_utilizado,
      oninput: (e) => { const v = soloNumero(e.target.value); if (v !== e.target.value) e.target.value = v; } });
    campos['c' + li.id] = cant;
    const casillaNoUsado = () => h('input', { type: 'checkbox', checked: !!li.no_utilizado, onchange: (e) => {
      cant.disabled = e.target.checked; if (e.target.checked) { cant.value = ''; cant.placeholder = ''; } else { cant.placeholder = 'ej. 10'; cant.focus(); } } });
    const etiquetaNoUsado = () => h('label', { style: 'display:inline-flex;gap:.4rem;align-items:center;margin:0 0 0 .8rem;font-weight:400;white-space:nowrap' },
      campos['n' + li.id], 'No se utilizó');
    let celdaLotes;
    if (li.tipo === 'no_aplica') {
      if (editable) campos['n' + li.id] = casillaNoUsado();
      celdaLotes = h('div', null, h('span', { class: 'suave' }, li.no_utilizado ? 'NO SE UTILIZÓ' : 'No aplica'),
        editable ? h('div', { class: 'no-imprimir', style: 'margin-top:.3rem' }, etiquetaNoUsado()) : null);
    } else {
      if (!vigentes.length && editable) campos['n' + li.id] = casillaNoUsado();
      celdaLotes = h('div', null,
        li.lotes.map((l) => h('span', { class: 'chip-lote' + (l.quitado_en ? ' quitado' : ''), title: l.quitado_en ? 'Quitado: ' + l.motivo
          : [l.proveedor, l.fecha_ingreso && 'ingresó ' + dia(l.fecha_ingreso)].filter(Boolean).join(' · ') }, l.lote,
        l.origen_lote === 'manual' && !l.quitado_en ? h('span', { class: 'etq rojo' }, 'a mano') : null,
        editable && !l.quitado_en ? h('button', { type: 'button', class: 'no-imprimir', 'aria-label': 'Quitar lote', onclick: () => quitarLote(l) }, '×') : null)),
        !vigentes.length && li.no_utilizado ? h('span', { class: 'suave' }, 'NO SE UTILIZÓ') : null,
        li.falta_lote ? h('div', { class: 'etq rojo' }, 'Falta el lote: ábrelo en Cocina o agrégalo') : null,
        editable ? h('div', { class: 'no-imprimir', style: 'margin-top:.3rem' },
          h('button', { type: 'button', class: 'boton chico', onclick: () => agregarLote(li) }, '+ lote'),
          campos['n' + li.id] ? etiquetaNoUsado() : null) : null);
    }
    return h('tr', { class: li.falta_lote ? 'falta' : '' }, h('td', null, h('b', null, li.etiqueta)), h('td', null, celdaLotes),
      h('td', { style: 'min-width:120px' }, campos['c' + li.id]));
  };

  const soloNum = (e) => { const v = soloNumero(e.target.value); if (v !== e.target.value) e.target.value = v; };
  campos.unidades = h('input', { type: 'text', value: p.unidades, disabled: !editable, placeholder: sub ? 'Opcional, ej. 5' : 'ej. 240',
    inputmode: sub ? 'decimal' : null, oninput: sub ? soloNum : null });
  campos.unidad_sub = h('select', { disabled: !editable }, h('option', { value: '' }, '—'),
    [...new Set([...unidadesSub, p.unidad_sub].filter(Boolean))].map((u) => h('option', { value: u, selected: u === p.unidad_sub }, u)));
  campos.unidades_lote = h('input', { type: 'text', value: p.unidades_lote || '', disabled: !editable, placeholder: 'ej. 200' });
  campos.horneado = h('input', { type: 'text', value: p.horneado, disabled: !editable, placeholder: 'ej. 180 °C · 18 min' });
  campos.n_etiquetas = h('input', { type: 'number', min: '0', value: p.n_etiquetas, disabled: !editable });
  campos.observaciones = h('textarea', { disabled: !editable }, p.observaciones);

  // Documento impreso: se arma con lo que está escrito en pantalla en el momento de imprimir.
  const impreso = h('div', { class: 'solo-imprimir hoja-impresa' });
  const armarImpreso = () => {
    const val = (c) => (c ? String(c.value || '').trim() : '');
    const cuando = (t, quien) => (t ? dia(t.slice(0, 10)) + ' ' + t.slice(11, 16) + (quien ? ' · ' + quien : '') : '—');
    const celda = (etq, valor) => h('td', null, h('div', { class: 'imp-etq' }, etq), h('div', { class: 'imp-valor' }, valor || '—'));
    const lotesDe = (li) => {
      const noUsado = campos['n' + li.id] ? campos['n' + li.id].checked : li.no_utilizado;
      if (li.tipo === 'no_aplica') return noUsado ? 'No se utilizó' : 'No aplica';
      const vig = li.lotes.filter((l) => !l.quitado_en).map((l) => l.lote);
      return vig.length ? vig.join(' · ') : (campos['n' + li.id] ? campos['n' + li.id].checked : li.no_utilizado) ? 'No se utilizó' : '—';
    };
    llenar(impreso,
      h('table', { class: 'imp-cabecera' }, h('tbody', null, h('tr', null,
        h('td', { class: 'imp-logo' }, h('img', { src: '/static/logo.png', alt: 'San Francisco FoodService' })),
        h('td', { class: 'imp-titulo' }, h('div', null, 'REGISTRO DE PRODUCCIÓN'), h('div', { class: 'imp-producto' }, p.producto)),
        h('td', { class: 'imp-lote' }, h('div', { class: 'imp-etq' }, 'Lote'), h('div', { class: 'imp-codigo' }, p.lote_final))))),
      h('table', { class: 'imp-datos' }, h('tbody', null,
        h('tr', null, celda('Fecha de elaboración', dia(p.fecha)), celda('Fecha de vencimiento', dia(p.fecha_vencimiento)),
          celda('Estado de la hoja', NOMBRE_ESTADO[p.estado]), celda('Etiquetas a imprimir', val(campos.n_etiquetas))),
        h('tr', null, celda('Inicio de masa', cuando(p.iniciado_en, p.iniciado_por)),
          celda(sub ? '—' : 'Masa lista', sub ? '' : cuando(p.masa_lista_en, p.masa_lista_por)),
          celda('Fin de producción', cuando(p.terminado_en, p.terminado_por)), celda('Hoja cerrada', cuando(p.completado_en, p.completado_por))))),
      h('div', { class: 'imp-sub' }, 'Ingredientes'),
      h('table', { class: 'imp-tabla' },
        h('thead', null, h('tr', null, h('th', { style: 'width:7%' }, 'N.º'), h('th', { style: 'width:35%' }, 'Ingrediente'),
          h('th', { style: 'width:36%' }, 'Lote(s)'), h('th', { style: 'width:22%' }, 'Cantidad utilizada'))),
        h('tbody', null, p.lineas.map((li, i) => h('tr', null, h('td', { class: 'centro' }, String(i + 1)), h('td', null, li.etiqueta),
          h('td', { class: li.lotes.some((l) => !l.quitado_en) ? 'mono' : '' }, lotesDe(li)), h('td', { class: 'centro' }, val(campos['c' + li.id]) || '—'))))),
      h('div', { class: 'imp-sub' }, 'Resultado'),
      h('table', { class: 'imp-datos' }, h('tbody', null, sub
        ? h('tr', null, celda('Cantidad total producida', val(campos.unidades)), celda('Unidad', val(campos.unidad_sub)))
        : h('tr', null, celda('Unidades producidas (total)', val(campos.unidades)), celda('Unidades para el lote', val(campos.unidades_lote)),
          celda('Horneado (temperatura y tiempo)', val(campos.horneado))))),
      p.usado_mix.length ? h('p', { class: 'imp-nota' }, 'Usadas en Mix Bread: ' + num(p.unidades_mix) + ' (' +
        p.usado_mix.map((u) => u.producto + ' ' + u.lote_final + ': ' + num(u.unidades)).join(', ') + ').') : null,
      h('div', { class: 'imp-sub' }, 'Observaciones'),
      h('div', { class: 'imp-observaciones' }, val(campos.observaciones)),
      h('table', { class: 'imp-firmas' }, h('tbody', null, h('tr', null,
        h('td', null, h('div', { class: 'imp-linea' }), 'Elaborado por', h('div', { class: 'imp-etq' }, p.iniciado_por || '')),
        h('td', null, h('div', { class: 'imp-linea' }), 'Revisado por', h('div', { class: 'imp-etq' }, p.completado_por || ''))))),
      h('div', { class: 'imp-pie' }, 'TrazaPanUSFQ · San Francisco FoodService · impreso el ' + new Date().toLocaleString('es-EC')));
  };
  window.onbeforeprint = () => { if (document.body.contains(impreso)) armarImpreso(); };

  pintar(impreso, h('div', { class: 'no-imprimir' },
    h('a', { class: 'volver', href: '#/hojas' }, h('span', { class: 'flecha-volver', 'aria-hidden': 'true' }, '←'), 'Volver a Hojas de producción'),
    h('div', { class: 'tarjeta hoja-cab' },
      h('div', null, h('div', { class: 'chico suave' }, 'REGISTRO DE PRODUCCIÓN'), h('h1', null, p.producto),
        h('div', { class: 'lote-final' }, p.lote_final),
        h('div', { class: 'chico suave' }, 'Elaboración ' + dia(p.fecha) + ' · Vence ' + dia(p.fecha_vencimiento))),
      h('div', { style: 'text-align:right' }, h('span', { class: 'etq ' + CLASE_ESTADO[p.estado] }, NOMBRE_ESTADO[p.estado]),
        h('div', { class: 'chico suave' }, 'Inició ' + hora(p.iniciado_en), h('br'), p.iniciado_por || '',
          p.terminado_en ? [h('br'), 'Terminó ' + hora(p.terminado_en)] : null,
          p.completado_en ? [h('br'), 'Cerró ' + hora(p.completado_en) + ' · ' + (p.completado_por || '')] : null))),
    p.estado === 'en_curso' ? h('div', { class: 'nota verde no-imprimir' }, 'Se está produciendo. Si en cocina se abre otro lote de estos ingredientes, se agrega solo a esta hoja. Cuando cocina avise que terminó, se podrán llenar las cantidades.') : null,
    p.faltantes.length ? h('div', { class: 'nota rojo no-imprimir' }, 'Falta el lote de: ' + p.faltantes.join(', ') + '. La hoja no se puede cerrar así.') : null,
    h('div', { class: 'tabla-caja' }, h('table', null,
      h('thead', null, h('tr', null, h('th', null, 'Ingrediente'), h('th', null, 'Lote(s)'), h('th', null, 'Cantidad utilizada'))),
      h('tbody', null, p.lineas.map(filaLinea)))),
    h('div', { class: 'tarjeta' },
      sub ? h('div', { class: 'en-linea' },
        h('div', null, h('label', null, 'Cantidad total producida'), campos.unidades),
        h('div', null, h('label', null, 'Unidad'), campos.unidad_sub),
        h('div', null, h('label', null, 'Etiquetas a imprimir'), campos.n_etiquetas))
      : h('div', { class: 'en-linea' },
        h('div', null, h('label', null, 'Unidades producidas (total)'), campos.unidades),
        h('div', null, h('label', null, 'Unidades para el lote de ' + p.producto.toLowerCase()), campos.unidades_lote),
        h('div', null, h('label', null, 'Horneado (temperatura y tiempo)'), campos.horneado),
        h('div', null, h('label', null, 'Etiquetas a imprimir'), campos.n_etiquetas)),
      p.usado_mix.length ? h('p', { class: 'chico' }, h('b', null, 'Usadas en Mix Bread: ' + num(p.unidades_mix) + '. '),
        p.usado_mix.map((u, i) => [i ? ', ' : '', h('a', { href: '#/hoja/' + u.produccion_id }, u.producto + ' ' + u.lote_final), ' (' + num(u.unidades) + ')'])) : null,
      h('label', null, 'Observaciones'), campos.observaciones),
    h('div', { class: 'fila-botones no-imprimir' },
      p.estado === 'en_curso' ? h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => {
        if (!await confirmar('Marcar como terminada', 'Normalmente lo avisa cocina. Úsalo solo si cocina olvidó tocar «Terminó».', 'Marcar terminada')) return;
        await api('/producciones/' + id + '/terminar', {}); avisar('Producción terminada', 'bien'); vistaHoja(id);
      }) }, 'Cocina olvidó avisar: marcar terminada') : null,
      editable ? h('button', { class: 'boton', type: 'button', onclick: () => guardar(false) }, 'Guardar') : null,
      p.estado === 'por_completar' ? h('button', { class: 'boton ok', type: 'button', onclick: () => guardar(true) }, 'Guardar y cerrar hoja') : null,
      p.estado === 'completa' ? h('button', { class: 'boton', type: 'button', onclick: () => intentar(async () => {
        await api('/producciones/' + id + '/reabrir', {}); vistaHoja(id);
      }) }, 'Reabrir para corregir') : null,
      h('button', { class: 'boton', type: 'button', onclick: () => window.print() }, 'Imprimir'),
      p.estado !== 'anulada' ? h('button', { class: 'boton chico peligro', type: 'button', onclick: () => intentar(async () => {
        const motivo = await pedirTexto('Anular esta producción', 'Motivo (queda registrado)');
        if (!motivo) return;
        await api('/producciones/' + id + '/anular', { motivo }); location.hash = '#/produccion';
      }) }, 'Anular') : null)));
  armarImpreso();
}

// ------------------------------------------------------------------ etiquetas
// Rango de fechas de los periodos rápidos de Etiquetas (la semana empieza el lunes).
function rangoPeriodo(periodo, hoy) {
  const d = new Date(hoy + 'T12:00:00');
  const iso = (x) => x.getFullYear() + '-' + String(x.getMonth() + 1).padStart(2, '0') + '-' + String(x.getDate()).padStart(2, '0');
  const mas = (x, n) => { const y = new Date(x); y.setDate(y.getDate() + n); return y; };
  const lunes = mas(d, -((d.getDay() + 6) % 7));
  if (periodo === 'semana') return [iso(lunes), hoy];
  if (periodo === 'pasada') return [iso(mas(lunes, -7)), iso(mas(lunes, -1))];
  if (periodo === 'mes') return [hoy.slice(0, 8) + '01', hoy];
  return [hoy, hoy];
}

const filtroEtiquetas = {};          // recuerda el periodo elegido mientras la app está abierta

async function vistaEtiquetas() {
  const estado = await api('/estado');
  const f = filtroEtiquetas;
  if (!f.periodo) f.periodo = 'hoy';
  if (f.periodo !== 'otro') [f.desde, f.hasta] = rangoPeriodo(f.periodo, estado.hoy);
  const lista = await api('/etiquetas?desde=' + f.desde + '&hasta=' + f.hasta);
  // Elegidas: todos los panes; las subrecetas solo si se marcan.
  const elegidas = new Set(lista.filter((d) => !d.es_subreceta).map((d) => d.id));
  const periodo = segmento([['hoy', 'Hoy'], ['semana', 'Esta semana'], ['pasada', 'Semana pasada'], ['mes', 'Este mes'], ['otro', 'Otras fechas']],
    f.periodo, (v) => { f.periodo = v; vistaEtiquetas(); });
  const cambiarFecha = (campo) => (e) => { f[campo] = e.target.value; if (f.desde > f.hasta) f[campo === 'desde' ? 'hasta' : 'desde'] = e.target.value; vistaEtiquetas(); };
  const fechas = f.periodo === 'otro' ? h('div', { class: 'en-linea' },
    h('div', null, h('label', null, 'Desde'), h('input', { type: 'date', value: f.desde, max: estado.hoy, onchange: cambiarFecha('desde') })),
    h('div', null, h('label', null, 'Hasta'), h('input', { type: 'date', value: f.hasta, max: estado.hoy, onchange: cambiarFecha('hasta') }))) : null;
  const botonExcel = h('button', { class: 'boton principal', type: 'button', onclick: () => bajar('xlsx') });
  const botonCsv = h('button', { class: 'boton', type: 'button', onclick: () => bajar('csv') }, 'Descargar CSV');
  const casillas = [];
  const todas = h('input', { type: 'checkbox', title: 'Marcar todas', onchange: (e) => {
    lista.forEach((d) => (e.target.checked ? elegidas.add(d.id) : elegidas.delete(d.id))); casillas.forEach((c) => { c.checked = e.target.checked; }); contar(); } });
  const contar = () => {
    const n = lista.filter((d) => elegidas.has(d.id)).reduce((t, d) => t + Number(d.n_etiquetas || 0), 0);
    botonExcel.textContent = elegidas.size ? 'Descargar Excel para Zebra (' + elegidas.size + ' lote' + (elegidas.size > 1 ? 's' : '') + ', ' + n + ' etiqueta' + (n === 1 ? '' : 's') + ')' : 'Marca los lotes a imprimir';
    botonExcel.disabled = botonCsv.disabled = !elegidas.size;
    todas.checked = lista.length > 0 && elegidas.size === lista.length;
  };
  const bajar = (ext) => {
    location.href = '/api/etiquetas.' + ext + '?desde=' + f.desde + '&hasta=' + f.hasta + '&subrecetas=1&ids=' + [...elegidas].join(',');
  };
  const varios = f.desde !== f.hasta;
  pintar(encabezado('Etiquetas'),
    periodo.el, fechas,
    lista.length ? h('div', { class: 'tabla-caja', style: 'margin-top:1rem' }, h('table', null,
      h('thead', null, h('tr', null, h('th', { style: 'width:2rem' }, todas), ['Lote', 'Producto', 'FE', 'FV', 'Etiquetas', 'Hoja'].map((t) => h('th', null, t)))),
      h('tbody', null, lista.map((d, k) => {
        const c = h('input', { type: 'checkbox', checked: elegidas.has(d.id), onchange: (e) => { e.target.checked ? elegidas.add(d.id) : elegidas.delete(d.id); contar(); } });
        casillas.push(c);
        const separa = varios && k > 0 && lista[k - 1].fecha !== d.fecha;
        return h('tr', { class: separa ? 'otro-dia' : '' }, h('td', null, c), h('td', { class: 'mono' }, h('b', null, d.lote_final)),
          h('td', null, d.producto, d.es_subreceta ? [' ', h('span', { class: 'etq gris' }, 'subreceta')] : null), h('td', { style: 'white-space:nowrap' }, d.fe), h('td', { style: 'white-space:nowrap' }, d.fv),
          h('td', null, d.n_etiquetas), h('td', null, h('a', { href: '#/hoja/' + d.id }, h('span', { class: 'etq ' + CLASE_ESTADO[d.estado] }, NOMBRE_ESTADO[d.estado]))));
      }))))
      : h('p', { class: 'suave', style: 'margin-top:1rem' }, 'No hay producciones en ese periodo.'),
    lista.length ? h('div', { class: 'fila-botones' }, botonExcel, botonCsv) : null,
    h('div', { class: 'tarjeta chico', style: 'margin-top:1.4rem;display:flex;justify-content:space-between;align-items:center;gap:.8rem;flex-wrap:wrap' },
      h('div', null, h('b', null, 'Todos los lotes desde que se usa la app'), h('div', { class: 'suave' }, 'Excel con cada lote, su producto, fechas, etiquetas y estado.')),
      h('button', { class: 'boton', type: 'button', onclick: () => { location.href = '/api/etiquetas.xlsx?todo=1'; } }, 'Descargar historial')),
    h('div', { class: 'tarjeta chico' }, h('b', null, 'Cómo imprimir sin volver a escribir los códigos'),
      h('p', null, 'En ZebraDesigner Professional, conecta la plantilla de la etiqueta a este archivo como base de datos (columnas LOTE, PRODUCTO, FE, FV, CANTIDAD) e imprime todos los registros. La columna CANTIDAD indica cuántas etiquetas de cada lote.'),
      h('p', null, 'Con la edición gratuita (Essentials) no hay conexión a base de datos: se imprime leyendo esta pantalla, sin pasar por la hoja de papel.')));
  contar();
}


// ------------------------------------------------------------------ despachos
// Cada lote de pan con la hoja cerrada queda en el congelador con sus paquetes (1 etiqueta = 1 paquete).
// El encargado de producción registra a qué destino sale cada lote: así la consulta sabe a quién avisar en un retiro.
const nDespacho = (id) => 'N.º ' + String(id).padStart(4, '0');
const plural = (n, uno, varios) => n + ' ' + (n === 1 ? uno : varios);
let despachoEnCurso = { destino: null, cantidades: new Map(), fecha: '', obs: '' };

async function editarDestino(d) {
  d = d || {};
  const campo = (valor, opc) => h('input', Object.assign({ type: 'text', value: valor || '' }, opc || {}));
  const c = {
    nombre: campoMayusculas(d.nombre), ruc: campo(d.ruc, { inputmode: 'numeric', placeholder: '10 o 13 números (opcional)' }),
    contacto: campo(d.contacto, { placeholder: 'Quién recibe (opcional)' }), telefono: campo(d.telefono, { inputmode: 'tel', placeholder: 'Opcional' }),
    correo: campo(d.correo, { type: 'email', placeholder: 'Opcional' }), direccion: campo(d.direccion, { placeholder: 'Opcional' }),
    notas: campo(d.notas, { placeholder: 'Opcional' }),
  };
  c.ruc.addEventListener('input', (e) => { e.target.value = e.target.value.replace(/\D/g, '').slice(0, 13); });
  setTimeout(() => c.nombre.focus(), 50);
  const r = await modal(d.id ? 'Editar destino' : 'Nuevo destino', h('div', { class: 'form-destino' },
    h('label', null, 'Nombre del destino o cliente *'), c.nombre,
    h('div', { class: 'dos-col' },
      h('div', null, h('label', null, 'RUC o cédula'), c.ruc), h('div', null, h('label', null, 'Teléfono'), c.telefono)),
    h('div', { class: 'dos-col' },
      h('div', null, h('label', null, 'Persona de contacto'), c.contacto), h('div', null, h('label', null, 'Correo'), c.correo)),
    h('label', null, 'Dirección'), c.direccion, h('label', null, 'Notas'), c.notas),
  [{ texto: d.id ? 'Guardar cambios' : 'Agregar destino', clase: 'principal',
    accion: () => api(d.id ? '/destinos/' + d.id : '/destinos', Object.fromEntries(Object.entries(c).map(([k, el]) => [k, el.value.trim()]))) },
    { texto: 'Cancelar', valor: null }]);
  return r || null;
}

function datosDestino(d) {
  return [d.ruc && 'RUC/CI ' + d.ruc, d.contacto, d.telefono, d.correo, d.direccion].filter(Boolean).join(' · ');
}

async function bajaCongelador(l, motivos) {
  const opciones = Object.entries(motivos).filter(([k]) => k !== 'ANTERIOR A LA APP');
  const motivo = segmento(opciones, l.semaforo === 'rojo' ? 'CADUCADO' : '');
  const cant = h('input', { type: 'text', inputmode: 'numeric', value: String(l.saldo), oninput: (e) => { e.target.value = e.target.value.replace(/\D/g, ''); } });
  const comentario = h('input', { type: 'text', placeholder: 'Qué pasó (opcional)' });
  const d = await modal('Dar de baja en el congelador', h('div', null,
    h('p', { style: 'margin-top:0' }, h('b', null, l.producto), ' | ', h('b', { class: 'mono' }, l.lote_final), ' ', semaforo(l)),
    h('label', null, 'Motivo'), motivo.el,
    h('label', null, '¿Cuántos paquetes?'), cant, h('div', { class: 'chico suave' }, 'Quedan ' + plural(l.saldo, 'paquete', 'paquetes') + '.'),
    h('label', null, 'Comentario'), comentario),
  [{ texto: 'Dar de baja', clase: 'peligro-lleno', accion: async () => {
      if (!motivo.valor) throw new Error('Elige el motivo.');
      await api('/congelador/bajas', { produccion_id: l.produccion_id, motivo: motivo.valor, paquetes: cant.value, comentario: comentario.value.trim() });
      return true;
    } },
    { texto: 'Cancelar', valor: null }]);
  if (!d) return false;
  avisar('Baja registrada', 'bien');
  return true;
}

async function vistaDespachos(pestana, id) {
  if (pestana === 'guia') return vistaGuiaDespacho(Number(id));
  pestana = ['historial', 'destinos'].includes(pestana) ? pestana : 'despachar';
  const ir = (p) => { history.replaceState(null, '', '#/despachos/' + p); vistaDespachos(p); };
  const cuerpoP = h('div', null, h('p', { class: 'suave' }, 'Cargando…'));
  pintar(encabezado('Despachos', 'A dónde sale cada lote de pan del congelador.'),
    h('div', { class: 'pestanas' }, [['despachar', 'Despachar'], ['historial', 'Historial'], ['destinos', 'Destinos']].map(([p, nombre]) =>
      h('button', { type: 'button', class: p === pestana ? 'activa' : '', onclick: () => ir(p) }, nombre))), cuerpoP);
  if (pestana === 'despachar') return panelDespachar(cuerpoP);
  if (pestana === 'historial') return panelHistorialDespachos(cuerpoP, 1);
  return panelDestinos(cuerpoP);
}

async function panelDespachar(caja) {
  const [estado, destinos, cong] = await Promise.all([api('/estado'), api('/destinos'), api('/congelador')]);
  const st = despachoEnCurso;
  if (!st.fecha || st.fecha > estado.hoy) st.fecha = estado.hoy;
  if (st.destino && !destinos.some((d) => d.id === st.destino)) st.destino = null;
  for (const k of [...st.cantidades.keys()]) if (!cong.lotes.some((l) => l.produccion_id === k)) st.cantidades.delete(k);
  let filtro = '';
  const cajaLotes = h('div', null);
  const boton = h('button', { class: 'boton principal grande', type: 'button', onclick: () => intentar(registrar) });
  const total = () => [...st.cantidades.values()].reduce((t, v) => t + (Number(v) || 0), 0);
  const actualizarBoton = () => {
    const n = total();
    const dest = destinos.find((d) => d.id === st.destino);
    boton.disabled = !dest || !n;
    boton.textContent = !dest ? 'Elige a dónde va' : !n ? 'Escribe los paquetes que salen' : 'Registrar despacho a ' + dest.nombre + ' (' + plural(n, 'paquete', 'paquetes') + ')';
  };
  const fecha = h('input', { type: 'date', value: st.fecha, max: estado.hoy, onchange: (e) => { st.fecha = e.target.value; } });
  const obs = h('input', { type: 'text', value: st.obs, placeholder: 'Opcional (ej. guía o factura, quién retiró)', oninput: (e) => { st.obs = e.target.value; } });

  const filaLote = (l) => {
    const campo = h('input', { type: 'text', inputmode: 'numeric', class: 'campo-paquetes', placeholder: '0', value: st.cantidades.get(l.produccion_id) || '',
      'aria-label': 'Paquetes de ' + l.lote_final,
      oninput: (e) => {
        let v = e.target.value.replace(/\D/g, '');
        if (v && Number(v) > l.saldo) { v = String(l.saldo); avisar('Del lote ' + l.lote_final + ' solo quedan ' + l.saldo + ' paquetes'); }
        e.target.value = v;
        if (v && Number(v) > 0) st.cantidades.set(l.produccion_id, v); else st.cantidades.delete(l.produccion_id);
        tarjeta.classList.toggle('marcado', st.cantidades.has(l.produccion_id));
        actualizarBoton();
      } });
    const todo = h('button', { class: 'boton chico', type: 'button', onclick: () => { campo.value = String(l.saldo); campo.dispatchEvent(new Event('input')); } }, 'Todo');
    const tarjeta = h('div', { class: 'lote lote-cong borde-' + l.semaforo + (st.cantidades.has(l.produccion_id) ? ' marcado' : '') },
      h('div', { class: 'datos' },
        h('div', { class: 'titulo-lote' }, h('b', null, l.producto), h('span', { class: 'separador' }, '|'), h('b', { class: 'mono' }, l.lote_final)),
        h('div', { class: 'chico' }, 'Quedan ', h('b', null, plural(l.saldo, 'paquete', 'paquetes')),
          h('span', { class: 'suave' }, ' de ' + l.paquetes + ' · elaborado ' + dia(l.fecha))),
        h('div', null, semaforo(l))),
      h('div', { class: 'salen' }, h('label', null, 'Salen'), h('div', { class: 'salen-fila' }, campo, todo)),
      h('div', { class: 'acciones-lote' }, h('button', { class: 'boton chico peligro', type: 'button', onclick: () => intentar(async () => {
        if (await bajaCongelador(l, cong.motivos)) { st.cantidades.delete(l.produccion_id); vistaDespachos('despachar'); } }) }, 'Dar de baja')));
    return tarjeta;
  };
  const pintarLotes = () => {
    const n = normal(filtro);
    const visibles = cong.lotes.filter((l) => !n || normal(l.producto + ' ' + l.lote_final).includes(n));
    llenar(cajaLotes,
      cong.lotes.length ? leyendaSemaforo(cong.lotes) : null,
      cong.lotes.length > 6 ? buscadorLotes(cajaLotes, filtro, (v) => { filtro = v; pintarLotes(); }) : null,
      cong.lotes.length ? (visibles.length ? visibles.map(filaLote) : h('p', { class: 'suave' }, 'Ningún lote coincide con la búsqueda.'))
        : h('p', { class: 'suave' }, 'No hay pan en el congelador. Los lotes aparecen aquí cuando se cierra su hoja de producción.'));
  };
  const tarjetaDestino = (d) => h('button', { type: 'button', class: 'mosaico destino' + (st.destino === d.id ? ' elegido' : ''),
    onclick: () => { st.destino = st.destino === d.id ? null : d.id; panelDespachar(caja); } },
  h('b', null, d.nombre), datosDestino(d) ? h('small', null, datosDestino(d)) : null);

  async function registrar() {
    const dest = destinos.find((d) => d.id === st.destino);
    const items = [...st.cantidades].map(([produccion_id, paquetes]) => ({ produccion_id, paquetes }));
    const lotes = items.map((it) => ({ ...cong.lotes.find((l) => l.produccion_id === it.produccion_id), sale: it.paquetes }));
    const ok = await modal('Confirmar despacho', h('div', null,
      h('p', { style: 'margin-top:0' }, 'Destino: ', h('b', null, dest.nombre), h('br'), 'Fecha: ', h('b', null, dia(st.fecha))),
      h('div', { class: 'tabla-caja' }, h('table', null, h('thead', null, h('tr', null, ['Lote', 'Producto', 'Paquetes'].map((t) => h('th', null, t)))),
        h('tbody', null, lotes.map((l) => h('tr', null, h('td', { class: 'mono' }, h('b', null, l.lote_final)), h('td', null, l.producto), h('td', null, l.sale))),
          h('tr', null, h('td', { colspan: '2' }, h('b', null, 'Total')), h('td', null, h('b', null, total()))))))),
    [{ texto: 'Sí, registrar', clase: 'principal', valor: true }, { texto: 'Cancelar', valor: null }]);
    if (!ok) return;
    const r = await api('/despachos', { destino_id: dest.id, fecha: st.fecha, observaciones: st.obs.trim(), items });
    despachoEnCurso = { destino: null, cantidades: new Map(), fecha: '', obs: '' };
    avisar('Despacho ' + nDespacho(r.id) + ' registrado', 'bien');
    location.hash = '#/despachos/guia/' + r.id;
  }

  const elegido = destinos.find((d) => d.id === st.destino);
  llenar(caja,
    h('h2', { style: 'margin-top:.4rem' }, '1. ¿A dónde va?'),
    h('div', { class: 'rejilla rejilla-destinos' }, destinos.map(tarjetaDestino),
      h('button', { type: 'button', class: 'mosaico destino nuevo', onclick: () => intentar(async () => {
        const d = await editarDestino(); if (d) { st.destino = d.id; avisar('Destino agregado', 'bien'); panelDespachar(caja); } }) },
      h('b', null, '+ Nuevo destino'), h('small', null, 'Punto de venta o cliente'))),
    elegido ? h('div', { class: 'destino-elegido' }, h('span', null, 'Va a ', h('b', null, elegido.nombre)),
      h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => {
        if (await editarDestino(elegido)) { avisar('Datos del cliente guardados', 'bien'); panelDespachar(caja); } }) }, 'Editar datos del cliente')) : null,
    h('h2', null, '2. ¿Qué lotes salen? ', h('span', { class: 'num verde' }, cong.lotes.length)),
    h('p', { class: 'chico suave' }, 'Escribe cuántos paquetes salen de cada lote. Un paquete = una etiqueta.'),
    cajaLotes,
    h('h2', null, '3. Datos del despacho'),
    h('div', { class: 'en-linea' }, h('div', { style: 'max-width:220px' }, h('label', null, 'Fecha'), fecha), h('div', null, h('label', null, 'Observaciones'), obs)),
    h('div', { class: 'fila-botones' }, boton),
    cong.lotes.length && (cong.primer_uso || cong.lotes.some((l) => l.dias_caducidad < -30)) ? tarjetaPuestaEnMarcha(estado, () => vistaDespachos('despachar')) : null);
  pintarLotes();
  actualizarBoton();
}

// Solo al empezar a usar Despachos: los lotes viejos ya salieron sin registrarse.
function tarjetaPuestaEnMarcha(estado, alTerminar) {
  const hasta = h('input', { type: 'date', max: estado.hoy });
  return h('div', { class: 'tarjeta chico', style: 'margin-top:1.4rem' }, h('b', null, 'Puesta en marcha: sacar lotes que ya salieron'),
    h('p', { class: 'suave' }, 'Si empiezan a usar Despachos con lotes antiguos en la lista, quítalos de una vez. Quedan registrados como «salió antes de usar Despachos».'),
    h('div', { class: 'en-linea' }, h('div', { style: 'max-width:220px' }, h('label', null, 'Elaborados hasta el'), hasta),
      h('button', { class: 'boton', type: 'button', onclick: () => intentar(async () => {
        if (!hasta.value) throw new Error('Elige la fecha.');
        if (!await confirmar('Sacar lotes antiguos', 'Los lotes elaborados hasta el ' + dia(hasta.value) + ' salen de la lista del congelador.', 'Sí, sacarlos')) return;
        const r = await api('/congelador/limpiar', { hasta: hasta.value });
        avisar(plural(r.lotes, 'lote quitado', 'lotes quitados'), 'bien'); alTerminar();
      }) }, 'Sacar de la lista')));
}

function tarjetaDespacho(d, alCambiar) {
  return h('div', { class: 'tarjeta despacho' + (d.anulado ? ' anulado' : '') },
    h('div', { class: 'despacho-cab' },
      h('div', null, h('b', { class: 'despacho-num' }, nDespacho(d.id)), ' · ', h('b', null, d.destino), ' · ', dia(d.fecha), ' ',
        d.anulado ? h('span', { class: 'etq gris' }, 'Anulado') : null,
        h('div', { class: 'chico suave' }, plural(d.total, 'paquete', 'paquetes') + ' · registrado ' + hora(d.fecha_hora) + (d.persona ? ' · ' + d.persona : '')
          + (d.observaciones ? ' · ' + d.observaciones : ''))),
      h('div', { class: 'acciones-receta' },
        h('a', { class: 'boton chico', href: '#/despachos/guia/' + d.id }, 'Guía'),
        d.anulado ? null : h('button', { class: 'boton chico peligro', type: 'button', onclick: () => intentar(async () => {
          const motivo = await pedirTexto('Anular el despacho ' + nDespacho(d.id), 'Motivo (queda registrado). Los paquetes vuelven al congelador.');
          if (!motivo) return;
          await api('/despachos/' + d.id + '/anular', { motivo }); avisar('Despacho anulado', 'bien'); alCambiar();
        }) }, 'Anular'))),
    h('div', { class: 'chips-despacho' }, d.lineas.map((li) => h('span', { class: 'chip-lote' }, h('b', { class: 'mono' }, li.lote_final), ' ' + li.producto + ' · ' + li.paquetes))),
    d.anulado ? h('div', { class: 'chico', style: 'color:var(--alerta)' }, 'Anulado ' + hora(d.anulado_en) + ': ' + d.motivo_anulacion) : null);
}

async function panelHistorialDespachos(caja, pagina) {
  const r = await api('/despachos?pagina=' + pagina);
  llenar(caja,
    h('div', { class: 'fila-botones' }, h('button', { class: 'boton', type: 'button', onclick: () => { location.href = '/api/despachos.xlsx'; } }, 'Descargar Excel de despachos')),
    r.total ? [r.datos.map((d) => tarjetaDespacho(d, () => panelHistorialDespachos(caja, pagina))),
      paginador(r.total, r.pagina, r.por_pagina, (n) => intentar(() => panelHistorialDespachos(caja, n)), 'Despachos')]
      : h('p', { class: 'suave' }, 'Todavía no hay despachos.'));
}

async function panelDestinos(caja) {
  const lista = await api('/destinos');
  const recargar = () => panelDestinos(caja);
  llenar(caja,
    h('div', { class: 'fila-botones' }, h('button', { class: 'boton principal', type: 'button', onclick: () => intentar(async () => {
      if (await editarDestino()) { avisar('Destino agregado', 'bien'); recargar(); } }) }, '+ Agregar destino')),
    lista.length ? lista.map((d) => h('div', { class: 'tarjeta fila-destino' },
      h('div', { class: 'datos' }, h('b', { class: 'nombre-destino' }, d.nombre),
        h('div', { class: 'chico' }, datosDestino(d) || h('span', { class: 'suave' }, 'Sin datos de contacto')),
        d.notas ? h('div', { class: 'chico suave' }, d.notas) : null,
        h('div', { class: 'chico suave' }, d.n_despachos ? plural(d.n_despachos, 'despacho', 'despachos') + ' · último ' + dia(d.ultimo) : 'Sin despachos')),
      h('div', { class: 'acciones-receta' },
        h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => {
          if (await editarDestino(d)) { avisar('Destino guardado', 'bien'); recargar(); } }) }, 'Editar'),
        h('button', { class: 'boton chico peligro', type: 'button', onclick: () => intentar(async () => {
          if (!await confirmar('Eliminar ' + d.nombre, 'Ya no aparecerá para elegir. Sus despachos anteriores se conservan en el historial y en la consulta.', 'Sí, eliminar')) return;
          await api('/destinos/' + d.id, { eliminar: true }); avisar('Destino eliminado', 'bien'); recargar(); }) }, 'Eliminar'))))
      : h('p', { class: 'suave' }, 'No hay destinos. Agrega el primero.'));
}

async function vistaGuiaDespacho(id) {
  const d = await api('/despachos/' + id);
  const celda = (etq, valor) => h('td', null, h('div', { class: 'imp-etq' }, etq), h('div', { class: 'imp-valor' }, valor || '—'));
  const impreso = h('div', { class: 'solo-imprimir hoja-impresa' },
    h('table', { class: 'imp-cabecera' }, h('tbody', null, h('tr', null,
      h('td', { class: 'imp-logo' }, h('img', { src: '/static/logo.png', alt: 'San Francisco FoodService' })),
      h('td', { class: 'imp-titulo' }, h('div', null, 'GUÍA DE DESPACHO'), h('div', { class: 'imp-producto' }, 'Pan congelado')),
      h('td', { class: 'imp-lote' }, h('div', { class: 'imp-etq' }, 'Número'), h('div', { class: 'imp-codigo' }, String(d.id).padStart(4, '0')))))),
    d.anulado ? h('p', { class: 'imp-nota', style: 'color:#b00;font-weight:700' }, 'DESPACHO ANULADO: ' + d.motivo_anulacion) : null,
    h('table', { class: 'imp-datos' }, h('tbody', null,
      h('tr', null, celda('Fecha de despacho', dia(d.fecha)), celda('Destino / cliente', d.destino), celda('RUC / cédula', d.ruc), celda('Teléfono', d.telefono)),
      h('tr', null, celda('Persona de contacto', d.contacto), h('td', { colspan: '2' }, h('div', { class: 'imp-etq' }, 'Dirección'), h('div', { class: 'imp-valor' }, d.direccion || '—')),
        celda('Registrado', hora(d.fecha_hora))))),
    h('div', { class: 'imp-sub' }, 'Lotes despachados'),
    h('table', { class: 'imp-tabla' },
      h('thead', null, h('tr', null, h('th', { style: 'width:7%' }, 'N.º'), h('th', { style: 'width:20%' }, 'Lote'), h('th', { style: 'width:33%' }, 'Producto'),
        h('th', { style: 'width:14%' }, 'Elaboración'), h('th', { style: 'width:14%' }, 'Vence'), h('th', { style: 'width:12%' }, 'Paquetes'))),
      h('tbody', null, d.lineas.map((li, i) => h('tr', null, h('td', { class: 'centro' }, String(i + 1)), h('td', { class: 'mono' }, li.lote_final), h('td', null, li.producto),
        h('td', { class: 'centro' }, dia(li.fecha)), h('td', { class: 'centro' }, dia(li.caducidad)), h('td', { class: 'centro' }, String(li.paquetes)))),
      h('tr', null, h('td', { colspan: '5', style: 'text-align:right;font-weight:700' }, 'TOTAL'), h('td', { class: 'centro', style: 'font-weight:800' }, String(d.total))))),
    h('div', { class: 'imp-sub' }, 'Observaciones'),
    h('div', { class: 'imp-observaciones' }, d.observaciones || ''),
    h('table', { class: 'imp-firmas' }, h('tbody', null, h('tr', null,
      h('td', null, h('div', { class: 'imp-linea' }), 'Entregado por', h('div', { class: 'imp-etq' }, 'Producción · panadería')),
      h('td', null, h('div', { class: 'imp-linea' }), 'Recibido por', h('div', { class: 'imp-etq' }, 'Nombre, firma y hora'))))),
    h('div', { class: 'imp-pie' }, 'TrazaPanUSFQ · San Francisco FoodService · impreso el ' + new Date().toLocaleString('es-EC')));
  pintar(impreso, h('div', { class: 'no-imprimir' },
    h('a', { class: 'volver', href: '#/despachos/historial' }, h('span', { class: 'flecha-volver', 'aria-hidden': 'true' }, '←'), 'Volver a Despachos'),
    h('h1', null, 'Despacho ' + nDespacho(d.id)),
    tarjetaDespacho(d, () => vistaGuiaDespacho(id)),
    d.ruc || d.contacto || d.telefono || d.direccion ? h('p', { class: 'chico suave' }, datosDestino(d)) : null,
    h('div', { class: 'fila-botones' },
      h('button', { class: 'boton principal', type: 'button', onclick: () => window.print() }, 'Imprimir guía de despacho'),
      h('a', { class: 'boton', href: '#/despachos/despachar' }, 'Nuevo despacho'))));
}

// Consulta: a dónde salió un lote de pan (para avisar en un retiro).
function bloqueDespachos(p) {
  if (!p.despachos) return null;
  return h('div', { class: 'bloque-despachos' },
    h('h3', null, 'A dónde se entregó'),
    p.despachos.length ? h('div', { class: 'tabla-caja' }, h('table', null,
      h('thead', null, h('tr', null, ['Fecha', 'Destino', 'Contacto', 'Paquetes', ''].map((t) => h('th', null, t)))),
      h('tbody', null, p.despachos.map((e) => h('tr', null, h('td', null, dia(e.fecha)), h('td', null, h('b', null, e.destino)),
        h('td', { class: 'chico' }, datosDestino(e) || '—'), h('td', null, e.paquetes),
        h('td', null, h('a', { href: '#/despachos/guia/' + e.despacho_id, class: 'chico' }, nDespacho(e.despacho_id))))))))
      : h('p', { class: 'suave chico' }, 'No tiene despachos registrados.'),
    h('p', { class: 'chico' }, p.en_congelador > 0 ? ['Quedan en el congelador: ', h('b', null, plural(p.en_congelador, 'paquete', 'paquetes'))] : null,
      p.bajas_congelador.length ? h('span', null, (p.en_congelador > 0 ? ' · ' : '') + 'Bajas: ' + p.bajas_congelador.map((b) =>
        b.paquetes + ' ' + b.motivo_texto.toLowerCase() + ' (' + hora(b.fecha_hora) + ')').join('; ')) : null));
}

// ------------------------------------------------------------------ consulta
function arbolProduccion(p) {
  if (p.armado) {
    return h('div', { class: 'tarjeta' },
      h('div', null, h('b', { class: 'mono', style: 'font-size:1.15rem' }, p.lote_final), ' · ', p.producto, ' ',
        h('span', { class: 'etq ' + CLASE_ESTADO[p.estado] }, NOMBRE_ESTADO[p.estado]), ' ', h('a', { href: '#/hoja/' + p.id, class: 'chico' }, 'ver hoja')),
      h('div', { class: 'chico suave' }, 'Armado ' + dia(p.fecha) + ' · vence ' + dia(p.fecha_vencimiento) + (p.unidades ? ' · ' + p.unidades + ' paquetes' : '')),
      p.componentes.map((c) => h('div', { class: 'arbol' },
        h('p', { class: 'chico' }, h('b', null, num(c.unidades) + ' unidades'), ' de ' + c.producto + ' ' + c.lote_final),
        c.produccion ? arbolProduccion(c.produccion) : null)));
  }
  return h('div', { class: 'tarjeta' },
    h('div', null, h('b', { class: 'mono', style: 'font-size:1.15rem' }, p.lote_final), ' · ', p.producto, ' ',
      h('span', { class: 'etq ' + CLASE_ESTADO[p.estado] }, NOMBRE_ESTADO[p.estado]), ' ', h('a', { href: '#/hoja/' + p.id, class: 'chico' }, 'ver hoja')),
    h('div', { class: 'chico suave' }, 'Elaborado ' + dia(p.fecha) + (p.unidades ? ' · ' + p.unidades + ' unidades' : '') + ' · inició ' + hora(p.iniciado_en) + ' ' + (p.iniciado_por || '')),
    h('div', { class: 'tabla-caja', style: 'margin-top:.5rem' }, h('table', null,
      h('thead', null, h('tr', null, ['Ingrediente', 'Lote', 'Proveedor', 'Ingresó a bodega', 'Caduca'].map((t) => h('th', null, t)))),
      h('tbody', null, p.lineas.map((li) => {
        const vig = li.lotes.filter((l) => !l.quitado_en);
        if (li.tipo === 'no_aplica') return h('tr', null, h('td', null, li.etiqueta), h('td', { colspan: '4', class: 'suave' }, 'No aplica'));
        if (!vig.length) return h('tr', { class: li.no_utilizado ? '' : 'falta' }, h('td', null, li.etiqueta),
          h('td', { colspan: '4' }, li.no_utilizado ? 'No se utilizó' : 'SIN LOTE REGISTRADO'));
        return vig.map((l) => [h('tr', null, h('td', null, li.etiqueta), h('td', { class: 'mono' }, h('b', null, l.lote),
          l.origen_lote === 'manual' ? h('span', { class: 'etq rojo' }, 'a mano') : null),
        h('td', null, l.origen_lote === 'interno' ? 'Hecho en la panadería' : (l.proveedor || '—')), h('td', null, dia(l.fecha_ingreso) || '—'), h('td', null, dia(l.caducidad) || '—')),
        (l.subproducciones || []).length ? h('tr', null, h('td', { colspan: '5' }, h('div', { class: 'arbol' }, l.subproducciones.map(arbolProduccion)))) : null]);
      })))));
}

async function vistaConsulta() {
  const campoPan = h('input', { type: 'search', placeholder: 'ej. 071026CR', autocapitalize: 'characters' });
  const campoIng = h('input', { type: 'search', placeholder: 'ej. 291234' });
  const salida = h('div', null);
  const buscarPan = () => intentar(async () => {
    const r = await api('/traza/final?lote=' + encodeURIComponent(campoPan.value));
    llenar(salida, h('h2', null, 'Pan ' + r.lote + ': de qué está hecho'),
      r.producciones.length ? h('div', null, r.producciones.length > 1 ? h('div', { class: 'nota' }, 'Hay ' + r.producciones.length + ' producciones con este mismo código de lote.') : null,
        r.producciones.map((p) => [arbolProduccion(p), bloqueDespachos(p)])) : h('div', { class: 'nota rojo' }, 'No hay ninguna producción registrada con ese código.'));
  });
  const buscarIng = () => intentar(async () => {
    const lotes = await api('/traza/ingrediente?q=' + encodeURIComponent(campoIng.value));
    llenar(salida, h('h2', null, 'Lotes de ingrediente que coinciden con «' + campoIng.value + '»'),
      lotes.length ? lotes.map((l) => h('div', { class: 'tarjeta' },
        tarjetaLote(l, { conIngrediente: true, conNota: true, acciones: l.agotado ? h('button', { class: 'boton chico', type: 'button',
          onclick: () => intentar(async () => { await api('/lotes/reactivar', { lote_id: l.id }); avisar('Lote reactivado', 'bien'); buscarIng(); }) }, 'Reactivar') : null }),
        l.foto ? h('p', null, h('a', { href: '/fotos/' + l.foto, target: '_blank' }, 'Ver foto del empaque')) : null,
        h('h3', null, 'Panes en los que se usó ', h('span', { class: 'num' + (l.usos.length ? ' rojo' : '') }, l.usos.length)),
        l.usos.length ? h('div', { class: 'tabla-caja' }, h('table', null,
          h('thead', null, h('tr', null, ['Lote del pan', 'Producto', 'Fecha', 'Unidades', 'Cómo llegó', 'Entregado a'].map((t) => h('th', null, t)))),
          h('tbody', null, l.usos.map((u) => h('tr', null, h('td', { class: 'mono' }, h('a', { href: '#/hoja/' + u.produccion_id }, h('b', null, u.lote_final))),
            h('td', null, u.producto, u.es_subreceta ? h('span', { class: 'etq gris' }, 'subreceta') : null), h('td', null, dia(u.fecha)), h('td', null, u.unidades || '—'),
            h('td', { class: 'chico' }, u.via.length ? 'a través de ' + u.via.join(' → ') : 'directo (' + u.renglon + ')'),
            h('td', { class: 'chico' }, u.es_subreceta ? '—' : [u.despachos.map((e) => e.destino + ' (' + e.paquetes + ')').join(', ') || null,
              u.en_congelador > 0 ? h('span', { class: 'suave' }, (u.despachos.length ? ' · ' : '') + u.en_congelador + ' en congelador') : null,
              !u.despachos.length && !(u.en_congelador > 0) ? '—' : null]))))))
          : h('p', { class: 'suave' }, 'No aparece en ninguna hoja de producción.'),
        l.destinos && l.destinos.length ? h('div', { class: 'nota destinos-avisar' }, h('b', null, 'Destinos que recibieron pan con este lote'),
          l.destinos.map((e) => h('div', { class: 'chico' }, '• ', h('b', null, e.destino), ': ' + plural(e.paquetes, 'paquete', 'paquetes') + ' (' + e.lotes.join(', ') + ')'
            + (datosDestino(e) ? ' · ' + datosDestino(e) : '')))) : null,
        l.salidas_areas && l.salidas_areas.length ? h('p', { class: 'chico' }, h('b', null, 'También salió de bodega principal a otras áreas: '),
          l.salidas_areas.map((o) => num(o.cantidad) + (o.unidad ? ' ' + o.unidad : '') + ' ' + (o.area === 'AJUSTE' ? 'cierre del lote (' + o.motivo + ')' : 'a ' + o.area)
            + ' el ' + hora(o.fecha_hora)).join('; ')) : null,
        l.bajas && l.bajas.length ? h('div', { class: 'chico', style: 'margin:.4rem 0' }, h('b', { style: 'color:var(--alerta)' }, 'Dado de baja: '),
          l.bajas.map((b) => h('div', null, '• ' + ({ principal: 'Bodega principal', secundaria: 'Bodega secundaria', cocina: 'Cocina' }[b.lugar]) + ', '
            + hora(b.fecha_hora) + ': ' + b.motivo.split(':')[0].toLowerCase() + (b.cantidad ? ' · ' + num(b.cantidad) + (b.unidad ? ' ' + b.unidad : '') : '')
            + (b.comentario ? ' · ' + b.comentario : '') + (b.persona ? ' · ' + b.persona : ''), ' ',
          b.id ? h('button', { class: 'boton chico peligro', type: 'button', style: 'padding:.1rem .5rem', onclick: () => intentar(async () => {
            const motivo = await pedirTexto('Anular la baja del lote ' + l.lote, 'Motivo (queda registrado)');
            if (!motivo) return;
            await api('/bajas/' + b.id + '/anular', { motivo }); avisar('Baja anulada', 'bien'); buscarIng();
          }) }, 'Anular') : null))) : null,
        l.aperturas.length ? h('p', { class: 'chico suave' }, 'En cocina: ' + l.aperturas.map((a) => hora(a.abierto_en) + ' → ' + (a.cerrado_en ? hora(a.cerrado_en) : 'sigue abierto')).join('; ')) : null))
        : h('div', { class: 'nota rojo' }, 'No hay lotes con ese texto.'));
  });
  campoPan.addEventListener('keydown', (e) => { if (e.key === 'Enter') buscarPan(); });
  campoIng.addEventListener('keydown', (e) => { if (e.key === 'Enter') buscarIng(); });
  pintar(encabezado('Consultar trazabilidad'),
    h('div', { class: 'tarjeta' }, h('label', null, 'Tengo un pan y quiero saber de qué lotes está hecho'),
      h('div', { class: 'en-linea' }, campoPan, h('button', { class: 'boton principal', type: 'button', onclick: buscarPan }, 'Buscar'))),
    h('div', { class: 'tarjeta' }, h('label', null, 'Tengo un lote de ingrediente y quiero saber en qué panes se usó'),
      h('div', { class: 'en-linea' }, campoIng, h('button', { class: 'boton principal', type: 'button', onclick: buscarIng }, 'Buscar'))),
    salida);
}

// ------------------------------------------------------------------ administrar
const PESTANAS = [['importar', 'Importar Excel antiguo'], ['pendientes', 'Pendientes'], ['bodega', 'Productos de bodega'], ['recetas', 'Recetas'],
  ['ingredientes', 'Ingredientes'], ['claves', 'Contraseñas'], ['historial', 'Historial']];

async function vistaAdmin(pestana) {
  pestana = pestana || 'importar';
  const cuerpoPestana = h('div', null, h('p', { class: 'suave' }, 'Cargando…'));
  pintar(encabezado('Administrar'),
    h('div', { class: 'pestanas' }, PESTANAS.map(([id, nombre]) => h('button', { type: 'button', class: id === pestana ? 'activa' : '',
      onclick: () => { location.hash = '#/admin/' + id; } }, nombre))), cuerpoPestana);
  const poner = (...n) => llenar(cuerpoPestana, ...n);
  const recargar = () => vistaAdmin(pestana);
  const ings = await api('/ingredientes');
  const selectorIngrediente = (valor, soloProveedor) => h('select', null, h('option', { value: '' }, '— elegir ingrediente —'),
    ings.filter((i) => (!soloProveedor || i.tipo === 'lote') && (i.activo || i.id === valor)).map((i) => h('option', { value: i.id, selected: i.id === valor }, i.nombre)));

  if (pestana === 'importar') {
    const archivo = h('input', { type: 'file', accept: '.xlsx' });
    const resultado = h('div', null);
    const fecha = h('input', { type: 'date' });
    poner(h('div', { class: 'tarjeta' }, h('h2', { style: 'margin-top:0' }, 'Importar registros anteriores de bodega principal'),
      h('p', { class: 'suave' }, 'Solo para cargar lo que se registró en Excel antes de usar la app. Desde ahora, bodega principal registra cada recepción en la pantalla «Bodega principal». Las filas ya importadas o ya registradas en la app no se duplican.'),
      archivo, h('div', { class: 'fila-botones' }, h('button', { class: 'boton principal', type: 'button', onclick: () => intentar(async () => {
        if (!archivo.files[0]) throw new Error('Elige el archivo Excel.');
        const resp = await fetch('/api/importar?persona=' + encodeURIComponent(quien()), { method: 'POST', body: archivo.files[0] });
        const r = await resp.json();
        if (!resp.ok) throw new Error(r.error);
        llenar(resultado, h('div', { class: 'nota verde' }, 'Hoja «' + r.hoja + '»: ' + r.filas_leidas + ' filas leídas · ' + r.recepciones_nuevas +
          ' recepciones nuevas · ' + r.lotes_nuevos + ' lotes nuevos · ' + r.ya_importadas + ' ya estaban.'),
        r.por_asignar ? h('div', { class: 'nota' }, r.por_asignar + ' producto(s) de bodega sin ingrediente asignado. ', h('a', { href: '#/admin/pendientes' }, 'Asignarlos')) : null,
        r.sin_lote.length ? h('div', { class: 'nota rojo' }, 'Filas sin lote (no se importaron): ' + r.sin_lote.map((s) => 'fila ' + s.fila + ' ' + s.producto + ' (' + dia(s.fecha) + ')').join('; ')) : null);
      }) }, 'Importar')), resultado),
    h('div', { class: 'tarjeta' }, h('h2', { style: 'margin-top:0' }, 'Puesta en marcha: limpiar lotes viejos'),
      h('p', { class: 'suave' }, 'La primera importación trae lotes de hace meses que ya no existen. Márcalos como agotados para que no aparezcan en las listas (siguen en el historial y se pueden reactivar).'),
      h('div', { class: 'en-linea' }, h('div', null, h('label', null, 'Agotar los lotes ingresados antes del'), fecha),
        h('button', { class: 'boton', type: 'button', onclick: () => intentar(async () => {
          const r = await api('/admin/agotar_antiguos', { antes_de: fecha.value }); avisar(r.lotes + ' lotes marcados como agotados', 'bien');
        }) }, 'Marcar como agotados'))),
    h('div', { class: 'tarjeta' }, h('h2', { style: 'margin-top:0' }, 'Respaldo'),
      h('p', { class: 'suave' }, 'Toda la información está en un solo archivo. Se guarda una copia automática por día en la carpeta datos/respaldos.'),
      h('a', { class: 'boton', href: '/api/respaldo' }, 'Descargar copia de la base de datos')),
    h('div', { class: 'tarjeta zona-peligro' }, h('h2', { style: 'margin-top:0' }, 'Empezar de cero'),
      h('p', null, 'Borra ', h('b', null, 'recepciones, lotes, hojas de producción, etiquetas, despachos, inventarios, bajas e historial'), '.'),
      h('p', { class: 'suave' }, 'Se conservan recetas, productos, ingredientes, productos de bodega, destinos y contraseñas. Antes de borrar se guarda una copia completa en datos/respaldos.'),
      h('button', { class: 'boton peligro-lleno', type: 'button', onclick: () => intentar(async () => {
        const t = await pedirTexto('Borrar todos los registros', 'No se puede deshacer desde la app. Escribe BORRAR para confirmar.');
        if (t === null || t === undefined) return;
        if (t.trim().toUpperCase() !== 'BORRAR') throw new Error('No se borró nada: había que escribir BORRAR.');
        const r = await api('/admin/borrar-registros', { confirmacion: 'BORRAR' });
        avisar('Listo: se borraron ' + r.recepciones + ' recepciones, ' + r.lotes + ' lotes y ' + r.hojas + ' hojas', 'bien');
        recargar();
      }) }, 'Borrar todos los registros')));
  }

  if (pestana === 'pendientes') {
    const pen = await api('/pendientes');
    poner(h('h2', null, 'Productos de bodega sin ingrediente ', h('span', { class: 'num' + (pen.alias.length ? ' rojo' : '') }, pen.alias.length)),
      h('p', { class: 'suave chico' }, 'Mientras no se asignen, sus lotes no aparecen en ninguna lista.'),
      pen.alias.map((a) => filaAlias(a)),
      h('h2', null, 'Lotes por revisar ', h('span', { class: 'num' + (pen.lotes.length ? ' rojo' : '') }, pen.lotes.length)),
      pen.lotes.map((l) => tarjetaLote(l, { conIngrediente: true, conNota: true, acciones: h('div', { style: 'display:grid;gap:.3rem' },
        l.foto ? h('a', { class: 'boton chico', href: '/fotos/' + l.foto, target: '_blank' }, 'Foto') : null,
        h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => { await api('/lotes/revisado', { lote_id: l.id }); recargar(); }) }, 'Está bien'),
        l.origen === 'manual' ? h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => {
          const otros = (await api('/lotes?donde=todos&ingrediente_id=' + l.ingrediente_id)).filter((o) => o.id !== l.id).slice(0, 25);
          const destino = await modal('«' + l.lote + '» en realidad es…', h('div', null, h('p', { class: 'suave' }, 'Toca el lote correcto de la lista de bodega. Todo lo registrado con el lote anotado a mano pasará a ese lote.'),
            otros.map((o) => tarjetaLote(o, { elegible: true, alTocar: (e) => { const v = e.currentTarget.closest('.velo'); v.dataset.elegido = o.id; v.querySelector('.principal').click(); } }))),
          [{ texto: 'Unir con el lote tocado', clase: 'principal', valor: () => Number(document.querySelector('.velo').dataset.elegido) || null }, { texto: 'Cancelar', valor: null }]);
          if (destino) { await api('/lotes/fusionar', { lote_id: l.id, destino_id: destino }); avisar('Lotes unidos', 'bien'); recargar(); }
        }) }, 'Es otro de la lista') : null) })),
      h('h2', null, 'Hojas con lotes faltantes ', h('span', { class: 'num' + (pen.hojas.length ? ' rojo' : '') }, pen.hojas.length)),
      pen.hojas.map(tarjetaProduccion));
  }

  const cambiarFoto = (a) => {
    const subir = h('input', { type: 'file', accept: 'image/*', hidden: true });
    subir.addEventListener('change', () => intentar(async () => {
      const archivo = subir.files[0]; if (!archivo) return;
      const r = await subirFotoProducto(a.id, archivo);
      avisar(r.sin_fondo ? 'Foto guardada sin fondo' : 'Foto guardada (no se pudo quitar el fondo automáticamente)', 'bien'); recargar();
    }));
    document.body.append(subir); subir.click(); setTimeout(() => subir.remove(), 60000);
  };
  function filaAlias(a) {
    const sel = selectorIngrediente(a.ingrediente_id, true);
    const foto = a.en_catalogo ? h('div', { class: 'foto-admin' }, fotoProducto(a.foto, a.producto),
      h('button', { class: 'boton chico', type: 'button', onclick: () => cambiarFoto(a) }, a.foto ? 'Cambiar foto' : 'Poner foto'),
      a.foto ? h('button', { class: 'boton chico peligro', type: 'button', onclick: () => intentar(async () => {
        await api('/bodega/productos/' + a.id + '/foto/quitar', {}); recargar(); }) }, 'Quitar foto') : null) : null;
    return h('div', { class: 'tarjeta fila-producto' + (foto ? ' con-foto' : '') }, foto,
      h('div', { class: 'datos-producto' },
        h('div', { class: 'titulo-producto' }, h('b', null, a.producto), h('span', { class: 'chico suave' }, a.recepciones + ' recepción(es)')),
        h('div', { class: 'etiquetas-producto' },
          a.ignorar ? h('span', { class: 'etq gris' }, 'Ignorado') : null, a.auto ? h('span', { class: 'etq aviso' }, 'Asignado automáticamente') : null,
          a.en_catalogo ? h('span', { class: 'etq verde' }, 'En la lista de bodega') : null),
        h('div', { class: 'asignar-producto' }, sel,
          h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => {
            if (!sel.value) throw new Error('Elige un ingrediente.');
            await api('/alias/' + a.id, { ingrediente_id: Number(sel.value) }); recargar();
          }) }, 'Asignar')),
        h('div', { class: 'botones-producto' },
          h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => {
            const nombre = await pedirTexto('Ingrediente nuevo', 'Nombre como debe salir en la hoja de producción', a.producto, { mayusculas: true });
            if (!nombre) return;
            const frio = await modal('¿Dónde se guarda?', h('p', null, nombre), [{ texto: 'Seco', clase: 'principal', valor: 'seco' }, { texto: 'Refrigerado', valor: 'frio' }]);
            if (!frio) return;
            await api('/alias/' + a.id, { nuevo: nombre, refrigerado: frio === 'frio' }); recargar();
          }) }, 'Ingrediente nuevo'),
          h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => { await api('/alias/' + a.id, { ignorar: true }); recargar(); }) }, 'No se usa en panadería'),
          h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => {
            await api('/bodega/productos/' + a.id + '/lista', { en_lista: !a.en_catalogo }); recargar();
          }) }, a.en_catalogo ? 'Quitar de la lista de bodega' : 'Poner en la lista de bodega'))));
  }

  if (pestana === 'bodega') {
    const lista = await api('/alias');
    const cat = await api('/bodega/catalogo');
    // 1 PAQUETE = 50 kg: todos registran en paquetes y la app calcula lo demás.
    const editarEmpaque = async (a) => {
      const empaque = h('select', null, cat.empaques.map((e) => h('option', { value: e, selected: e === (a.empaque || 'PAQUETE') }, nombreEmpaque(e, 1))));
      const contenido = h('input', { type: 'text', inputmode: 'decimal', value: a.contenido ? num(a.contenido) : '', placeholder: 'ej. 50',
        oninput: (e) => { const v = soloNumero(e.target.value); if (v !== e.target.value) e.target.value = v; } });
      const unidadC = h('select', null, cat.unidades_contenido.map((u) => h('option', { value: u, selected: u === (a.unidad_contenido || 'kg') }, u)));
      setTimeout(() => contenido.focus(), 50);
      const r = await modal('Empaque de ' + a.producto, h('div', null,
        h('p', { class: 'chico suave', style: 'margin-top:0' }, 'Bodega principal, bodega secundaria y cocina registran en este empaque. La app calcula lo que equivale.'),
        h('div', { class: 'en-linea empaque-linea' }, h('b', null, '1'), empaque, h('b', null, '='), contenido, unidadC),
        a.empaque && a.recepciones ? h('p', { class: 'chico suave' }, 'Lo ya registrado en otra unidad (kg, unidades) se pasa a ' + nombreEmpaque(empaque.value, 2) + '.') : null),
      [{ texto: 'Guardar', clase: 'principal', valor: () => ({ empaque: empaque.value, contenido: contenido.value, unidad_contenido: unidadC.value }) },
        a.empaque ? { texto: 'Quitar empaque', clase: 'peligro', valor: () => ({ quitar: true }) } : null,
        { texto: 'Cancelar', valor: null }].filter(Boolean));
      if (!r) return false;
      await api('/bodega/productos/' + a.id + '/empaque', r);
      avisar(r.quitar ? 'Empaque quitado' : 'Empaque guardado', 'bien');
      return true;
    };
    // Producto de bodega: foto, nombre (el mismo en toda la app) y eliminar.
    const filaProducto = (a) => {
      return h('div', { class: 'tarjeta prod-bodega' },
        fotoProducto(a.foto, a.producto, 'prod-foto'),
        h('div', { class: 'prod-nombre' }, h('b', null, a.producto),
          h('span', { class: 'chico suave' }, a.recepciones ? a.recepciones + (a.recepciones === 1 ? ' recepción' : ' recepciones') : 'Sin recepciones todavía'),
          a.empaque ? h('span', { class: 'etq verde' }, textoEmpaque(a)) : h('span', { class: 'etq gris' }, 'Sin empaque')),
        h('div', { class: 'prod-acciones' },
          h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => { if (await editarEmpaque(a)) recargar(); }) }, 'Empaque'),
          h('button', { class: 'boton chico', type: 'button', onclick: () => cambiarFoto(a) }, a.foto ? 'Cambiar foto' : 'Subir foto'),
          a.foto ? h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => {
            await api('/bodega/productos/' + a.id + '/foto/quitar', {}); recargar(); }) }, 'Quitar foto') : null,
          h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => {
            const nombre = await pedirTexto('Cambiar nombre', 'Nombre del producto en bodega principal', a.producto, { mayusculas: true });
            if (!nombre) return;
            await api('/bodega/productos/' + a.id + '/nombre', { nombre }); avisar('Nombre cambiado', 'bien'); recargar();
          }) }, 'Cambiar nombre'),
          h('button', { class: 'boton chico peligro', type: 'button', onclick: () => intentar(async () => {
            if (!await confirmar('Eliminar ' + a.producto, 'Ya no aparecerá en ninguna parte de la app. Lo que ya se registró de este producto se mantiene.', 'Sí, eliminar')) return;
            await api('/bodega/productos/' + a.id + '/lista', { en_lista: false }); avisar('Producto eliminado', 'bien'); recargar();
          }) }, 'Eliminar')));
    };
    const enLista = lista.filter((a) => a.en_catalogo);
    const fuera = lista.filter((a) => !a.en_catalogo);
    poner(h('p', { class: 'suave' }, 'Productos que bodega principal puede registrar.'),
      enLista.map(filaProducto),
      fuera.length ? h('details', { class: 'eliminados' }, h('summary', null, 'Eliminados o que solo vienen del Excel antiguo (' + fuera.length + ')'),
        fuera.map((a) => h('div', { class: 'fila-cerrado' }, h('span', null, h('b', null, a.producto), a.recepciones ? h('span', { class: 'chico suave' }, ' · ' + a.recepciones + ' recepción(es)') : null),
          h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => {
            await api('/bodega/productos/' + a.id + '/lista', { en_lista: true }); avisar('Vuelve a la lista de bodega', 'bien'); recargar(); }) }, 'Volver a la lista')))) : null);
  }

  if (pestana === 'recetas') {
    const productos = await api('/productos');
    const editar = (p) => {
      p = p || { nombre: '', codigo: '', vida_util_meses: 1, lineas: [], activo: 1 };
      const nombre = h('input', { type: 'text', value: p.nombre });
      const codigo = h('input', { type: 'text', value: p.codigo, placeholder: 'ej. CR' });
      const meses = h('input', { type: 'number', min: '0', value: p.vida_util_meses });
      const dias = h('input', { type: 'number', min: '1', value: p.vida_util_dias || 7 });
      const sub = h('input', { type: 'checkbox', checked: !!p.produce_ingrediente_id, onchange: () => { alternarVida(); dibujarLineas(); } });
      const cajaMeses = h('div', null, h('label', null, 'Vida útil (meses)'), meses);
      const cajaDias = h('div', null, h('label', null, 'Vida útil (días)'), dias);
      const alternarVida = () => { cajaMeses.hidden = sub.checked; cajaDias.hidden = !sub.checked; };
      const arm = h('input', { type: 'checkbox', checked: !!p.armado, onchange: () => alternar() });
      let componentes = (p.componentes || []).map((c) => c.id);
      const panes = productos.filter((x) => !x.armado && !x.produce && x.id !== p.id && (x.activo || (p.componentes || []).some((c) => c.id === x.id)));
      const cajaComp = h('div', null);
      const dibujarComp = () => cajaComp.replaceChildren(...componentes.map((cid, i) => {
        const sel = h('select', { onchange: (e) => { componentes[i] = Number(e.target.value) || null; } }, h('option', { value: '' }, '— elegir pan —'),
          panes.map((x) => h('option', { value: x.id, selected: x.id === cid }, x.nombre)));
        return h('div', { class: 'en-linea', style: 'margin:.4rem 0' }, sel,
          h('button', { class: 'boton chico peligro', type: 'button', onclick: () => { componentes.splice(i, 1); dibujarComp(); } }, 'Quitar'));
      }));
      const seccionReceta = h('div', null);
      const seccionMix = h('div', null, h('h3', null, 'Panes que forman la mezcla'), cajaComp,
        h('button', { class: 'boton chico', type: 'button', onclick: () => { componentes.push(null); dibujarComp(); } }, '+ pan'));
      const alternar = () => { seccionMix.hidden = !arm.checked; seccionReceta.hidden = arm.checked; dibujarLineas(); };
      let lineas = p.lineas.map((l) => ({ etiqueta: l.etiqueta, ingrediente_id: l.ingrediente_id, despues_reposo: !!l.despues_reposo }));
      // Todos los panes: la masa se hace un día y la cocción al siguiente. «Se pone al terminar» = lo que se agrega al hornear
      // (relleno, glacé); al elegir una subreceta que no es masa madre se marca sola.
      const ingDe = (id) => ings.find((i) => i.id === id) || {};
      const caja = h('div', null);
      const dibujarLineas = () => caja.replaceChildren(...lineas.map((l, i) => {
        const sel = selectorIngrediente(l.ingrediente_id);
        sel.addEventListener('change', () => { l.ingrediente_id = Number(sel.value) || null;
          const ing = ingDe(l.ingrediente_id); l.despues_reposo = ing.tipo === 'interno' && !/MASA MADRE/.test(ing.nombre || ''); dibujarLineas(); });
        const et = h('input', { type: 'text', value: l.etiqueta, placeholder: 'Nombre en la hoja (opcional)', oninput: (e) => { l.etiqueta = e.target.value; } });
        const despues = !sub.checked ? h('label', { class: 'chico', style: 'display:flex;gap:.35rem;align-items:center;font-weight:400;margin:0;white-space:nowrap' },
          h('input', { type: 'checkbox', checked: !!l.despues_reposo, onchange: (e) => { l.despues_reposo = e.target.checked; } }), 'Se pone al terminar') : null;
        return h('div', { class: 'en-linea', style: 'margin:.4rem 0' }, sel, et, despues,
          h('button', { class: 'boton chico', type: 'button', disabled: i === 0, onclick: () => { [lineas[i - 1], lineas[i]] = [lineas[i], lineas[i - 1]]; dibujarLineas(); } }, '↑'),
          h('button', { class: 'boton chico peligro', type: 'button', onclick: () => { lineas.splice(i, 1); dibujarLineas(); } }, 'Quitar'));
      }));
      dibujarLineas();
      dibujarComp();
      llenar(seccionReceta, h('h3', null, 'Ingredientes, en el orden de la hoja'), caja,
        h('button', { class: 'boton chico', type: 'button', onclick: () => { lineas.push({ etiqueta: '', ingrediente_id: null }); dibujarLineas(); } }, '+ ingrediente'));
      alternarVida();
      poner(h('button', { class: 'boton chico', type: 'button', onclick: recargar }, '← Todas las recetas'),
        h('div', { class: 'tarjeta' }, h('div', { class: 'en-linea' },
          h('div', null, h('label', null, 'Producto'), nombre), h('div', null, h('label', null, 'Código del lote'), codigo),
          cajaMeses, cajaDias),
        h('div', { class: 'opciones-receta' },
          h('label', { class: 'opcion-grande' }, sub, 'Es subreceta'),
          h('label', { class: 'opcion-grande' }, arm, 'Es Mix Bread'),
          null),
        seccionMix, seccionReceta,
        h('div', { class: 'fila-botones' }, h('button', { class: 'boton principal', type: 'button', onclick: () => intentar(async () => {
          await api('/productos', { id: p.id, nombre: nombre.value, codigo: codigo.value, vida_util_meses: meses.value, vida_util_dias: dias.value, es_subreceta: sub.checked,
            inactivo: p.id ? !p.activo : false, lineas, armado: arm.checked, componentes: componentes.filter(Boolean) });
          avisar('Receta guardada', 'bien'); recargar();
        }) }, 'Guardar receta'))));
      alternar();
    };
    poner(h('button', { class: 'boton chico', type: 'button', onclick: () => editar(null) }, '+ Producto nuevo'),
      productos.filter((p) => p.activo).map((p) => h('div', { class: 'tarjeta receta-fila' },
        h('div', { class: 'receta-contenido' },
          h('div', { class: 'receta-datos' }, h('b', null, p.nombre), ' ', h('span', { class: 'etq gris mono' }, p.codigo), p.produce ? h('span', { class: 'etq aviso' }, 'subreceta') : null,
            p.armado ? h('span', { class: 'etq verde' }, 'Mix Bread') : null,
            h('div', { class: 'chico suave' }, p.armado ? 'Panes: ' + (p.componentes.map((c) => c.nombre).join(' · ') || 'sin definir')
              : p.lineas.length ? p.lineas.map((l) => l.etiqueta).join(' · ') : 'Sin receta: no se puede iniciar su producción.')),
          h('div', { class: 'acciones-receta' },
            h('button', { class: 'boton chico', type: 'button', onclick: () => editar(p) }, 'Editar'),
            h('button', { class: 'boton chico peligro', type: 'button', onclick: () => intentar(async () => {
              if (!await confirmar('Eliminar ' + p.nombre, 'Ya no aparecerá en Producción ni en Cocina para empezarlo. Las hojas ya hechas se mantienen.', 'Sí, eliminar')) return;
              await api('/productos/' + p.id + '/activo', { activo: false }); avisar(p.nombre + ' eliminado', 'bien'); recargar();
            }) }, 'Eliminar'))))),
      productos.some((p) => !p.activo) ? h('details', { class: 'eliminados' },
        h('summary', null, 'Eliminados (' + productos.filter((p) => !p.activo).length + ')'),
        productos.filter((p) => !p.activo).map((p) => h('div', { class: 'fila-cerrado' }, h('b', null, p.nombre),
          h('button', { class: 'boton chico', type: 'button', onclick: () => intentar(async () => {
            await api('/productos/' + p.id + '/activo', { activo: true }); avisar(p.nombre + ' vuelve a producción', 'bien'); recargar();
          }) }, 'Volver a producir')))) : null);
  }

  if (pestana === 'ingredientes') {
    poner(h('p', { class: 'suave' }, 'Forma de almacenamiento en cocina: en cuántos recipientes se guarda o si se usa directo de su empaque. Los ingredientes se crean al agregar el producto en bodega principal.'),
      h('div', { class: 'tabla-caja' }, h('table', null, h('thead', null, h('tr', null, ['Ingrediente', 'Forma de almacenamiento', 'Refrigerado'].map((t) => h('th', null, t)))),
        h('tbody', null, ings.filter((i) => i.activo).map((i) => h('tr', null, h('td', null, i.nombre),
          h('td', null, i.tipo === 'no_aplica' ? '—' : h('select', { style: 'min-height:40px', onchange: (e) => intentar(async () => {
            await api('/ingredientes', { id: i.id, recipientes: Number(e.target.value) }); avisar('Guardado', 'bien');
          }).then((r) => { if (r === undefined) recargar(); }) },
          [[0, 'Su propio empaque'], [1, '1 recipiente'], [2, '2 recipientes'], [3, '3 recipientes']].map(([v, t]) =>
            h('option', { value: v, selected: (i.recipientes ?? 1) === v }, t)))),
          h('td', null, i.tipo === 'lote' ? h('input', { type: 'checkbox', checked: !!i.refrigerado, onchange: (e) => intentar(async () => {
            await api('/ingredientes', { id: i.id, refrigerado: e.target.checked }); avisar('Guardado', 'bien');
          }) }) : '—')))))));
  }

  if (pestana === 'claves') {
    const est = await api('/claves');
    const campos = {};
    const fila = (x) => {
      campos[x.area] = h('input', { type: 'password', autocomplete: 'new-password', placeholder: est.activas ? 'Nueva contraseña' : 'Contraseña' });
      return h('div', null, h('label', null, x.nombre), campos[x.area],
        est.activas && x.cambiado_en ? h('div', { class: 'chico suave' }, 'Cambiada el ' + hora(x.cambiado_en)) : null);
    };
    const guardar = () => intentar(async () => {
      const claves = {};
      Object.entries(campos).forEach(([a, c]) => { if (c.value) claves[a] = c.value; });
      await api('/claves', { claves });
      sesion.requiere = true; sesion.area = 'produccion';
      avisar(est.activas ? 'Contraseña cambiada' : 'Contraseñas activadas', 'bien'); recargar();
    });
    const pausa = (pausar) => intentar(async () => {
      if (pausar && !await confirmar('Pausar las contraseñas', 'Mientras esté en pausa, cualquiera entra a las tres áreas sin contraseña. Las contraseñas guardadas no se borran.', 'Pausar')) return;
      await api('/claves/pausa', { pausar });
      sesion.requiere = !pausar; if (!pausar) sesion.area = 'produccion';
      avisar(pausar ? 'Ya no se pide contraseña' : 'Se vuelve a pedir contraseña', 'bien'); recargar();
    });
    const estadoClaves = est.activas ? h('div', { class: 'tarjeta estado-claves', style: 'max-width:480px' },
      h('div', null, h('b', null, 'Pedir contraseña'),
        h('div', { class: 'chico suave' }, est.pausadas ? 'En pausa: se entra a las áreas sin contraseña.' : 'Activado: cada área entra con su contraseña.')),
      h('span', { class: 'etq ' + (est.pausadas ? 'aviso' : 'verde') }, est.pausadas ? 'En pausa' : 'Activado'),
      h('button', { class: 'boton chico' + (est.pausadas ? ' principal' : ''), type: 'button', onclick: () => pausa(!est.pausadas) },
        est.pausadas ? 'Volver a pedir' : 'Pausar')) : null;
    poner(estadoClaves, h('p', { class: 'suave' }, est.activas
      ? 'Cada área entra con su contraseña. Escribe solo la que quieras cambiar (mínimo 4 caracteres).'
      : 'Ahora nadie necesita contraseña. Para activarlas, escribe una para cada área (mínimo 4 caracteres).'),
    h('div', { class: 'tarjeta', style: 'max-width:480px' }, est.areas.map(fila),
      h('div', { class: 'fila-botones' }, h('button', { class: 'boton principal', type: 'button', onclick: guardar }, est.activas ? 'Cambiar' : 'Activar contraseñas'))));
    return;
  }
  if (pestana === 'historial') {
    // Historial de cambios de 30 en 30 (se pide al servidor solo la página que se ve).
    const caja = h('div', null);
    const cargarPag = (n) => intentar(async () => {
      const r = await api('/movimientos?pagina=' + n);
      llenar(caja,
        h('div', { class: 'tabla-caja' }, h('table', null, h('thead', null, h('tr', null, ['Cuándo', 'Quién', 'Qué', 'Detalle'].map((t) => h('th', null, t)))),
          h('tbody', null, r.datos.map((m) => h('tr', null, h('td', { class: 'chico', style: 'white-space:nowrap' }, hora(m.fecha_hora)),
            h('td', { class: 'chico', style: 'white-space:nowrap' }, m.persona || ''),
            h('td', { class: 'chico' }, m.tipo.replace(/_/g, ' ').toLowerCase()), h('td', { class: 'chico' }, m.detalle,
              m.produccion_id ? [' ', h('a', { href: '#/hoja/' + m.produccion_id }, 'hoja')] : null)))))),
        paginador(r.total, r.pagina, r.por_pagina, (k) => { cargarPag(k); caja.scrollIntoView({ block: 'start', behavior: 'smooth' }); }, 'Registros'));
    });
    poner(h('p', { class: 'suave' }, 'Todo lo que se registra en la app. Nada de esto se puede borrar ni editar.'), caja);
    await cargarPag(1);
  }
}

// ------------------------------------------------------------------ rutas
// Pantalla para escribir la contraseña del área de este dispositivo.
function vistaClave() {
  const a = AREAS[area()];
  const clave = h('input', { type: 'password', autocomplete: 'current-password', placeholder: 'Contraseña' });
  const entrar = () => intentar(async () => {
    if (!clave.value) throw new Error('Escribe la contraseña.');
    const r = await api('/entrar', { area: area(), clave: clave.value });
    sesion.area = r.area; pintarArea(); navegar();
  });
  clave.addEventListener('keydown', (e) => { if (e.key === 'Enter') entrar(); });
  pintar(h('div', { class: 'nombre-app nombre-inicio' }, 'Traza', h('b', null, 'Pan'), 'USFQ'),
    botonCambiarArea(),
    h('h1', null, a.nombre),
    h('div', { class: 'tarjeta', style: 'max-width:420px' },
      h('label', { style: 'margin-top:0' }, 'Contraseña de ' + a.nombre), clave,
      h('div', { class: 'fila-botones' }, h('button', { class: 'boton principal', type: 'button', onclick: entrar }, 'Entrar'))));
  setTimeout(() => clave.focus(), 50);
}

async function navegar() {
  const partes = location.hash.replace(/^#\/?/, '').split('/');
  document.querySelectorAll('.velo').forEach((v) => v.remove());
  paginaHorizontal(false);
  $vista.classList.remove('ancho');
  const rutas = { '': vistaInicio, bodega: vistaBodega, inventario: () => vistaInventario(partes[1]), salidas: () => vistaInventario(), secundaria: () => vistaSecundaria(partes[1]), cocina: () => vistaCocina(partes[1]), produccion: vistaProduccion, hojas: vistaHojas,
    hoja: () => vistaHoja(Number(partes[1])), etiquetas: () => vistaEtiquetas(), despachos: () => vistaDespachos(partes[1], partes[2]), consulta: vistaConsulta, admin: () => vistaAdmin(partes[1]) };
  const vista = rutas[partes[0]] || vistaInicio;
  if (!area()) { vistaInicio(); return; }
  if (necesitaClave()) { vistaClave(); return; }
  try { await vista(); } catch (e) {
    pintar(encabezado('No se pudo cargar'), h('div', { class: 'nota rojo' }, e.message),
      h('button', { class: 'boton', type: 'button', onclick: navegar }, 'Reintentar'));
  }
}
window.addEventListener('hashchange', navegar);
(async () => {
  try { Object.assign(sesion, await (await fetch('/api/sesion')).json()); } catch (e) { /* sin conexión: navegar mostrará el error */ }
  pintarQuien();
  pintarArea();
  navegar();
})();
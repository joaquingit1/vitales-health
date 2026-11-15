/* Body render for the crew dashboard.
   A human figure drawn as a dense cloud of points (WebGL points, no library). The shape comes from body-mesh.js,
   which must be loaded first. The cloud is filled: points are spread evenly over the skin and also through the
   inside of the body, so thick parts are dense and nothing reads as an empty shell. A soft light shades the skin
   points, which is what gives the form. The solid figure is drawn into the depth buffer only, so the near skin
   hides the far skin.
   Every body region is addressable, so a card, a reading or the assistant can point at it:

     const body = BodyRender.mount(canvas, { breathsPerMin: 14, onFrame });
     body.focus("ear");                 // the region lights up, the figure turns and the view moves in
     body.focus("heart", { camera: false });  // light only, the view stays where it is
     body.focus("ear", { ax: 0.4 });    // put the region at 40% of the width, leaving room for a label
     body.focus(null);                  // back to the whole figure
     BodyRender.project(BodyRender.point("wrist"));   // screen position of a region, for labels

   Regions: head, eyes, ear, neck, shoulder, heart, lungs, abdomen, flank, back, wrist, hand, legs, knee, ankle. */
(() => {
  const M = window.BODY_MESH;
  if (!M) { console.error("body-render.js needs body-mesh.js to be loaded first"); return; }
  const K = M.marks;

  /* ---------- shape: positions in metres, y up, z toward the viewer ---------- */
  const bytes = (b64) => { const s = atob(b64), a = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) a[i] = s.charCodeAt(i); return a; };
  const POS = (() => { const q = new Int16Array(bytes(M.pos).buffer), p = new Float32Array(q.length); for (let i = 0; i < q.length; i++) p[i] = q[i] * M.unit; return p; })();
  const IDX = new Uint16Array(bytes(M.idx).buffer);
  const NT = IDX.length / 3;
  /* smooth normals, and a running total of triangle area so points can be dealt out evenly by area */
  const NOR = new Float32Array(POS.length), AREA = new Float32Array(NT);
  let total = 0;
  for (let t = 0; t < NT; t++) {
    const a = IDX[t * 3] * 3, b = IDX[t * 3 + 1] * 3, c = IDX[t * 3 + 2] * 3;
    const ux = POS[b] - POS[a], uy = POS[b + 1] - POS[a + 1], uz = POS[b + 2] - POS[a + 2];
    const vx = POS[c] - POS[a], vy = POS[c + 1] - POS[a + 1], vz = POS[c + 2] - POS[a + 2];
    const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    for (const i of [a, b, c]) { NOR[i] += nx; NOR[i + 1] += ny; NOR[i + 2] += nz; }
    total += Math.sqrt(nx * nx + ny * ny + nz * nz) / 2;
    AREA[t] = total;
  }
  for (let i = 0; i < NOR.length; i += 3) { const l = Math.hypot(NOR[i], NOR[i + 1], NOR[i + 2]) || 1; NOR[i] /= l; NOR[i + 1] /= l; NOR[i + 2] /= l; }
  const SOLID = { pos: POS, idx: IDX, nor: NOR };

  /* ---------- the wrist band, on the arm that is on the viewer's left ---------- */
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]], mul = (a, s) => [a[0] * s, a[1] * s, a[2] * s];
  const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]], dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const norm = (a) => mul(a, 1 / Math.sqrt(dot(a, a))), cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const BAND = K.band, BAND_AX = norm(K.bandAxis), BAND_R = K.bandR + 0.0035;
  let BAND_OUT = norm([-0.62, 0, 0.78]); BAND_OUT = norm(sub(BAND_OUT, mul(BAND_AX, dot(BAND_OUT, BAND_AX))));
  const BAND_SIDE = cross(BAND_AX, BAND_OUT);
  const CHEST_HALF = Math.min(K.chestHalf, 0.18);

  /* ---------- points ---------- */
  const SPACING = 0.0030;                /* least distance between two points on the skin, in metres */
  const SKIN_MAX = 140000;
  const INNER_SPACING = 0.0086;          /* least distance between two points inside the body */
  const INNER_MAX = 70000;
  const CAPACITY = SKIN_MAX + INNER_MAX;
  const stats = { points: 0, skin: 0, inner: 0, ms: 0, done: false };

  /* Which places are inside the body. A fine grid of lines runs through the figure from back to front, and for each
     line the places where it crosses the skin are noted. A place is inside when an odd number of crossings lie behind it. */
  function insideTest() {
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9, z0 = 1e9, z1 = -1e9;
    for (let i = 0; i < POS.length; i += 3) {
      x0 = Math.min(x0, POS[i]); x1 = Math.max(x1, POS[i]);
      y0 = Math.min(y0, POS[i + 1]); y1 = Math.max(y1, POS[i + 1]);
      z0 = Math.min(z0, POS[i + 2]); z1 = Math.max(z1, POS[i + 2]);
    }
    const G = 0.004, ox = x0 - G * 0.37, oy = y0 - G * 0.41;     /* offset, so no line runs exactly through a corner */
    const W = Math.ceil((x1 - ox) / G) + 1, H = Math.ceil((y1 - oy) / G) + 1;
    const visit = (found) => {
      for (let t = 0; t < NT; t++) {
        const a = IDX[t * 3] * 3, b = IDX[t * 3 + 1] * 3, c = IDX[t * 3 + 2] * 3;
        const ax = POS[a], ay = POS[a + 1], bx = POS[b], by = POS[b + 1], cx = POS[c], cy = POS[c + 1];
        const d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
        if (Math.abs(d) < 1e-13) continue;
        const i0 = Math.max(0, Math.floor((Math.min(ax, bx, cx) - ox) / G - 0.5)), i1 = Math.min(W - 1, Math.ceil((Math.max(ax, bx, cx) - ox) / G - 0.5));
        const j0 = Math.max(0, Math.floor((Math.min(ay, by, cy) - oy) / G - 0.5)), j1 = Math.min(H - 1, Math.ceil((Math.max(ay, by, cy) - oy) / G - 0.5));
        for (let j = j0; j <= j1; j++) for (let i = i0; i <= i1; i++) {
          const px = ox + (i + 0.5) * G, py = oy + (j + 0.5) * G;
          const wa = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d;
          const wb = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d;
          const wc = 1 - wa - wb;
          if (wa < 0 || wb < 0 || wc < 0) continue;
          found(j * W + i, wa * POS[a + 2] + wb * POS[b + 2] + wc * POS[c + 2]);
        }
      }
    };
    const first = new Uint32Array(W * H + 1);
    visit((col) => { first[col + 1]++; });
    for (let i = 0; i < W * H; i++) first[i + 1] += first[i];
    const at = first.slice(0, W * H), zs = new Float32Array(first[W * H]);
    visit((col, z) => { zs[at[col]++] = z; });
    const odd = (x, y, z) => {
      const i = Math.floor((x - ox) / G), j = Math.floor((y - oy) / G);
      if (i < 0 || j < 0 || i >= W || j >= H) return false;
      const from = first[j * W + i], to = first[j * W + i + 1];
      if ((to - from) & 1) return false;                         /* a line that grazed the skin: leave it out */
      let behind = 0;
      for (let m = from; m < to; m++) if (zs[m] < z) behind++;
      return (behind & 1) === 1;
    };
    /* a little under the skin on both sides, so no inner point sits on the skin */
    return { box: [x0, x1, y0, y1, z0, z1], has: (x, y, z) => odd(x, y, z - 0.004) && odd(x, y, z + 0.004) };
  }

  /* Deal points over the skin by area, and keep one only if no kept point is closer than SPACING.
     That is what makes the cloud even. Then do the same through the inside of the body, more loosely.
     The work is done a few milliseconds at a time, so the page never stalls and the figure gathers on screen. */
  const cloud = (() => {
    const data = new Float32Array(CAPACITY * 8);
    let n = 0, seed = 20261114;
    const rnd = () => { seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
    const put = (x, y, z, nx, ny, nz, r, kind) => { const o = n * 8; data[o] = x; data[o + 1] = y; data[o + 2] = z; data[o + 3] = nx; data[o + 4] = ny; data[o + 5] = nz; data[o + 6] = r; data[o + 7] = kind; n++; };

    /* the band first: a ring of points and a small flat module, in rows, so it reads as a made thing */
    for (let i = 0; i < 9; i++) for (let j = 0; j < 56; j++) {
      const a = (j + (i % 2) * 0.5) / 56 * 2 * Math.PI, nr = add(mul(BAND_OUT, Math.cos(a)), mul(BAND_SIDE, Math.sin(a)));
      const p = add(BAND, add(mul(BAND_AX, -0.014 + 0.0035 * i), mul(nr, BAND_R)));
      put(p[0], p[1], p[2], nr[0], nr[1], nr[2], 0.5, 1);
    }
    for (let i = 0; i < 11; i++) for (let j = 0; j < 9; j++) {
      const p = add(BAND, add(add(mul(BAND_AX, -0.0175 + 0.0035 * i), mul(BAND_SIDE, -0.014 + 0.0035 * j)), mul(BAND_OUT, BAND_R + 0.007)));
      put(p[0], p[1], p[2], BAND_OUT[0], BAND_OUT[1], BAND_OUT[2], 0.5, 1);
    }

    /* kept points are filed by cell in a small hash table, so the neighbours of a new point are quick to find.
       Skin and inside have a table each, because their points keep different distances. */
    const HSIZE = 1 << 18, head = new Int32Array(HSIZE).fill(-1), next = new Int32Array(CAPACITY).fill(-1);
    const slot = (i, j, k) => ((Math.imul(i, 73856093) ^ Math.imul(j, 19349663) ^ Math.imul(k, 83492791)) >>> 0) & (HSIZE - 1);
    const cell = SPACING, min2 = SPACING * SPACING;
    let throwsLeft = 1150000;
    const head2 = new Int32Array(HSIZE).fill(-1), cell2 = INNER_SPACING, min22 = INNER_SPACING * INNER_SPACING;
    let innerLeft = 900000, inside = null, skinEnd = -1;

    function fillInside(start, budgetMs) {
      if (!inside) { inside = insideTest(); skinEnd = n; }
      const B = inside.box;
      while (innerLeft > 0 && n < CAPACITY) {
        if ((innerLeft & 511) === 0 && performance.now() - start > budgetMs) break;
        innerLeft--;
        const x = B[0] + rnd() * (B[1] - B[0]), y = B[2] + rnd() * (B[3] - B[2]), z = B[4] + rnd() * (B[5] - B[4]);
        if (!inside.has(x, y, z)) continue;
        const ci = Math.floor(x / cell2), cj = Math.floor(y / cell2), ck = Math.floor(z / cell2);
        let free = true;
        for (let i = -1; i <= 1 && free; i++) for (let j = -1; j <= 1 && free; j++) for (let k = -1; k <= 1 && free; k++) {
          for (let m = head2[slot(ci + i, cj + j, ck + k)]; m >= 0; m = next[m]) {
            const ux = data[m * 8] - x, uy = data[m * 8 + 1] - y, uz = data[m * 8 + 2] - z;
            if (ux * ux + uy * uy + uz * uz < min22) { free = false; break; }
          }
        }
        if (!free) continue;
        const h = slot(ci, cj, ck);
        next[n] = head2[h]; head2[h] = n;
        put(x, y, z, 0, 0, 0, rnd(), 2);
      }
    }

    function step(budgetMs) {
      const start = performance.now();
      while (throwsLeft > 0 && n < SKIN_MAX) {
        if ((throwsLeft & 511) === 0 && performance.now() - start > budgetMs) break;
        throwsLeft--;
        /* a triangle chosen by area, then a place inside it */
        const want = rnd() * total;
        let lo = 0, hi = NT - 1;
        while (lo < hi) { const mid = (lo + hi) >> 1; if (AREA[mid] < want) lo = mid + 1; else hi = mid; }
        const a = IDX[lo * 3] * 3, b = IDX[lo * 3 + 1] * 3, c = IDX[lo * 3 + 2] * 3;
        const s = Math.sqrt(rnd()), t = rnd(), wa = 1 - s, wb = s * (1 - t), wc = s * t;
        const x = POS[a] * wa + POS[b] * wb + POS[c] * wc, y = POS[a + 1] * wa + POS[b + 1] * wb + POS[c + 1] * wc, z = POS[a + 2] * wa + POS[b + 2] * wb + POS[c + 2] * wc;
        const ci = Math.floor(x / cell), cj = Math.floor(y / cell), ck = Math.floor(z / cell);
        let free = true;
        for (let i = -1; i <= 1 && free; i++) for (let j = -1; j <= 1 && free; j++) for (let k = -1; k <= 1 && free; k++) {
          for (let m = head[slot(ci + i, cj + j, ck + k)]; m >= 0; m = next[m]) {
            const ux = data[m * 8] - x, uy = data[m * 8 + 1] - y, uz = data[m * 8 + 2] - z;
            if (ux * ux + uy * uy + uz * uz < min2) { free = false; break; }
          }
        }
        if (!free) continue;
        let nx = NOR[a] * wa + NOR[b] * wb + NOR[c] * wc, ny = NOR[a + 1] * wa + NOR[b + 1] * wb + NOR[c + 1] * wc, nz = NOR[a + 2] * wa + NOR[b + 2] * wb + NOR[c + 2] * wc;
        const nl = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
        const h = slot(ci, cj, ck);
        next[n] = head[h]; head[h] = n;
        put(x, y, z, nx / nl, ny / nl, nz / nl, rnd(), 0);
      }
      if (throwsLeft <= 0 || n >= SKIN_MAX) fillInside(start, budgetMs);
      stats.points = n; stats.skin = skinEnd < 0 ? n : skinEnd; stats.inner = n - stats.skin;
      stats.ms = Math.round(stats.ms + performance.now() - start);
      stats.done = skinEnd >= 0 && (innerLeft <= 0 || n >= CAPACITY);
      return stats.done;
    }
    /* points up to skinEnd lie on the skin (the band first), the rest are inside */
    return { data, step, get count() { return n; }, get skinEnd() { return skinEnd < 0 ? n : skinEnd; }, get done() { return stats.done; } };
  })();

  /* ---------- drawing ---------- */
  const F = (v) => v.toFixed(4);
  const CAMERA = `
uniform vec2 uRes;
uniform float uYaw;
uniform vec2 uCenter;
uniform float uViewH;
vec3 ry(vec3 p, float a){ float c = cos(a), s = sin(a); return vec3(p.x*c - p.z*s, p.y, p.x*s + p.z*c); }
vec4 place(vec3 w, float back){ return vec4(2.0*(w.x - uCenter.x)/(uViewH*uRes.x/uRes.y), 2.0*(w.y - uCenter.y)/uViewH, -w.z*1.2 + back, 1.0); }`;
  /* the solid figure, written to depth only and set a few millimetres back so the points on it are not hidden by it */
  const SOLID_VERT = `attribute vec3 aP;${CAMERA}
void main(){ gl_Position = place(ry(aP, -uYaw), 0.008); }`;
  const SOLID_FRAG = "void main(){ gl_FragColor = vec4(0.0); }";
  /* the same figure as plain shaded clay, for checking the shape itself (mount option clay) */
  const CLAY_VERT = `attribute vec3 aP; attribute vec3 aN;${CAMERA}
varying vec3 vN;
void main(){ vN = ry(aN, -uYaw); gl_Position = place(ry(aP, -uYaw), 0.0); }`;
  const CLAY_FRAG = `precision mediump float;
varying vec3 vN;
void main(){ vec3 n = normalize(vN); float l = clamp(dot(n, normalize(vec3(-0.50, 0.48, 0.72)))*0.5 + 0.5, 0.0, 1.0); gl_FragColor = vec4(vec3(0.16 + 0.80*l*l), 1.0); }`;

  const POINT_VERT = `
attribute vec3 aPos;
attribute vec3 aNor;
attribute vec2 aMeta;${CAMERA}
uniform float uBreath;
uniform float uTime;
uniform float uPx;
uniform vec3 uBand;
uniform vec2 uFade;
uniform vec3 uFocus;
uniform float uFocusR;
uniform float uFocusAmt;
varying float vAlpha;
varying vec3 vCol;
varying float vSize;
void main(){
  vec3 p = aPos;
  /* aMeta.y says what a point is: 0 on the skin, 1 on the band, 2 inside the body */
  float rnd = aMeta.x, band = step(0.5, aMeta.y)*step(aMeta.y, 1.5), inner = step(1.5, aMeta.y), body = 1.0 - band;

  /* the chest rises with the breath */
  float chest = exp(-pow((p.y - ${F(K.chestY)})/0.13, 2.0))*smoothstep(${F(CHEST_HALF)}, ${F(CHEST_HALF - 0.05)}, abs(p.x))*smoothstep(-0.2, 0.4, aNor.z);
  p += aNor*(0.006*uBreath*chest*body);

  /* how much of the figure shows: all of it near the top of the view, none near the bottom,
     more around the band and around whatever region is being shown */
  float a = 1.0 - smoothstep(0.0, 1.0, (uFade.x - p.y)/(uFade.x - uFade.y));
  vec3 fb = p - uBand;
  float nearBand = exp(-dot(fb, fb)/(2.0*0.05*0.05))*smoothstep(${F(Math.abs(K.band[0]) * 0.72)}, ${F(Math.abs(K.band[0]) * 0.84)}, -p.x);
  vec3 ff = p - uFocus;
  float fd = dot(ff, ff);
  float glow = uFocusAmt*exp(-fd/(2.0*uFocusR*uFocusR));
  float s = 2.1*uFocusR + 0.05;
  float reveal = uFocusAmt*exp(-fd/(2.0*s*s));
  float vis = clamp(max(max(a, 0.92*nearBand), reveal), 0.0, 1.0);

  /* where the figure fades, its points lift off and drift */
  float lost = (1.0 - vis)*body;
  p += aNor*(lost*lost*0.10*rnd);
  p.y += lost*lost*0.06*(fract(rnd*7.31) - 0.35);
  p += aNor*(0.0008*sin(uTime*0.8 + rnd*50.0)*body);
  p.x += inner*lost*lost*0.05*(fract(rnd*5.17) - 0.5);

  vec3 w = ry(p, -uYaw);
  vec3 n = ry(aNor, -uYaw);

  /* A soft light from the upper left. Every point carries real ink, so the figure reads as filled:
     shaded skin is darker and its points larger, lit skin is lighter, and that difference gives the form.
     Skin that turns away from the viewer is thinned, because points pile up there and would draw a dark rim. */
  float light = clamp(dot(n, normalize(vec3(-0.50, 0.48, 0.72)))*0.5 + 0.5, 0.0, 1.0);
  float sh = 1.0 - smoothstep(0.34, 0.97, light);
  /* the figure is kept even and neutral between the legs, and wherever it is dissolving */
  vec3 fg = p - vec3(${F(K.groin[0])}, ${F(K.groin[1])}, ${F(K.groin[2])});
  sh = mix(sh, 0.30, max(exp(-dot(fg, fg)/(2.0*0.075*0.075)), smoothstep(0.08, 0.6, lost)));
  float facing = smoothstep(-0.04, 0.26, n.z);
  float alpha = mix(0.76, 1.0, sh)*facing;
  float size = mix(1.00, 1.20, sh)*(0.88 + 0.24*fract(rnd*3.7));
  vec3 col = mix(vec3(0.150, 0.262, 0.212), vec3(0.060, 0.104, 0.085), sh);
  float lit = mix(0.72, 1.0, sh)*smoothstep(-0.06, 0.16, n.z);
  /* points inside the body: quieter, and stronger toward the viewer, so the mass has depth */
  if (inner > 0.5){
    float front = clamp(w.z/0.30 + 0.5, 0.0, 1.0);
    alpha = mix(0.42, 0.78, front);
    size = mix(0.84, 1.06, front)*(0.88 + 0.24*fract(rnd*3.7));
    col = vec3(0.160, 0.280, 0.228);
    lit = 0.66; sh = 0.45;
  }
  /* at rest the figure is darkest at the head and lightens going down, the way a figure stands in mist */
  float up = smoothstep(${F(K.hips)}, ${F(K.crown - 0.30)}, p.y);
  alpha *= mix(mix(0.80, 1.0, up), 1.0, uFocusAmt);
  col *= mix(mix(1.12, 0.86, up), 1.0, uFocusAmt);
  if (band > 0.5){ alpha = 0.95*smoothstep(-0.10, 0.02, n.z); size = 1.0; col = vec3(0.07, 0.078, 0.072); }

  /* the region being shown turns green and everything else steps back. Light and shade stay, so the form does too */
  alpha *= mix(1.0, 0.60, uFocusAmt*(1.0 - glow));
  col = mix(col, mix(vec3(0.130, 0.470, 0.265), vec3(0.045, 0.235, 0.132), sh), glow);
  alpha = mix(alpha, max(alpha, lit), glow);
  size *= 1.0 + 0.20*glow;
  alpha *= vis;

  gl_Position = place(w, 0.0);
  if (alpha < 0.012) gl_Position = vec4(3.0, 3.0, 0.0, 1.0);
  vSize = max(1.0, uPx*size);
  gl_PointSize = vSize;
  vAlpha = alpha;
  vCol = col;
}`;
  const POINT_FRAG = `
precision mediump float;
varying float vAlpha;
varying vec3 vCol;
varying float vSize;
void main(){
  float d = length(gl_PointCoord - 0.5)*2.0;
  float a = vAlpha*(1.0 - smoothstep(1.0 - min(0.9, 2.2/vSize), 1.0, d));
  gl_FragColor = vec4(vCol*a, a);
}`;
  /* a soft light behind the region being shown */
  const AURA_VERT = "attribute vec2 aTri; void main(){ gl_Position = vec4(aTri, 0.0, 1.0); }";
  const AURA_FRAG = `
precision mediump float;
uniform vec2 uC;
uniform float uR;
uniform float uA;
void main(){
  vec2 d = gl_FragCoord.xy - uC;
  float a = uA*exp(-dot(d, d)/(2.0*uR*uR));
  gl_FragColor = vec4(vec3(0.886, 0.961, 0.361)*a, a);
}`;

  /* Resting view: the figure stands straight on, centred, head and trunk filling the height it is given.
     It is whole down to the waist and has dissolved by the hips. mount() can be told how much room to keep clear
     above and below the figure (opts.frame, in pixels), and the view is then set so the figure fits between them. */
  const REST = { yaw: 0, viewH: 1.55, cy: K.crown - 0.50, ax: 0.5 };
  const WHOLE = K.waist, GONE = K.hips;          /* heights in metres where the resting dissolve starts and where it ends */
  const FADE = [0, 0];                           /* the same two places as shares of the view height below centre */
  const setFade = () => { FADE[0] = (REST.cy - WHOLE) / REST.viewH; FADE[1] = (REST.cy - GONE) / REST.viewH; };
  setFade();

  /* c: centre in metres, measured on the mesh. r: size in metres.
     view: visible height in metres when the region is shown. yaw: turn that brings it to the camera. */
  const REGIONS = {
    head:     { c: K.head, r: 0.105, view: 0.96 },
    eyes:     { c: K.eyes, r: 0.045, view: 0.84 },
    ear:      { c: K.ear, r: 0.038, view: 0.84, yaw: 1.25 },
    neck:     { c: K.neck, r: 0.055, view: 0.90 },
    shoulder: { c: K.shoulder, r: 0.075, view: 0.90, yaw: 0.35 },
    heart:    { c: K.heart, r: 0.068, view: 1.05, yaw: -0.18 },
    lungs:    { c: K.lungs, r: 0.120, view: 1.10 },
    abdomen:  { c: K.abdomen, r: 0.100, view: 1.05 },
    flank:    { c: K.flank, r: 0.080, view: 1.00, yaw: 2.1 },
    back:     { c: K.back, r: 0.105, view: 1.05, yaw: Math.PI },
    wrist:    { c: BAND, r: 0.048, view: 0.80, yaw: 0.5 },
    hand:     { c: K.hand, r: 0.065, view: 0.84, yaw: 0.5 },
    legs:     { c: K.legs, r: 0.260, view: 1.55 },
    knee:     { c: K.knee, r: 0.075, view: 0.92, yaw: 0.4 },
    ankle:    { c: K.ankle, r: 0.065, view: 0.90, yaw: 0.5 },
  };

  /* A place that is not a region: the face of the band, for a mark that sits on it */
  const ANCHORS = {
    band: { c: add(BAND, mul(BAND_OUT, BAND_R + 0.008)) },
  };

  const cam = { yaw: REST.yaw, sway: 0, viewH: REST.viewH, cx: 0, cy: REST.cy, W: 1, H: 1, amt: 0, focus: [0, 0, 0], focusR: 0.1 };

  /* model point of a region or an anchor */
  function point(name) {
    const d = REGIONS[name] || ANCHORS[name];
    return d ? d.c.slice() : null;
  }
  /* same projection the points use, so labels sit on real places on the figure */
  function project(p) {
    const yaw = cam.yaw + cam.sway;
    const xw = p[0] * Math.cos(yaw) + p[2] * Math.sin(yaw);
    const zw = -p[0] * Math.sin(yaw) + p[2] * Math.cos(yaw);
    const ppm = cam.H / cam.viewH;
    return { x: cam.W / 2 + (xw - cam.cx) * ppm, y: cam.H / 2 - (p[1] - cam.cy) * ppm, facing: zw, scale: ppm };
  }
  function restCx(viewH) { return -(REST.ax - 0.5) * (cam.W / cam.H) * viewH; }

  function mount(canvas, opts) {
    opts = opts || {};
    const gl = canvas.getContext("webgl", { alpha: true, premultipliedAlpha: true, antialias: false, depth: true });
    if (!gl) return null;
    const compile = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const program = (vs, fs, attribs) => {
      const p = gl.createProgram();
      gl.attachShader(p, compile(gl.VERTEX_SHADER, vs));
      gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fs));
      attribs.forEach((name, i) => gl.bindAttribLocation(p, i, name));
      gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
      return p;
    };
    const uniforms = (p, names) => { const u = {}; names.forEach((n) => (u[n] = gl.getUniformLocation(p, n))); return u; };
    const VIEW = ["uRes", "uYaw", "uCenter", "uViewH"];

    const solid = opts.clay ? program(CLAY_VERT, CLAY_FRAG, ["aP", "aN"]) : program(SOLID_VERT, "precision mediump float;" + SOLID_FRAG, ["aP"]);
    const points = program(POINT_VERT, POINT_FRAG, ["aPos", "aNor", "aMeta"]);
    const aura = program(AURA_VERT, AURA_FRAG, ["aTri"]);
    const US = uniforms(solid, VIEW);
    const U = uniforms(points, VIEW.concat(["uBreath", "uTime", "uPx", "uBand", "uFade", "uFocus", "uFocusR", "uFocusAmt"]));
    const UA = uniforms(aura, ["uC", "uR", "uA"]);

    const solidBuf = gl.createBuffer(), clayBuf = gl.createBuffer(), indexBuf = gl.createBuffer();
    let solidCount = 0, solidType = gl.UNSIGNED_SHORT;
    const triBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, triBuf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const pointBuf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, pointBuf);
    gl.bufferData(gl.ARRAY_BUFFER, cloud.data.byteLength, gl.DYNAMIC_DRAW);
    let sent = 0;
    /* make some more of the cloud if any is left to make, and hand what is new to the GPU */
    function gather(budgetMs) {
      if (!cloud.done) cloud.step(budgetMs);
      let grew = false;
      if (SOLID && !solidCount) {
        gl.bindBuffer(gl.ARRAY_BUFFER, solidBuf);
        gl.bufferData(gl.ARRAY_BUFFER, SOLID.pos, gl.STATIC_DRAW);
        if (opts.clay) { gl.bindBuffer(gl.ARRAY_BUFFER, clayBuf); gl.bufferData(gl.ARRAY_BUFFER, SOLID.nor, gl.STATIC_DRAW); }
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuf);
        gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, SOLID.idx, gl.STATIC_DRAW);
        solidCount = SOLID.idx.length; solidType = SOLID.idx instanceof Uint32Array ? gl.UNSIGNED_INT : gl.UNSIGNED_SHORT;
        grew = true;
      }
      if (sent < cloud.count) {
        gl.bindBuffer(gl.ARRAY_BUFFER, pointBuf);
        gl.bufferSubData(gl.ARRAY_BUFFER, sent * 32, cloud.data.subarray(sent * 8, cloud.count * 8));
        sent = cloud.count;
        grew = true;
      }
      return grew;
    }
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.depthFunc(gl.LEQUAL);
    gl.clearColor(0, 0, 0, 0);
    gl.clearDepth(1);

    const still = !!opts.still;
    /* a fixed view, for checking the shape itself: { yaw, viewH, cy } */
    const fixed = opts.view || null;
    const target = { yaw: REST.yaw, viewH: REST.viewH, cx: 0, cy: REST.cy, amt: 0 };
    let focused = null, moveCamera = true, focusAx = 0.5, running = false, last = 0, t0 = performance.now(), settled = false;

    function aim() {
      const reg = focused && REGIONS[focused];
      if (reg && moveCamera) {
        const p = point(focused);
        target.yaw = reg.yaw == null ? REST.yaw : reg.yaw;
        target.viewH = reg.view;
        const xw = p[0] * Math.cos(target.yaw) + p[2] * Math.sin(target.yaw);
        target.cx = xw - (focusAx - 0.5) * (cam.W / cam.H) * target.viewH;
        target.cy = p[1] + 0.025 * target.viewH;
      } else if (fixed) {
        target.yaw = fixed.yaw || 0; target.viewH = fixed.viewH; target.cx = 0; target.cy = fixed.cy;
      } else {
        target.yaw = REST.yaw; target.viewH = REST.viewH; target.cx = restCx(REST.viewH); target.cy = REST.cy;
      }
      target.amt = reg ? 1 : 0;
      if (reg) { cam.focus = point(focused); cam.focusR = reg.r; }
      settled = false;
    }

    function resize() {
      const r = canvas.getBoundingClientRect();
      cam.W = Math.max(2, r.width); cam.H = Math.max(2, r.height);
      const scale = Math.min(window.devicePixelRatio || 1, opts.maxScale || 2);
      canvas.width = Math.round(cam.W * scale);
      canvas.height = Math.round(cam.H * scale);
      gl.viewport(0, 0, canvas.width, canvas.height);
      const room = typeof opts.frame === "function" ? opts.frame() : opts.frame;
      if (room) {
        /* fit what shows of the figure, crown to the end of the dissolve (to the soles when whole), between the two margins */
        const ppm = Math.min(Math.max(120, cam.H - room.top - room.bottom) / (K.crown - (opts.whole ? K.ground : GONE)), cam.W * 0.74 / 0.56);
        REST.viewH = cam.H / ppm;
        REST.cy = K.crown - (cam.H / 2 - room.top) / ppm;
        setFade();
      }
      aim();
      if (cam.amt === 0 && !focused) { cam.cx = target.cx; cam.cy = target.cy; cam.viewH = target.viewH; cam.yaw = target.yaw; }
      settled = false;
    }

    function step(dt) {
      const k = still ? 1 : 1 - Math.exp(-dt / 0.26);
      const before = cam.yaw + cam.viewH + cam.cx + cam.cy + cam.amt;
      cam.yaw += (target.yaw - cam.yaw) * k;
      cam.viewH += (target.viewH - cam.viewH) * k;
      cam.cx += (target.cx - cam.cx) * k;
      cam.cy += (target.cy - cam.cy) * k;
      cam.amt += (target.amt - cam.amt) * (still ? 1 : 1 - Math.exp(-dt / 0.18));
      return Math.abs(cam.yaw + cam.viewH + cam.cx + cam.cy + cam.amt - before) > 1e-5;
    }

    function view(u) {
      gl.uniform2f(u.uRes, canvas.width, canvas.height);
      gl.uniform1f(u.uYaw, cam.yaw + cam.sway);
      gl.uniform2f(u.uCenter, cam.cx, cam.cy);
      gl.uniform1f(u.uViewH, cam.viewH);
    }

    function draw(seconds) {
      const breath = still ? 0.5 : 0.5 - 0.5 * Math.cos((2 * Math.PI * (opts.breathsPerMin || 14) / 60) * seconds);
      cam.sway = still ? 0 : 0.10 * Math.sin(seconds * 0.31) * (1 - cam.amt);
      const scale = canvas.width / cam.W;
      gl.colorMask(true, true, true, true);
      gl.depthMask(true);
      gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

      if (opts.clay) {
        if (!solidCount) return;
        gl.enable(gl.DEPTH_TEST); gl.disable(gl.BLEND);
        gl.useProgram(solid);
        gl.enableVertexAttribArray(0); gl.enableVertexAttribArray(1); gl.disableVertexAttribArray(2);
        gl.bindBuffer(gl.ARRAY_BUFFER, solidBuf); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
        gl.bindBuffer(gl.ARRAY_BUFFER, clayBuf); gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 0, 0);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuf);
        view(US);
        gl.drawElements(gl.TRIANGLES, solidCount, solidType, 0);
        return;
      }

      /* the light behind the region being shown */
      gl.disable(gl.DEPTH_TEST);
      gl.enable(gl.BLEND);
      gl.enableVertexAttribArray(0); gl.disableVertexAttribArray(1); gl.disableVertexAttribArray(2);
      if (cam.amt > 0.004) {
        const c = project(cam.focus);
        gl.useProgram(aura);
        gl.bindBuffer(gl.ARRAY_BUFFER, triBuf);
        gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
        gl.uniform2f(UA.uC, c.x * scale, (cam.H - c.y) * scale);
        gl.uniform1f(UA.uR, Math.max(30, cam.focusR * c.scale * 1.2) * scale);
        gl.uniform1f(UA.uA, 0.66 * cam.amt * Math.min(1, Math.sqrt(0.09 / cam.focusR)));
        gl.drawArrays(gl.TRIANGLES, 0, 3);
      }

      /* the solid figure, to depth only */
      gl.enable(gl.DEPTH_TEST);
      if (solidCount) {
        gl.disable(gl.BLEND);
        gl.colorMask(false, false, false, false);
        gl.useProgram(solid);
        gl.bindBuffer(gl.ARRAY_BUFFER, solidBuf);
        gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuf);
        view(US);
        gl.drawElements(gl.TRIANGLES, solidCount, solidType, 0);
      }

      /* the points */
      gl.colorMask(true, true, true, true);
      gl.depthMask(false);
      gl.enable(gl.BLEND);
      gl.useProgram(points);
      gl.bindBuffer(gl.ARRAY_BUFFER, pointBuf);
      gl.enableVertexAttribArray(1); gl.enableVertexAttribArray(2);
      gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 32, 0);
      gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 32, 12);
      gl.vertexAttribPointer(2, 2, gl.FLOAT, false, 32, 24);
      view(U);
      gl.uniform1f(U.uBreath, breath);
      gl.uniform1f(U.uTime, still ? 0 : seconds);
      /* points grow with the figure, and a little less than the view when it moves in */
      const grow = Math.min(1.7, Math.max(0.8, cam.H / REST.viewH / 474));
      gl.uniform1f(U.uPx, scale * 1.48 * grow * Math.pow(REST.viewH / cam.viewH, 0.80));
      gl.uniform3f(U.uBand, BAND[0], BAND[1], BAND[2]);
      if (opts.whole) gl.uniform2f(U.uFade, -8, -9);
      else gl.uniform2f(U.uFade, cam.cy - FADE[0] * cam.viewH, cam.cy - FADE[1] * cam.viewH);
      gl.uniform3f(U.uFocus, cam.focus[0], cam.focus[1], cam.focus[2]);
      gl.uniform1f(U.uFocusR, cam.focusR);
      gl.uniform1f(U.uFocusAmt, cam.amt);
      /* the inside first and through everything, then the skin, which the solid figure hides where it faces away */
      const skin = Math.min(sent, cloud.skinEnd);
      if (sent > skin) { gl.disable(gl.DEPTH_TEST); gl.drawArrays(gl.POINTS, skin, sent - skin); gl.enable(gl.DEPTH_TEST); }
      gl.drawArrays(gl.POINTS, 0, skin);
    }

    function frame(now) {
      if (!running) return;
      requestAnimationFrame(frame);
      if (now - last < 32) return;                 /* about 30 frames a second is plenty for a slow breath */
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const grew = gather(14);
      const moved = step(dt) || grew;
      if (still && settled && !moved) return;
      draw((now - t0) / 1000);
      settled = !moved;
      if (opts.onFrame) opts.onFrame(moved);
    }

    const api = {
      resize,
      focus(name, o) {
        focused = name && REGIONS[name] ? name : null;
        moveCamera = !(o && o.camera === false);
        focusAx = o && o.ax != null ? o.ax : 0.5;
        aim();
      },
      get focused() { return focused; },
      start() { if (running) return; running = true; last = performance.now() - 40; requestAnimationFrame(frame); },
      stop() { running = false; },
      /* one synchronous frame, used on load and by tests */
      renderNow(jump) {
        if (still) while (!cloud.done) cloud.step(40);       /* nothing animates in this mode, so finish the cloud first */
        gather(0);
        if (jump) { aim(); cam.yaw = target.yaw; cam.viewH = target.viewH; cam.cx = target.cx; cam.cy = target.cy; cam.amt = target.amt; }
        draw((performance.now() - t0) / 1000);
        if (opts.onFrame) opts.onFrame(true);
      },
    };
    resize();
    return api;
  }

  /* Which region is under a point of the canvas, judged by distance to region centres that face the camera */
  function regionAt(x, y, names) {
    let best = null, bestScore = 1;
    (names || Object.keys(REGIONS)).forEach((name) => {
      const p = project(point(name));
      if (p.facing < -0.02) return;
      const rad = Math.max(14, REGIONS[name].r * p.scale);
      const score = Math.hypot(x - p.x, y - p.y) / rad;
      if (score < bestScore) { bestScore = score; best = name; }
    });
    return best;
  }

  window.BodyRender = { mount, project, point, regionAt, REGIONS, ANCHORS, cam, stats };
})();

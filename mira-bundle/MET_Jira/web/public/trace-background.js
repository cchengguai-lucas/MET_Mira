(function () {
  const win = window;
  const doc = win.document;

  // Optional per-app overrides, set before this loads. See README.md
  // "Configuration".
  const OPT = win.__miraLoginTheme || {};
  const PANEL_SELECTOR = OPT.panelSelector || ".mira-login-panel";

  // A React route can mount twice (StrictMode, remounts); never stack loops.
  if (win.__miraTraceBg && typeof win.__miraTraceBg.destroy === "function") {
    win.__miraTraceBg.destroy();
  }

  const CONF = {
    // Children per node at each depth: seed (main goal) -> trunk -> main
    // branches -> limbs -> sub-branches -> twigs (sub-tasks).
    // [1, 3, 3, 3, 2] gives 95 nodes and 54 twigs.
    branching: [1, 3, 3, 3, 2],
    // Segment length per depth, relative; the tree is then scaled to a
    // height of 1 world unit.
    branchLen: [0.3, 0.27, 0.21, 0.15, 0.1],
    lenJitter: 0.12,
    // Fork half-angle (rad) per depth; the trunk does not fork.
    spread: [0, 0.9, 0.8, 0.72, 0.62],
    // Pull of each fork back towards "up", so the crown grows upward.
    upBias: 0.2,
    trunkLean: 0.1,
    // Leaf cluster at every twig tip: count and scatter radius.
    leafCount: 4,
    leafRadius: 0.026,
    thermalAmp: 0.003,
    // Tree height as a share of the window height, and where the tree's
    // vertical middle sits (share of the height from the top).
    heightFill: 0.86,
    narrowHeightFill: 0.78,
    centreAt: 0.47,
    // Horizontal position of the trunk (share of the width) when the side
    // panel is shown; nudged left so more of the crown is in the open.
    trunkAt: 0.45,
    // Timeline. The seed pops in at rootPop. Each branch then grows out of
    // its parent's tip over growDur[depth], siblings `siblingStagger` apart,
    // and a node pops at the tip when its branch arrives; twigs then unfold
    // their leaves. After `settle`, twigs complete `traceGap` apart in random
    // order; each sends a pulse down its branch over traceSeg. A node
    // completes once all its children's pulses have arrived, so completion
    // runs down to the seed. The achieved tree holds for achieveHold, fades,
    // rests, and a new tree grows.
    rootPop: 0.4,
    growDur: [1.3, 0.95, 0.75, 0.6, 0.45],
    siblingStagger: 0.12,
    growJitter: 0.15,
    popDur: 0.35,
    settle: 0.6,
    traceLead: 0.5,
    traceGap: 0.13,
    traceSeg: 0.34,
    achieveHold: 3.6,
    fadeSpan: 1.6,
    rest: 0.6,
    spin: 0.07,
    focal: 2.7,
    baseYaw: 0.62,
    // Nearly side-on, slightly from above, so the tree reads as standing.
    basePitch: 0.16,
    pitchMin: -1.2,
    pitchMax: 1.4,
    parallaxYaw: 0.30,
    parallaxPitch: 0.10,
    dragYawScale: 0.005,
    dragPitchScale: 0.004,
    // Branch stroke width per depth: the trunk thickest, twigs thinnest.
    // Branch thickness (px) at a node of each depth. A branch tapers from its
    // parent's thickness to its child's: a flared trunk, hair-thin twigs.
    branchWidth: [18, 9, 5.5, 3.4, 2.0, 1.1],
    // Soft halo stroke around every branch: width multiple and opacity.
    edgeGlow: 3.2,
    edgeGlowAlpha: 0.12,
    atomPx: 3.4,
    // Glow size per depth: a large seed, small forks, larger twig tips.
    nodeScale: [3.0, 0.9, 0.75, 0.65, 0.6, 0.9],
    leafScale: 0.55,
    maxSizePersp: 2.0,
    glowRatio: 7,
    dprCap: 1.75,
    // Below this width the panel spans the window; must match login.css.
    sidePanelMinWidth: 900,
    cursorRadius: 210,
    cursorPush: 26,
    zoomMin: 0.55,
    zoomMax: 3.2,
    dim: 1.0
  };

  // One tone per depth, all in the lattice blues; warm marks completion.
  const COLORS = {
    stages: [[240, 244, 252], [150, 196, 248], [160, 200, 248], [176, 210, 249], [198, 221, 250], [222, 232, 250]],
    bond: [168, 205, 245],
    pulse: [232, 244, 255],
    done: [255, 210, 160],
    flash: [255, 236, 214]
  };

  Object.assign(CONF, OPT.conf || {});
  Object.assign(COLORS, OPT.colors || {});

  let style = doc.getElementById("mira-login-bg-style");
  if (!style) {
    style = doc.createElement("style");
    style.id = "mira-login-bg-style";
    doc.head.appendChild(style);
  }
  style.textContent = `
    html.mira-grab{cursor:url("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAb0lEQVR4nGNgoBAw4pIQERG5hMx/8+aNHlEGwDS+fv1aF1lcVFT0Mj6D4Jr/EwDoLmPCaRqRYBAZgO43YgELTDN6qJPsAmKAqKjoZfRoZGJggMQtLJ7JdgG5hlAcCxgAV2rEFUtYMxM2xXjzACUAADh2WTJLy24rAAAAAElFTkSuQmCC") 6 2, grab}
    html.mira-grabbing,html.mira-grabbing *{cursor:url("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAZUlEQVR4nGNgGAWM2ARFREQuYRN/8+aNHkETRURELv3HAXAZTJRmXIYwIWt+/fq1LkEnogEmSjSjuIBcwMTAAAldUVHRyxS5gFxDKPYCBhAREbmEKzqxpQOsKRFmELoYUSmRVAAAszJrJcokWWoAAAAASUVORK5CYII=") 7 6, grabbing!important;user-select:none!important}
    #mira-login-bg{position:fixed;inset:0;z-index:0;overflow:hidden;pointer-events:none;background:#05070b}
    #mira-login-bg canvas{display:block;width:100%;height:100%}
    @media (max-width:900px){#mira-login-bg canvas{opacity:.5}}
  `;

  let host = doc.getElementById("mira-login-bg");
  if (!host) {
    host = doc.createElement("div");
    host.id = "mira-login-bg";
    host.setAttribute("aria-hidden", "true");
    doc.body.prepend(host);
  }
  host.innerHTML = "";
  const canvas = doc.createElement("canvas");
  host.appendChild(canvas);
  const ctx = canvas.getContext("2d");

  const clamp = function (v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); };
  const rand = Math.random;

  // ---- pre-rendered glow sprites -----------------------------------------
  const SPRITE = 64;
  function makeSprite(rgb) {
    const c = doc.createElement("canvas");
    c.width = SPRITE;
    c.height = SPRITE;
    const g = c.getContext("2d");
    const half = SPRITE / 2;
    const grad = g.createRadialGradient(half, half, 0, half, half, half);
    const s = rgb[0] + "," + rgb[1] + "," + rgb[2];
    grad.addColorStop(0, "rgba(" + s + ",1)");
    grad.addColorStop(0.16, "rgba(" + s + ",0.55)");
    grad.addColorStop(0.42, "rgba(" + s + ",0.15)");
    grad.addColorStop(1, "rgba(" + s + ",0)");
    g.fillStyle = grad;
    g.fillRect(0, 0, SPRITE, SPRITE);
    return c;
  }
  const stageSprites = COLORS.stages.map(makeSprite);
  const sprites = {
    pulse: makeSprite(COLORS.pulse),
    done: makeSprite(COLORS.done),
    flash: makeSprite(COLORS.flash)
  };

  // ---- tree topology (fixed) ----------------------------------------------
  // Breadth-first, so a parent always has a lower index than its children.
  const maxDepth = CONF.branching.length;
  const nodeDepth = [0];
  const nodeParent = [-1];
  const siblingIndex = [0];
  let frontier = [0];
  for (let d = 1; d <= maxDepth; d++) {
    const next = [];
    for (let f = 0; f < frontier.length; f++) {
      for (let i = 0; i < CONF.branching[d - 1]; i++) {
        next.push(nodeDepth.length);
        nodeDepth.push(d);
        nodeParent.push(frontier[f]);
        siblingIndex.push(i);
      }
    }
    frontier = next;
  }
  const nodeCount = nodeDepth.length;
  const leaves = frontier;
  const leafSlot = new Int32Array(nodeCount).fill(-1);
  leaves.forEach(function (leaf, i) { leafSlot[leaf] = i; });
  const LEAF_COUNT = CONF.leafCount;

  // ---- tree shape (new every cycle) ---------------------------------------
  function norm(v) {
    const l = Math.hypot(v[0], v[1], v[2]) || 1;
    return [v[0] / l, v[1] / l, v[2] / l];
  }
  function cross(a, b) {
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  }

  // World +y is screen-down, so "up" is -y.
  const UP = [0, -1, 0];
  const nodeDir = [];
  const nodePos = [];
  const phase = new Float32Array(nodeCount * 3);
  const leafOff = new Float32Array(leaves.length * LEAF_COUNT * 3);

  function shapeTree() {
    nodeDir[0] = UP;
    nodePos[0] = [0, 0, 0];
    const turnOf = new Float32Array(nodeCount);
    for (let n = 0; n < nodeCount; n++) turnOf[n] = rand() * Math.PI * 2;
    for (let n = 1; n < nodeCount; n++) {
      const d = nodeDepth[n];
      const p = nodeParent[n];
      const i = siblingIndex[n];
      const k = CONF.branching[d - 1];
      const pd = nodeDir[p];
      let dir;
      if (k === 1) {
        // A single child continues its parent (the trunk), with a slight lean.
        dir = norm([
          pd[0] + (rand() - 0.5) * CONF.trunkLean,
          pd[1],
          pd[2] + (rand() - 0.5) * CONF.trunkLean
        ]);
      } else {
        // Fork: fan the children around the parent's direction, then bend
        // them back towards the light so the crown grows upward.
        const helper = Math.abs(pd[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0];
        const u = norm(cross(pd, helper));
        const v = cross(pd, u);
        const ang = turnOf[p] + (Math.PI * 2 * i) / k + (rand() - 0.5) * 0.6;
        const s = CONF.spread[d - 1] * (0.8 + 0.4 * rand());
        const cs = Math.cos(s), sn = Math.sin(s);
        dir = norm([
          pd[0] * cs + (u[0] * Math.cos(ang) + v[0] * Math.sin(ang)) * sn + UP[0] * CONF.upBias,
          pd[1] * cs + (u[1] * Math.cos(ang) + v[1] * Math.sin(ang)) * sn + UP[1] * CONF.upBias,
          pd[2] * cs + (u[2] * Math.cos(ang) + v[2] * Math.sin(ang)) * sn + UP[2] * CONF.upBias
        ]);
      }
      const len = CONF.branchLen[d - 1] * (1 + (rand() * 2 - 1) * CONF.lenJitter);
      const pp = nodePos[p];
      nodeDir[n] = dir;
      nodePos[n] = [pp[0] + dir[0] * len, pp[1] + dir[1] * len, pp[2] + dir[2] * len];
    }
    // Scale every tree to a height of 1 and centre it vertically on the
    // origin, so each random shape fills the same share of the window.
    let minY = Infinity, maxY = -Infinity;
    for (let n = 0; n < nodeCount; n++) {
      minY = Math.min(minY, nodePos[n][1]);
      maxY = Math.max(maxY, nodePos[n][1]);
    }
    const mid = (minY + maxY) / 2;
    const fit = 1 / Math.max(maxY - minY, 1e-3);
    for (let n = 0; n < nodeCount; n++) {
      const q = nodePos[n];
      q[0] *= fit;
      q[1] = (q[1] - mid) * fit;
      q[2] *= fit;
    }

    for (let k = 0; k < nodeCount * 3; k++) phase[k] = rand() * Math.PI * 2;
    // Leaf clusters: a few small glows scattered around each twig tip.
    for (let k = 0; k < leafOff.length; k += 3) {
      let dx, dy, dz, l;
      do {
        dx = rand() * 2 - 1;
        dy = rand() * 2 - 1;
        dz = rand() * 2 - 1;
        l = Math.hypot(dx, dy, dz);
      } while (l > 1 || l < 0.3);
      leafOff[k] = dx * CONF.leafRadius;
      leafOff[k + 1] = dy * CONF.leafRadius;
      leafOff[k + 2] = dz * CONF.leafRadius;
    }
  }

  // ---- timeline (new every cycle) -----------------------------------------
  const growStart = new Float32Array(nodeCount);
  const arrive = new Float32Array(nodeCount);
  const closeAt = new Float32Array(nodeCount);
  const tl = { grown: 0, achieved: 0, fadeStart: 0, cycle: 1 };

  function schedule() {
    shapeTree();
    growStart[0] = 0;
    arrive[0] = CONF.rootPop;
    let lastArrival = arrive[0];
    for (let n = 1; n < nodeCount; n++) {
      growStart[n] = arrive[nodeParent[n]] + siblingIndex[n] * CONF.siblingStagger +
        rand() * CONF.growJitter;
      arrive[n] = growStart[n] + CONF.growDur[nodeDepth[n] - 1];
      lastArrival = Math.max(lastArrival, arrive[n]);
    }
    tl.grown = lastArrival + CONF.settle;

    const order = leaves.slice();
    for (let i = order.length - 1; i > 0; i--) {
      const j = (rand() * (i + 1)) | 0;
      const t = order[i]; order[i] = order[j]; order[j] = t;
    }
    closeAt.fill(-Infinity);
    order.forEach(function (leaf, i) {
      closeAt[leaf] = tl.grown + CONF.traceLead + i * CONF.traceGap;
    });
    for (let n = nodeCount - 1; n > 0; n--) {
      const p = nodeParent[n];
      closeAt[p] = Math.max(closeAt[p], closeAt[n] + CONF.traceSeg);
    }
    tl.achieved = closeAt[0];
    tl.fadeStart = tl.achieved + CONF.achieveHold;
    tl.cycle = tl.fadeStart + CONF.fadeSpan + CONF.rest;
  }

  // Per-frame screen state.
  const nodeX = new Float32Array(nodeCount);
  const nodeY = new Float32Array(nodeCount);
  const nodeP = new Float32Array(nodeCount);
  const leafX = new Float32Array(leaves.length * LEAF_COUNT);
  const leafY = new Float32Array(leaves.length * LEAF_COUNT);
  const leafP = new Float32Array(leaves.length * LEAF_COUNT);

  // ---- layout -------------------------------------------------------------
  let W = 0, H = 0, dpr = 1, worldScale = 1, sizeScale = 1, centreX = 0, centreY = 0;

  function layout() {
    dpr = Math.min(win.devicePixelRatio || 1, CONF.dprCap);
    W = win.innerWidth;
    H = win.innerHeight;
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    const wide = W >= CONF.sidePanelMinWidth;
    // Centre near the middle of the full window, as the lattice theme does,
    // so the outer branches pass behind the glass panel. The tree is sized
    // by the window height so it towers over the page; the crown may run
    // off the sides.
    centreX = W * (wide ? CONF.trunkAt : 0.5);
    centreY = H * CONF.centreAt;
    worldScale = H * (wide ? CONF.heightFill : CONF.narrowHeightFill);
    sizeScale = clamp(H / 820, 0.8, 1.5);
  }

  // ---- projection ---------------------------------------------------------
  const cam = { cy: 1, sy: 0, cp: 1, sp: 0 };
  const out = { x: 0, y: 0, persp: 1 };

  function project(x, y, z) {
    const x1 = x * cam.cy + z * cam.sy;
    const z1 = z * cam.cy - x * cam.sy;
    const y2 = y * cam.cp - z1 * cam.sp;
    const z2 = y * cam.sp + z1 * cam.cp;
    const denom = CONF.focal + z2;
    if (denom < 0.25) return false;
    const persp = CONF.focal / denom;
    const scale = worldScale * view.zoom;
    out.x = centreX + x1 * persp * scale;
    out.y = centreY + y2 * persp * scale;
    out.persp = persp;
    return true;
  }

  function depthOf(persp) { return clamp((persp - 0.72) / 0.88, 0, 1); }

  // Cursor repulsion, in screen space. The canvas is pointer-events:none, so
  // the position comes from a document-level listener.
  const pointer = {
    x: -1e4, y: -1e4, nx: 0, ny: 0,
    fx: -1e4, fy: -1e4, strength: 0, inside: false,
    dragging: false, lastX: 0, lastY: 0
  };
  const disp = { dx: 0, dy: 0 };

  function displace(sx, sy) {
    disp.dx = 0;
    disp.dy = 0;
    if (pointer.strength < 0.01) return;
    const vx = sx - pointer.fx;
    const vy = sy - pointer.fy;
    const d2 = vx * vx + vy * vy;
    const r = CONF.cursorRadius;
    if (d2 >= r * r || d2 <= 1e-4) return;
    const d = Math.sqrt(d2);
    const t = 1 - d / r;
    const f = t * t * t * (t * (t * 6 - 15) + 10);
    const push = (CONF.cursorPush * pointer.strength * f) / d;
    disp.dx = vx * push;
    disp.dy = vy * push;
  }

  // ---- drawing ------------------------------------------------------------
  function smooth(e0, e1, x) {
    const t = clamp((x - e0) / (e1 - e0), 0, 1);
    return t * t * (3 - 2 * t);
  }
  function easeOutBack(p) {
    const c = 1.7;
    const q = p - 1;
    return 1 + (c + 1) * q * q * q + c * q * q;
  }
  function easeOutCubic(p) {
    const q = 1 - p;
    return 1 - q * q * q;
  }
  function mix(a, b, t) {
    return Math.round(a[0] + (b[0] - a[0]) * t) + "," +
      Math.round(a[1] + (b[1] - a[1]) * t) + "," +
      Math.round(a[2] + (b[2] - a[2]) * t);
  }

  // 0 before the fade, 1 once the achieved tree has fully faded.
  function fadeOf(ct) { return smooth(tl.fadeStart, tl.fadeStart + CONF.fadeSpan, ct); }
  // Warm afterglow of the achieved goal, until the fade ends.
  function glowOf(ct) { return smooth(tl.achieved, tl.achieved + 0.6, ct) * (1 - fadeOf(ct)); }

  function drawBackdrop(ct) {
    ctx.clearRect(0, 0, W, H);
    ctx.globalCompositeOperation = "lighter";
    const reach = worldScale * view.zoom * 1.4;
    const haze = ctx.createRadialGradient(centreX, centreY, 0, centreX, centreY, reach);
    haze.addColorStop(0, "rgba(46,74,120,0.30)");
    haze.addColorStop(0.42, "rgba(24,38,66,0.14)");
    haze.addColorStop(1, "rgba(5,7,11,0)");
    ctx.fillStyle = haze;
    ctx.fillRect(0, 0, W, H);
    const g = glowOf(ct);
    if (g > 0.01) {
      const warm = ctx.createRadialGradient(centreX, centreY, 0, centreX, centreY, reach * 0.8);
      warm.addColorStop(0, "rgba(" + COLORS.done.join(",") + "," + (0.14 * g).toFixed(3) + ")");
      warm.addColorStop(1, "rgba(5,7,11,0)");
      ctx.fillStyle = warm;
      ctx.fillRect(0, 0, W, H);
    }
  }

  function placeNodes(t) {
    for (let n = 0; n < nodeCount; n++) {
      const pos = nodePos[n];
      const x = pos[0] + Math.sin(t * 2.9 + phase[n * 3]) * CONF.thermalAmp;
      const y = pos[1] + Math.sin(t * 3.3 + phase[n * 3 + 1]) * CONF.thermalAmp;
      const z = pos[2] + Math.sin(t * 3.7 + phase[n * 3 + 2]) * CONF.thermalAmp;
      const slot = leafSlot[n];
      if (slot >= 0) {
        for (let j = 0; j < LEAF_COUNT; j++) {
          const li = slot * LEAF_COUNT + j;
          if (!project(x + leafOff[li * 3], y + leafOff[li * 3 + 1], z + leafOff[li * 3 + 2])) {
            leafP[li] = -1;
            continue;
          }
          displace(out.x, out.y);
          leafX[li] = out.x + disp.dx;
          leafY[li] = out.y + disp.dy;
          leafP[li] = out.persp;
        }
      }
      if (!project(x, y, z)) {
        nodeP[n] = -1;
        continue;
      }
      displace(out.x, out.y);
      nodeX[n] = out.x + disp.dx;
      nodeY[n] = out.y + disp.dy;
      nodeP[n] = out.persp;
    }
  }

  // A branch grows out of its parent's tip, led by a bright bud. It turns
  // warm, child to parent, as its completion pulse runs home.
  function drawEdges(ct) {
    const fade = 1 - fadeOf(ct);
    const bud = CONF.atomPx * sizeScale * view.zoom * CONF.glowRatio * 0.75;
    ctx.lineCap = "round";
    for (let n = 1; n < nodeCount; n++) {
      const p = nodeParent[n];
      if (nodeP[n] < 0 || nodeP[p] < 0) continue;
      const d = nodeDepth[n];
      const g = clamp((ct - growStart[n]) / CONF.growDur[d - 1], 0, 1);
      if (g <= 0) continue;
      const e = easeOutCubic(g);
      const tipX = nodeX[p] + (nodeX[n] - nodeX[p]) * e;
      const tipY = nodeY[p] + (nodeY[n] - nodeY[p]) * e;
      const done = smooth(closeAt[n], closeAt[n] + CONF.traceSeg, ct);
      const depth = depthOf((nodeP[n] + nodeP[p]) * 0.5);
      const alpha = clamp((0.26 + depth * 0.34 + done * 0.25) * fade * CONF.dim, 0, 1);
      if (alpha < 0.004) continue;
      const wScale = sizeScale * Math.sqrt(view.zoom);
      const wBase = Math.max(0.6, CONF.branchWidth[d - 1] * wScale);
      const wEnd = Math.max(0.6, CONF.branchWidth[Math.min(d, CONF.branchWidth.length - 1)] * wScale);
      const wTip = wBase + (wEnd - wBase) * e;
      const rgb = mix(COLORS.bond, COLORS.done, done);
      ctx.beginPath();
      ctx.moveTo(nodeX[p], nodeY[p]);
      ctx.lineTo(tipX, tipY);
      ctx.lineWidth = (wBase + wTip) * 0.5 * CONF.edgeGlow;
      ctx.strokeStyle = "rgba(" + rgb + "," + (alpha * CONF.edgeGlowAlpha).toFixed(3) + ")";
      ctx.stroke();
      // Tapered body: a quad from the base width to the tip width.
      const dx = tipX - nodeX[p];
      const dy = tipY - nodeY[p];
      const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len;
      ctx.fillStyle = "rgba(" + rgb + "," + alpha.toFixed(3) + ")";
      ctx.beginPath();
      ctx.moveTo(nodeX[p] + nx * wBase / 2, nodeY[p] + ny * wBase / 2);
      ctx.lineTo(tipX + nx * wTip / 2, tipY + ny * wTip / 2);
      ctx.lineTo(tipX - nx * wTip / 2, tipY - ny * wTip / 2);
      ctx.lineTo(nodeX[p] - nx * wBase / 2, nodeY[p] - ny * wBase / 2);
      ctx.closePath();
      ctx.fill();
      if (g < 1) {
        ctx.globalAlpha = clamp(0.9 * CONF.dim, 0, 1);
        ctx.drawImage(sprites.pulse, tipX - bud / 2, tipY - bud / 2, bud, bud);
        ctx.globalAlpha = 1;
      }
    }
  }

  // A completed node sends a warm pulse with a short tail back to its parent.
  const TRAIL = [0, 0.1, 0.2, 0.3];
  function drawPulses(ct) {
    const fade = 1 - fadeOf(ct);
    if (fade <= 0) return;
    const size = CONF.atomPx * sizeScale * view.zoom * CONF.glowRatio * 0.95;
    for (let n = 1; n < nodeCount; n++) {
      const head = (ct - closeAt[n]) / CONF.traceSeg;
      if (head < 0 || head >= 1 + TRAIL[TRAIL.length - 1]) continue;
      const p = nodeParent[n];
      if (nodeP[n] < 0 || nodeP[p] < 0) continue;
      for (let k = 0; k < TRAIL.length; k++) {
        const u = head - TRAIL[k];
        if (u < 0 || u >= 1) continue;
        const f = smooth(0, 1, u);
        const x = nodeX[n] + (nodeX[p] - nodeX[n]) * f;
        const y = nodeY[n] + (nodeY[p] - nodeY[n]) * f;
        const s = size * (1 - k * 0.18);
        ctx.globalAlpha = clamp((0.95 - k * 0.24) * fade * CONF.dim, 0, 1);
        ctx.drawImage(sprites.flash, x - s / 2, y - s / 2, s, s);
      }
    }
    ctx.globalAlpha = 1;
  }

  // A node pops in when its branch arrives, flashes cool, and later turns
  // warm and flashes warm when it completes. The goal flashes larger and
  // keeps a slow warm pulse once achieved.
  function drawNodes(ct, t) {
    const core = CONF.atomPx * sizeScale;
    const fade = 1 - fadeOf(ct);
    for (let n = 0; n < nodeCount; n++) {
      if (nodeP[n] < 0) continue;
      const pop = clamp((ct - arrive[n] + CONF.popDur) / CONF.popDur, 0, 1);
      if (pop <= 0) continue;
      const d = nodeDepth[n];
      const persp = Math.min(nodeP[n], CONF.maxSizePersp);
      const size = core * persp * view.zoom * CONF.glowRatio *
        CONF.nodeScale[Math.min(d, CONF.nodeScale.length - 1)] * easeOutBack(pop);
      if (size <= 0) continue;
      const base = clamp((0.45 + depthOf(nodeP[n]) * 0.5) * fade * CONF.dim, 0, 1);
      const closed = smooth(closeAt[n], closeAt[n] + 0.4, ct);
      const x = nodeX[n] - size / 2;
      const y = nodeY[n] - size / 2;
      ctx.globalAlpha = base * (1 - closed * 0.7);
      ctx.drawImage(stageSprites[Math.min(d, stageSprites.length - 1)], x, y, size, size);
      if (closed > 0.01) {
        ctx.globalAlpha = base * closed;
        ctx.drawImage(sprites.done, x, y, size, size);
      }
      const land = ct >= arrive[n] ? Math.exp(-(ct - arrive[n]) * 3.5) * 0.55 : 0;
      const close = ct >= closeAt[n] ? Math.exp(-(ct - closeAt[n]) * 3) * 0.85 : 0;
      const flash = Math.max(land, close) * fade;
      if (flash > 0.02) {
        const big = size * (n === 0 && close > land ? 3.2 : 1.8);
        ctx.globalAlpha = clamp(flash * CONF.dim, 0, 1);
        ctx.drawImage(land > close ? sprites.pulse : sprites.flash,
          nodeX[n] - big / 2, nodeY[n] - big / 2, big, big);
      }
      if (n === 0) {
        const g = glowOf(ct);
        if (g > 0.01) {
          const halo = size * 2.4;
          ctx.globalAlpha = clamp(g * (0.32 + 0.1 * Math.sin(t * 2.6)) * CONF.dim, 0, 1);
          ctx.drawImage(sprites.done, nodeX[0] - halo / 2, nodeY[0] - halo / 2, halo, halo);
        }
      }
      if (leafSlot[n] >= 0) drawLeaves(n, ct, closed, fade);
    }
    ctx.globalAlpha = 1;
  }

  // A twig unfolds its leaf cluster one leaf at a time after it arrives;
  // the leaves turn warm with the twig.
  function drawLeaves(n, ct, closed, fade) {
    const core = CONF.atomPx * sizeScale * view.zoom * CONF.glowRatio * CONF.leafScale;
    const sprite = stageSprites[stageSprites.length - 1];
    for (let j = 0; j < LEAF_COUNT; j++) {
      const li = leafSlot[n] * LEAF_COUNT + j;
      if (leafP[li] < 0) continue;
      const open = clamp((ct - arrive[n] - j * 0.09) / CONF.popDur, 0, 1);
      if (open <= 0) continue;
      const size = core * Math.min(leafP[li], CONF.maxSizePersp) * easeOutBack(open);
      if (size <= 0) continue;
      const base = clamp((0.35 + depthOf(leafP[li]) * 0.45) * fade * CONF.dim, 0, 1);
      const x = leafX[li] - size / 2;
      const y = leafY[li] - size / 2;
      ctx.globalAlpha = base * (1 - closed * 0.7);
      ctx.drawImage(sprite, x, y, size, size);
      if (closed > 0.01) {
        ctx.globalAlpha = base * closed;
        ctx.drawImage(sprites.done, x, y, size, size);
      }
    }
  }

  // Three warm ripples spread over the ground from the seed when the goal
  // is achieved: circles in the horizontal plane through the seed.
  const RIPPLE_STEPS = 64;
  function drawAchieved(ct) {
    const seed = nodePos[0];
    ctx.lineWidth = Math.max(0.8, 1.8 * sizeScale);
    for (let k = 0; k < 3; k++) {
      const r = (ct - tl.achieved - k * 0.35) / 2.4;
      if (r <= 0 || r >= 1) continue;
      const a = (1 - r) * (1 - r) * 0.5 * CONF.dim;
      const radius = 0.02 + r * 0.8;
      ctx.strokeStyle = "rgba(" + COLORS.done.join(",") + "," + a.toFixed(3) + ")";
      ctx.beginPath();
      let started = false;
      for (let i = 0; i <= RIPPLE_STEPS; i++) {
        const ang = (i / RIPPLE_STEPS) * Math.PI * 2;
        if (!project(seed[0] + Math.cos(ang) * radius, seed[1], seed[2] + Math.sin(ang) * radius)) {
          started = false;
          continue;
        }
        if (started) ctx.lineTo(out.x, out.y);
        else ctx.moveTo(out.x, out.y);
        started = true;
      }
      ctx.stroke();
    }
  }

  // ---- loop ---------------------------------------------------------------
  const reduce = win.matchMedia("(prefers-reduced-motion: reduce)");
  const view = { yaw: CONF.baseYaw, pitch: CONF.basePitch, zoom: 1, targetZoom: 1, dragYaw: 0, dragPitch: 0 };
  let simTime = 0;
  let cycleStart = 0;
  let last = win.performance.now();
  let rafId = 0;
  let alive = true;
  let frozenAt = null;
  schedule();

  function frame(now) {
    if (!alive) return;
    rafId = win.requestAnimationFrame(frame);
    const dt = Math.min((now - last) / 1000, 0.05);
    last = now;
    if (!reduce.matches && frozenAt === null) simTime += dt;

    const ease = 1 - Math.pow(0.001, dt);
    const follow = 1 - Math.exp(-dt / 0.16);
    if (pointer.strength < 0.01) {
      pointer.fx = pointer.x;
      pointer.fy = pointer.y;
    } else {
      pointer.fx += (pointer.x - pointer.fx) * follow;
      pointer.fy += (pointer.y - pointer.fy) * follow;
    }
    pointer.strength += ((pointer.inside ? 1 : 0) - pointer.strength) * follow;
    const paraYaw = pointer.inside && !pointer.dragging ? pointer.nx * CONF.parallaxYaw : 0;
    const paraPitch = pointer.inside && !pointer.dragging ? pointer.ny * CONF.parallaxPitch : 0;
    const targetYaw = CONF.baseYaw + view.dragYaw + paraYaw;
    const targetPitch = clamp(
      CONF.basePitch + view.dragPitch + paraPitch,
      CONF.pitchMin,
      CONF.pitchMax
    );
    view.yaw += (targetYaw - view.yaw) * ease;
    view.pitch += (targetPitch - view.pitch) * ease;
    view.zoom += (view.targetZoom - view.zoom) * (1 - Math.pow(0.004, dt));

    const yaw = view.yaw + simTime * CONF.spin;
    cam.cy = Math.cos(yaw);
    cam.sy = Math.sin(yaw);
    cam.cp = Math.cos(view.pitch);
    cam.sp = Math.sin(view.pitch);

    // Each cycle has its own length (random tree, random order), so cycles
    // are chained rather than taken modulo a fixed period.
    while (simTime - cycleStart >= tl.cycle) {
      cycleStart += tl.cycle;
      schedule();
    }
    // Reduced motion freezes simTime; show the grown tree before any task
    // completes instead of an empty stage.
    const ct = frozenAt !== null ? frozenAt : (reduce.matches ? tl.grown : simTime - cycleStart);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    drawBackdrop(ct);
    ctx.globalCompositeOperation = "lighter";
    placeNodes(simTime);
    drawEdges(ct);
    drawPulses(ct);
    drawNodes(ct, simTime);
    drawAchieved(ct);
    ctx.globalCompositeOperation = "source-over";
  }

  // The no-drag region is the panel's real box, so it follows the CSS width
  // without a duplicated constant.
  function overLoginPanel(x, y) {
    const panel = doc.querySelector(PANEL_SELECTOR);
    if (!panel) return false;
    const r = panel.getBoundingClientRect();
    return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  }
  function isLoginTarget(el) {
    return !!(el && el.closest &&
      el.closest(PANEL_SELECTOR + ",input,button,textarea,select,label,a"));
  }
  function setGrabCursor(mode) {
    doc.documentElement.classList.toggle("mira-grab", mode === "grab");
    doc.documentElement.classList.toggle("mira-grabbing", mode === "grabbing");
  }
  function onMove(e) {
    pointer.inside = true;
    pointer.x = e.clientX;
    pointer.y = e.clientY;
    pointer.nx = (e.clientX / W) * 2 - 1;
    pointer.ny = (e.clientY / H) * 2 - 1;
    if (pointer.dragging) {
      const dx = e.clientX - pointer.lastX;
      const dy = e.clientY - pointer.lastY;
      pointer.lastX = e.clientX;
      pointer.lastY = e.clientY;
      view.dragYaw += dx * CONF.dragYawScale;
      view.dragPitch = clamp(view.dragPitch + dy * CONF.dragPitchScale, -1.1, 1.1);
      e.preventDefault();
    } else if (!overLoginPanel(e.clientX, e.clientY) && !isLoginTarget(e.target)) {
      setGrabCursor("grab");
    } else {
      setGrabCursor("");
    }
  }
  function onDown(e) {
    if (e.button !== 0) return;
    if (overLoginPanel(e.clientX, e.clientY) || isLoginTarget(e.target)) return;
    pointer.dragging = true;
    pointer.lastX = e.clientX;
    pointer.lastY = e.clientY;
    setGrabCursor("grabbing");
    e.preventDefault();
  }
  function onUp() {
    if (!pointer.dragging) return;
    pointer.dragging = false;
    setGrabCursor(overLoginPanel(pointer.x, pointer.y) ? "" : "grab");
  }
  function onLeave() {
    pointer.inside = false;
    if (!pointer.dragging) setGrabCursor("");
  }
  function onResize() { layout(); }
  function onVisibility() {
    if (doc.hidden) return;
    last = win.performance.now();
  }
  function onWheel(e) {
    if (overLoginPanel(e.clientX, e.clientY) || isLoginTarget(e.target)) return;
    e.preventDefault();
    const factor = Math.exp(-e.deltaY * 0.0012);
    view.targetZoom = clamp(view.targetZoom * factor, CONF.zoomMin, CONF.zoomMax);
  }

  doc.addEventListener("pointermove", onMove, { passive: false });
  doc.addEventListener("pointerdown", onDown, { passive: false });
  doc.addEventListener("pointerup", onUp, { passive: true });
  doc.addEventListener("pointercancel", onUp, { passive: true });
  doc.addEventListener("pointerleave", onLeave, { passive: true });
  doc.addEventListener("wheel", onWheel, { passive: false });
  win.addEventListener("resize", onResize);
  doc.addEventListener("visibilitychange", onVisibility);

  win.__miraTraceBg = {
    stats: function () {
      return {
        nodes: nodeCount, leaves: leaves.length,
        grown: tl.grown, achieved: tl.achieved, cycle: tl.cycle
      };
    },
    // Cycle time in seconds, for previews and screenshots.
    time: function () { return simTime - cycleStart; },
    // Hold the current tree at cycle time `t` (seconds); null resumes.
    freeze: function (t) { frozenAt = typeof t === "number" ? t : null; },
    destroy: function () {
      alive = false;
      win.cancelAnimationFrame(rafId);
      setGrabCursor("");
      doc.removeEventListener("pointermove", onMove);
      doc.removeEventListener("pointerdown", onDown);
      doc.removeEventListener("pointerup", onUp);
      doc.removeEventListener("pointercancel", onUp);
      doc.removeEventListener("pointerleave", onLeave);
      doc.removeEventListener("wheel", onWheel);
      win.removeEventListener("resize", onResize);
      doc.removeEventListener("visibilitychange", onVisibility);
      const h = doc.getElementById("mira-login-bg");
      if (h) h.remove();
      const s = doc.getElementById("mira-login-bg-style");
      if (s) s.remove();
      win.__miraTraceBg = null;
    }
  };

  layout();
  rafId = win.requestAnimationFrame(frame);
})();

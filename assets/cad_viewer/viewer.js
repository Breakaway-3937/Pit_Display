'use strict';

(function () {

  // ── State ──────────────────────────────────────────────────────────────────
  var renderer, scene, camera, controls;
  var model = null;            // the loaded gltf scene
  var modelRoot = null;        // orientation/normalization wrapper around model
  var subsystems = [];
  var subsystemMeshMap = {};   // id → [Mesh]
  var mode = 'interactive';    // 'interactive' | 'judges'
  var focusedId = null;
  var animating = false;
  var upAxis = 'y';            // from subsystems.json — corrects CAD exports
  var allMeshesCache = null;   // rebuilt on (re)load; raycasting is hot
  var materialList = [];       // unique materials — focus fades tween these
  var needsRender = true;      // render-on-demand: skip frames when idle

  var defaultCamPos  = new THREE.Vector3(3, 2, 3);
  var defaultTarget  = new THREE.Vector3(0, 0, 0);

  // ── Init ───────────────────────────────────────────────────────────────────
  function init() {
    if (typeof THREE === 'undefined' || typeof gsap === 'undefined') return;

    var canvas = document.getElementById('c');

    renderer = new THREE.WebGLRenderer({
      canvas: canvas,
      antialias: true,
      powerPreference: 'high-performance',
    });
    // Cap the pixel ratio: on 2x displays a full-DPR canvas is 4× the pixels,
    // which large CAD models can't sustain.
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
    renderer.setSize(window.innerWidth, window.innerHeight);
    // Color-accurate pipeline: sRGB out, NO tone mapping — filmic curves and
    // extra exposure shift/clip the appearance colors authored in CAD.
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.toneMapping = THREE.NoToneMapping;
    renderer.shadowMap.enabled = false;

    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x121013);

    camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.01, 1000);
    camera.position.copy(defaultCamPos);
    scene.add(camera);

    // Neutral lighting that sums to ≈1 on a camera-facing surface, so
    // authored colors render at their real values instead of clipping white.
    // All lights are pure gray/white — no colored fills to shift hues.
    scene.add(new THREE.AmbientLight(0xffffff, 0.45));
    scene.add(new THREE.HemisphereLight(0xffffff, 0x555555, 0.25));
    // Headlight rides on the camera aimed at the model, so free-tumble never
    // rotates into an unlit face.
    var headlight = new THREE.DirectionalLight(0xffffff, 0.5);
    headlight.position.set(0.3, 0.6, 1);
    camera.add(headlight);

    // OrbitControls handles zoom (wheel / pinch) and pan (right-drag /
    // two-finger) only. Its turntable rotation is disabled — left-drag uses
    // the free-tumble handler below, which has no pole limits and rotates
    // 360° across all axes like a desktop CAD package.
    controls = new THREE.OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;
    controls.minDistance = 0.1;
    controls.maxDistance = 100;
    controls.enableRotate = false;
    controls.touches = { ONE: -1, TWO: THREE.TOUCH.DOLLY_PAN };
    controls.addEventListener('change', function () { needsRender = true; });

    canvas.addEventListener('pointerdown',   onPointerDown);
    canvas.addEventListener('pointermove',   onPointerMove);
    canvas.addEventListener('pointerup',     onPointerUp);
    canvas.addEventListener('pointercancel', onPointerUp);

    // Initial GSAP state for facts panel
    gsap.set('#facts-panel', { x: 340, opacity: 0, yPercent: -50 });
    gsap.set('#subsystem-label', { opacity: 0, x: -12 });

    window.addEventListener('resize', onResize);
    canvas.addEventListener('click', onCanvasClick);
    canvas.addEventListener('touchend', onCanvasTouch, { passive: true });

    loadConfig().then(function () { loadModel(); });
    animate();
  }

  function onResize() {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
    needsRender = true;
  }

  // ── Config ─────────────────────────────────────────────────────────────────
  function loadConfig() {
    return fetch('/cad/subsystems.json', { cache: 'no-cache' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        subsystems = data.subsystems || [];
        upAxis = data.up_axis || 'y';
        buildSubBar();
      })
      .catch(function (e) { console.warn('subsystems.json load failed:', e); });
  }

  // Re-read config and re-apply orientation/subsystems without re-parsing the
  // (potentially huge) model file.
  function refreshConfig() {
    return loadConfig().then(function () {
      if (modelRoot) {
        normalizeModel();
        buildSubsystemMeshMap();
        resetView();
      }
      needsRender = true;
    });
  }

  // ── Model ──────────────────────────────────────────────────────────────────
  function loadModel() {
    return fetch('/cad/robot.glb', { method: 'HEAD', cache: 'no-cache' })
      .then(function (r) {
        if (!r.ok) { showStatus('no-model'); return; }
        showStatus('loading');
        var loader = new THREE.GLTFLoader();
        loader.load(
          '/cad/robot.glb',
          onModelLoaded,
          function (prog) {
            var mb = (prog.loaded / 1048576).toFixed(0);
            var pct = prog.total ? ' (' + Math.round((prog.loaded / prog.total) * 100) + '%)' : '';
            setStatusText('Loading model… ' + mb + ' MB' + pct);
          },
          function (err) {
            console.error('GLTF error:', err);
            showStatus('error');
          }
        );
      })
      .catch(function () { showStatus('no-model'); });
  }

  function onModelLoaded(gltf) {
    if (modelRoot) scene.remove(modelRoot);
    model = gltf.scene;
    modelRoot = new THREE.Group();
    modelRoot.add(model);
    scene.add(modelRoot);

    normalizeModel();

    // The model never moves after normalization — freeze its (potentially
    // thousands of) node matrices so the render loop skips recomposing them.
    model.traverse(function (o) { o.matrixAutoUpdate = false; });

    allMeshesCache = null;
    storeOriginalMaterials();
    buildSubsystemMeshMap();
    hideStatus();
    needsRender = true;
  }

  // Group rotations that bring each possible CAD "up" axis to Three.js +Y.
  var AXIS_ROTATIONS = {
    'y':  [0, 0, 0],
    'z':  [-Math.PI / 2, 0, 0],
    '-z': [Math.PI / 2, 0, 0],
    'x':  [0, 0, Math.PI / 2],
    '-x': [0, 0, -Math.PI / 2],
  };

  // Orient (up_axis), center, and scale the model to a max dimension of 2,
  // then park the camera at the default three-quarter view.
  function normalizeModel() {
    if (!modelRoot) return;
    var rot = AXIS_ROTATIONS[upAxis] || AXIS_ROTATIONS.y;
    modelRoot.rotation.set(rot[0], rot[1], rot[2]);
    modelRoot.scale.setScalar(1);
    modelRoot.position.set(0, 0, 0);
    modelRoot.updateMatrixWorld(true);

    var box = new THREE.Box3().setFromObject(modelRoot);
    var center = box.getCenter(new THREE.Vector3());
    var size = box.getSize(new THREE.Vector3());
    var maxDim = Math.max(size.x, size.y, size.z) || 1;
    var scale = 2.0 / maxDim;

    modelRoot.scale.setScalar(scale);
    modelRoot.position.copy(center.multiplyScalar(-scale));
    modelRoot.updateMatrixWorld(true);

    // Model is now centered at the origin with max dimension 2.
    defaultTarget.set(0, 0, 0);
    controls.target.copy(defaultTarget);
    defaultCamPos.set(2.6, 1.8, 2.6);
    camera.up.set(0, 1, 0);
    camera.position.copy(defaultCamPos);
    camera.lookAt(defaultTarget);
    needsRender = true;
  }

  // ── Subsystem mesh map ─────────────────────────────────────────────────────
  function buildSubsystemMeshMap() {
    subsystemMeshMap = {};
    if (!model) return;

    subsystems.forEach(function (sub) {
      var meshes = [];
      model.traverse(function (obj) {
        if (obj.name === sub.node_name) {
          obj.traverse(function (child) {
            if (child.isMesh) meshes.push(child);
          });
        }
      });
      subsystemMeshMap[sub.id] = meshes;
    });
  }

  function getAllMeshes() {
    if (!allMeshesCache) {
      allMeshesCache = [];
      if (model) {
        model.traverse(function (obj) {
          if (obj.isMesh) allMeshesCache.push(obj);
        });
      }
    }
    return allMeshesCache;
  }

  // ── Materials (focus fades tween unique materials, not per-mesh) ───────────
  function storeOriginalMaterials() {
    materialList = [];
    var seen = new Set();
    getAllMeshes().forEach(function (mesh) {
      var mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      mats.forEach(function (m) {
        if (m && !seen.has(m)) {
          seen.add(m);
          m.userData._origOpacity = m.opacity;
          m.userData._origTransparent = m.transparent;
          // Keep the authored base color readable: without an environment
          // map, high metalness renders near-black and low roughness throws
          // blown-white speculars. Clamping both keeps parts looking like
          // the color picked in CAD.
          if (m.metalness !== undefined) m.metalness = Math.min(m.metalness, 0.4);
          if (m.roughness !== undefined) m.roughness = Math.max(m.roughness, 0.35);
          materialList.push(m);
        }
      });
    });
  }

  function materialsOf(meshes) {
    var s = new Set();
    meshes.forEach(function (mesh) {
      var mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      mats.forEach(function (m) { s.add(m); });
    });
    return s;
  }

  // ── Free tumble rotation (CAD-style, full 360° on all axes) ────────────────
  var ROT_SPEED = 1.0;
  var activePointers = 0;
  var tumbling = false;
  var lastPX = 0, lastPY = 0;
  var dragDist = 0;              // suppresses the click that follows a drag
  var tumblePivot = new THREE.Vector3();  // rotation center for current drag

  // CAD convention: the rotation pivot is the point on the model under the
  // cursor when the drag starts (empty space falls back to the view target).
  function pickPivot(clientX, clientY) {
    tumblePivot.copy(controls.target);
    if (!model) return;
    screenToNDC(clientX, clientY);
    raycaster.setFromCamera(mouse2D, camera);
    var hits = raycaster.intersectObjects(getAllMeshes());
    for (var i = 0; i < hits.length; i++) {
      // Skip parts faded out by a subsystem focus — they're invisible.
      var mat = hits[i].object.material;
      var op = Array.isArray(mat) ? mat[0].opacity : mat.opacity;
      if (op >= 0.15) { tumblePivot.copy(hits[i].point); return; }
    }
  }

  // Rotate the whole camera rig (position, target, up) around the pivot,
  // about the camera's current screen axes. The up vector rides along, so
  // there is no fixed "world up", no pole lock — full tumble in any
  // direction — and the grabbed point stays put on screen.
  function applyTumble(yaw, pitch) {
    var upAxisV  = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 1);
    var rightV   = new THREE.Vector3().setFromMatrixColumn(camera.matrix, 0);
    var q = new THREE.Quaternion().setFromAxisAngle(upAxisV, yaw);
    q.multiply(new THREE.Quaternion().setFromAxisAngle(rightV, pitch));
    camera.position.sub(tumblePivot).applyQuaternion(q).add(tumblePivot);
    controls.target.sub(tumblePivot).applyQuaternion(q).add(tumblePivot);
    camera.up.applyQuaternion(q).normalize();
    camera.lookAt(controls.target);
    needsRender = true;
  }

  function onPointerDown(e) {
    activePointers++;
    if (activePointers > 1) { tumbling = false; return; }  // pinch → OrbitControls
    if (!controls || !controls.enabled) return;            // judges mode
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    tumbling = true;
    dragDist = 0;
    pickPivot(e.clientX, e.clientY);
    lastPX = e.clientX;
    lastPY = e.clientY;
    try { e.target.setPointerCapture(e.pointerId); } catch (err) { /* synthetic/expired pointer */ }
  }

  function onPointerMove(e) {
    if (!tumbling || activePointers > 1) return;
    var dx = e.clientX - lastPX;
    var dy = e.clientY - lastPY;
    lastPX = e.clientX;
    lastPY = e.clientY;
    dragDist += Math.abs(dx) + Math.abs(dy);
    var h = renderer.domElement.clientHeight || 1;
    applyTumble(
      -2 * Math.PI * dx / h * ROT_SPEED,
      -2 * Math.PI * dy / h * ROT_SPEED
    );
  }

  function onPointerUp(e) {
    activePointers = Math.max(0, activePointers - 1);
    tumbling = false;
  }

  // ── Interaction ────────────────────────────────────────────────────────────
  var raycaster = new THREE.Raycaster();
  var mouse2D   = new THREE.Vector2();

  function screenToNDC(clientX, clientY) {
    var rect = renderer.domElement.getBoundingClientRect();
    mouse2D.x =  ((clientX - rect.left) / rect.width)  * 2 - 1;
    mouse2D.y = -((clientY - rect.top)  / rect.height) * 2 + 1;
  }

  function onCanvasClick(e) {
    if (mode !== 'interactive' || animating || !model) return;
    if (dragDist > 8) return;   // was a rotate-drag, not a tap
    screenToNDC(e.clientX, e.clientY);
    pickAtPoint();
  }

  function onCanvasTouch(e) {
    if (mode !== 'interactive' || animating || !model) return;
    if (dragDist > 8) return;
    if (!e.changedTouches.length) return;
    var t = e.changedTouches[0];
    screenToNDC(t.clientX, t.clientY);
    pickAtPoint();
  }

  function pickAtPoint() {
    raycaster.setFromCamera(mouse2D, camera);
    var allMeshes = getAllMeshes();
    var hits = raycaster.intersectObjects(allMeshes);
    if (!hits.length) { if (focusedId !== null) resetView(); return; }

    var hit = hits[0].object;
    var ids = Object.keys(subsystemMeshMap);
    for (var i = 0; i < ids.length; i++) {
      if (subsystemMeshMap[ids[i]].indexOf(hit) !== -1) {
        focusSubsystem(ids[i]);
        return;
      }
    }
    if (focusedId !== null) resetView();
  }

  // ── Focus subsystem ────────────────────────────────────────────────────────
  function focusSubsystem(id) {
    if (!model) return;
    var sub = null;
    for (var i = 0; i < subsystems.length; i++) {
      if (subsystems[i].id === id) { sub = subsystems[i]; break; }
    }
    if (!sub) return;

    focusedId = id;
    animating = true;

    var isJudges    = (mode === 'judges');
    var camDuration = isJudges ? 1.8 : 0.65;
    var fadeDur     = isJudges ? 1.0 : 0.4;
    var ease        = isJudges ? 'power4.inOut' : 'power2.inOut';
    var fadeOpacity = isJudges ? 0.04 : 0.08;

    var meshes = subsystemMeshMap[id] || [];

    // Fade materials not used by the selected subsystem. Tweening unique
    // materials (usually dozens) instead of every mesh (often thousands)
    // keeps focus animations responsive on large CAD files.
    var selected = materialsOf(meshes);
    materialList.forEach(function (m) {
      var target = selected.has(m) ? m.userData._origOpacity : fadeOpacity;
      m.transparent = true;
      gsap.to(m, { opacity: target, duration: fadeDur, ease: 'power2.inOut' });
    });

    // Compute camera destination
    var camTarget = defaultTarget.clone();
    var camPos    = defaultCamPos.clone();

    if (meshes.length > 0) {
      var box = new THREE.Box3();
      meshes.forEach(function (m) { box.expandByObject(m); });
      var ctr  = box.getCenter(new THREE.Vector3());
      var size = box.getSize(new THREE.Vector3());
      var maxD = Math.max(size.x, size.y, size.z);

      var preset   = sub.camera || {};
      var azimuth  = ((preset.azimuth_deg  || 45)  * Math.PI) / 180;
      var elev     = ((preset.elevation_deg || 25) * Math.PI) / 180;
      var dist     = maxD * (preset.distance_factor || 2.2);

      camTarget = ctr;
      camPos = new THREE.Vector3(
        ctr.x + dist * Math.cos(elev) * Math.sin(azimuth),
        ctr.y + dist * Math.sin(elev),
        ctr.z + dist * Math.cos(elev) * Math.cos(azimuth)
      );
    }

    gsap.to(camera.position, {
      x: camPos.x, y: camPos.y, z: camPos.z,
      duration: camDuration, ease: ease,
      onComplete: function () { animating = false; }
    });
    gsap.to(controls.target, {
      x: camTarget.x, y: camTarget.y, z: camTarget.z,
      duration: camDuration, ease: ease
    });
    // Presets are authored Y-up — roll the camera back upright.
    gsap.to(camera.up, { x: 0, y: 1, z: 0, duration: camDuration, ease: ease });

    showSubsystemLabel(sub.display_name);
    showFactsPanel(sub);
    updateSubBarActive(id);

    console.log(JSON.stringify({ type: 'subsystem_selected', id: id }));
  }

  // ── Reset view ─────────────────────────────────────────────────────────────
  function resetView() {
    if (!model) return;

    focusedId = null;
    animating = true;

    var isJudges = (mode === 'judges');
    var dur  = isJudges ? 1.4 : 0.5;
    var ease = isJudges ? 'power3.inOut' : 'power2.out';

    materialList.forEach(function (m) {
      gsap.to(m, {
        opacity: m.userData._origOpacity, duration: 0.4, ease: 'power2.inOut',
        onComplete: function () { m.transparent = m.userData._origTransparent; }
      });
    });

    gsap.to(camera.position, {
      x: defaultCamPos.x, y: defaultCamPos.y, z: defaultCamPos.z,
      duration: dur, ease: ease,
      onComplete: function () { animating = false; }
    });
    gsap.to(controls.target, {
      x: defaultTarget.x, y: defaultTarget.y, z: defaultTarget.z,
      duration: dur, ease: ease
    });
    gsap.to(camera.up, { x: 0, y: 1, z: 0, duration: dur, ease: ease });

    hideSubsystemLabel();
    hideFactsPanel();
    updateSubBarActive(null);

    console.log(JSON.stringify({ type: 'view_reset' }));
  }

  // ── Mode ───────────────────────────────────────────────────────────────────
  function setMode(newMode) {
    mode = newMode;
    var bar = document.getElementById('sub-bar');
    if (newMode === 'judges') {
      bar.classList.add('hidden');
      controls.enabled = false;
    } else {
      bar.classList.remove('hidden');
      controls.enabled = true;
    }
  }

  // ── Theme / accent ─────────────────────────────────────────────────────────
  var DEFAULT_ACCENT = '#C82027';
  var themeBg = { dark: 0x121013, light: 0xFAF9F8 };

  function setTheme(name) {
    var t = (name === 'light') ? 'light' : 'dark';
    if (t === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
    if (scene) scene.background = new THREE.Color(themeBg[t]);
    needsRender = true;
  }

  function setAccent(hex) {
    DEFAULT_ACCENT = hex || '#C82027';
    // Only override the live accent when nothing is focused; a focused
    // subsystem keeps its own accent_color.
    if (focusedId === null) {
      document.documentElement.style.setProperty('--accent', DEFAULT_ACCENT);
    }
  }

  // ── Reload ─────────────────────────────────────────────────────────────────
  function reload() {
    focusedId = null;
    animating = false;
    hideSubsystemLabel();
    hideFactsPanel();
    if (modelRoot) { scene.remove(modelRoot); modelRoot = null; model = null; }
    allMeshesCache = null;
    materialList = [];
    subsystemMeshMap = {};
    needsRender = true;
    loadConfig().then(function () { loadModel(); });
  }

  // ── Sub-bar ────────────────────────────────────────────────────────────────
  function buildSubBar() {
    var bar = document.getElementById('sub-bar');
    bar.innerHTML = '';
    if (!subsystems.length) return;

    var resetBtn = document.createElement('button');
    resetBtn.className = 'sub-btn reset-btn';
    resetBtn.textContent = '↩ Full View';
    resetBtn.addEventListener('click', resetView);
    bar.appendChild(resetBtn);

    bar.appendChild(spacer());

    subsystems.forEach(function (sub) {
      var btn = document.createElement('button');
      btn.className = 'sub-btn';
      btn.dataset.subId = sub.id;
      btn.textContent = sub.display_name;
      if (sub.accent_color) {
        btn.style.setProperty('--btn-accent', sub.accent_color);
      }
      btn.addEventListener('click', function () { focusSubsystem(sub.id); });
      bar.appendChild(btn);
    });

    bar.appendChild(spacer());

    if (mode === 'interactive') bar.classList.remove('hidden');
  }

  function spacer() {
    var d = document.createElement('div');
    d.className = 'sub-bar-spacer';
    return d;
  }

  function updateSubBarActive(id) {
    document.querySelectorAll('.sub-btn[data-sub-id]').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.subId === id);
    });
  }

  // ── Overlay UI ─────────────────────────────────────────────────────────────
  function showSubsystemLabel(name) {
    var el = document.getElementById('subsystem-label');
    el.querySelector('.sub-name').textContent = name;
    gsap.to(el, { opacity: 1, x: 0, duration: 0.45, ease: 'power2.out' });
  }

  function hideSubsystemLabel() {
    gsap.to('#subsystem-label', { opacity: 0, x: -8, duration: 0.25 });
  }

  function showFactsPanel(sub) {
    var isJudges = (mode === 'judges');
    var dur  = isJudges ? 0.85 : 0.5;
    var ease = isJudges ? 'power3.out' : 'power2.out';

    document.getElementById('facts-title').textContent = sub.display_name.toUpperCase();
    if (sub.accent_color) {
      document.documentElement.style.setProperty('--accent', sub.accent_color);
    }

    var list = document.getElementById('facts-list');
    list.innerHTML = '';
    (sub.facts || []).forEach(function (fact) {
      var li = document.createElement('li');
      li.textContent = fact;
      list.appendChild(li);
    });

    gsap.to('#facts-panel', { x: 0, opacity: 1, yPercent: -50, duration: dur, ease: ease });
  }

  function hideFactsPanel() {
    gsap.to('#facts-panel', {
      x: 340, opacity: 0, yPercent: -50, duration: 0.3, ease: 'power2.in',
      onComplete: function () {
        document.documentElement.style.setProperty('--accent', DEFAULT_ACCENT);
      }
    });
  }

  // ── Status overlay ─────────────────────────────────────────────────────────
  var STATUSES = {
    'no-model': ['⬡', 'No Model Loaded',   'Upload a .glb file using the Control Screen to get started.'],
    'loading':  ['⟳', 'Loading Model…', ''],
    'error':    ['⚠', 'Load Error',    'The model could not be loaded. Check the file and try again.'],
  };

  function showStatus(key) {
    var s = STATUSES[key];
    if (!s) return;
    var ov = document.getElementById('status-overlay');
    ov.classList.remove('hidden');
    document.getElementById('status-icon').textContent  = s[0];
    document.getElementById('status-title').textContent = s[1];
    document.getElementById('status-sub').textContent   = s[2];
  }

  function setStatusText(text) {
    document.getElementById('status-title').textContent = text;
  }

  function hideStatus() {
    document.getElementById('status-overlay').classList.add('hidden');
  }

  // ── Render loop (on demand) ────────────────────────────────────────────────
  // Only draw when the camera moved (controls.update() returns true while
  // orbiting/damping), a GSAP tween is running, or something flagged a
  // change. An idle 360 MB model no longer burns a core at 60 fps.
  function animate() {
    requestAnimationFrame(animate);
    if (!renderer || !scene || !camera) return;
    var moving = controls ? controls.update() : false;
    if (moving || needsRender || gsap.globalTimeline.isActive()) {
      renderer.render(scene, camera);
      needsRender = false;
    }
  }

  // Render one frame and return it as a PNG data-URL (the canvas has no
  // preserveDrawingBuffer, so capture must happen right after a render).
  function snapshot() {
    renderer.render(scene, camera);
    return renderer.domElement.toDataURL('image/png');
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  window.cadViewer = {
    focusSubsystem: focusSubsystem,
    resetView:      resetView,
    setMode:        setMode,
    reload:         reload,
    refreshConfig:  refreshConfig,
    setTheme:       setTheme,
    setAccent:      setAccent,
    snapshot:       snapshot,
  };

  // Boot
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    // Small delay so script tags for Three.js/GSAP can finish evaluating
    setTimeout(init, 0);
  }

})();

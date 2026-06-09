'use strict';

(function () {

  // ── State ──────────────────────────────────────────────────────────────────
  var renderer, scene, camera, controls;
  var model = null;
  var subsystems = [];
  var subsystemMeshMap = {};   // id → [Mesh]
  var mode = 'interactive';    // 'interactive' | 'judges'
  var focusedId = null;
  var animating = false;

  var defaultCamPos  = new THREE.Vector3(3, 2, 3);
  var defaultTarget  = new THREE.Vector3(0, 0, 0);

  // ── Init ───────────────────────────────────────────────────────────────────
  function init() {
    if (typeof THREE === 'undefined' || typeof gsap === 'undefined') return;

    var canvas = document.getElementById('c');

    renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.2;
    renderer.shadowMap.enabled = false;

    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x080808);

    camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.01, 1000);
    camera.position.copy(defaultCamPos);

    // Lighting
    scene.add(new THREE.AmbientLight(0xffffff, 0.5));
    var key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(5, 8, 5);
    scene.add(key);
    var fill = new THREE.DirectionalLight(0x8899ff, 0.35);
    fill.position.set(-5, 2, -3);
    scene.add(fill);
    var rim = new THREE.DirectionalLight(0xffffff, 0.4);
    rim.position.set(0, -3, -5);
    scene.add(rim);

    controls = new THREE.OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;
    controls.minDistance = 0.1;
    controls.maxDistance = 100;

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
  }

  // ── Config ─────────────────────────────────────────────────────────────────
  function loadConfig() {
    return fetch('/cad/subsystems.json', { cache: 'no-cache' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        subsystems = data.subsystems || [];
        buildSubBar();
      })
      .catch(function (e) { console.warn('subsystems.json load failed:', e); });
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
            var pct = prog.total ? Math.round((prog.loaded / prog.total) * 100) : 0;
            setStatusText('Loading model… ' + pct + '%');
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
    if (model) scene.remove(model);
    model = gltf.scene;
    scene.add(model);

    // Normalize scale and center
    var box = new THREE.Box3().setFromObject(model);
    var center = box.getCenter(new THREE.Vector3());
    var size = box.getSize(new THREE.Vector3());
    var maxDim = Math.max(size.x, size.y, size.z);
    var scale = 2.0 / maxDim;

    model.scale.setScalar(scale);
    model.position.sub(center.multiplyScalar(scale));

    // Recompute after scale + center
    var box2 = new THREE.Box3().setFromObject(model);
    var center2 = box2.getCenter(new THREE.Vector3());
    var size2 = box2.getSize(new THREE.Vector3());
    var maxDim2 = Math.max(size2.x, size2.y, size2.z);

    defaultTarget.copy(center2);
    controls.target.copy(center2);

    defaultCamPos.set(
      center2.x + maxDim2 * 1.3,
      center2.y + maxDim2 * 0.9,
      center2.z + maxDim2 * 1.3
    );
    camera.position.copy(defaultCamPos);
    camera.lookAt(center2);

    buildSubsystemMeshMap();
    hideStatus();
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
            if (child.isMesh) {
              // Store original opacity so we can restore it
              if (child.userData._origStored !== true) {
                var mat = child.material;
                if (Array.isArray(mat)) {
                  child.userData._origOpacity = mat.map(function (m) { return m.opacity; });
                  child.userData._origTransparent = mat.map(function (m) { return m.transparent; });
                } else {
                  child.userData._origOpacity = mat.opacity;
                  child.userData._origTransparent = mat.transparent;
                }
                child.userData._origStored = true;
              }
              meshes.push(child);
            }
          });
        }
      });
      subsystemMeshMap[sub.id] = meshes;
    });
  }

  function getAllMeshes() {
    var meshes = [];
    if (model) model.traverse(function (obj) { if (obj.isMesh) meshes.push(obj); });
    return meshes;
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
    screenToNDC(e.clientX, e.clientY);
    pickAtPoint();
  }

  function onCanvasTouch(e) {
    if (mode !== 'interactive' || animating || !model) return;
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

    var meshes    = subsystemMeshMap[id] || [];
    var allMeshes = getAllMeshes();

    // Fade non-selected meshes
    allMeshes.forEach(function (mesh) {
      var inSub = meshes.indexOf(mesh) !== -1;
      animateMeshOpacity(mesh, inSub ? 1.0 : fadeOpacity, fadeDur);
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

    getAllMeshes().forEach(function (mesh) {
      restoreMeshOpacity(mesh, 0.4);
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

    hideSubsystemLabel();
    hideFactsPanel();
    updateSubBarActive(null);

    console.log(JSON.stringify({ type: 'view_reset' }));
  }

  // ── Mesh opacity helpers ───────────────────────────────────────────────────
  function animateMeshOpacity(mesh, targetOpacity, duration) {
    var mat = mesh.material;
    if (Array.isArray(mat)) {
      mat.forEach(function (m) {
        m.transparent = true;
        gsap.to(m, { opacity: targetOpacity, duration: duration, ease: 'power2.inOut' });
      });
    } else {
      mat.transparent = true;
      gsap.to(mat, { opacity: targetOpacity, duration: duration, ease: 'power2.inOut' });
    }
  }

  function restoreMeshOpacity(mesh, duration) {
    var mat      = mesh.material;
    var origOp   = mesh.userData._origOpacity;
    var origTr   = mesh.userData._origTransparent;
    if (Array.isArray(mat)) {
      mat.forEach(function (m, i) {
        var o = Array.isArray(origOp) ? (origOp[i] !== undefined ? origOp[i] : 1.0) : 1.0;
        var t = Array.isArray(origTr) ? (origTr[i] !== undefined ? origTr[i] : false) : false;
        gsap.to(m, { opacity: o, duration: duration, ease: 'power2.inOut',
          onComplete: function () { m.transparent = t; }
        });
      });
    } else {
      var o = (origOp !== undefined) ? origOp : 1.0;
      var t = (origTr !== undefined) ? origTr : false;
      gsap.to(mat, { opacity: o, duration: duration, ease: 'power2.inOut',
        onComplete: function () { mat.transparent = t; }
      });
    }
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

  // ── Reload ─────────────────────────────────────────────────────────────────
  function reload() {
    focusedId = null;
    animating = false;
    hideSubsystemLabel();
    hideFactsPanel();
    if (model) { scene.remove(model); model = null; }
    getAllMeshes();
    subsystemMeshMap = {};
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
        document.documentElement.style.setProperty('--accent', '#ffffff');
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

  // ── Render loop ────────────────────────────────────────────────────────────
  function animate() {
    requestAnimationFrame(animate);
    if (controls) controls.update();
    if (renderer && scene && camera) renderer.render(scene, camera);
  }

  // ── Public API ─────────────────────────────────────────────────────────────
  window.cadViewer = {
    focusSubsystem: focusSubsystem,
    resetView:      resetView,
    setMode:        setMode,
    reload:         reload,
  };

  // Boot
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    // Small delay so script tags for Three.js/GSAP can finish evaluating
    setTimeout(init, 0);
  }

})();

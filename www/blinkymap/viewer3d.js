/**
 * viewer3d.js — Three.js 3D viewer with confidence halos.
 *
 * Usage:
 *   import { Viewer3D } from './viewer3d.js';
 *   const v = new Viewer3D(containerEl);
 *   v.update(pixelsArray);   // [{x,y,z,confidence,grade}, ...]
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const GRADE_COLORS = {
  high:   new THREE.Color(0x69f0ae),  // green
  medium: new THREE.Color(0xffee58),  // yellow
  low:    new THREE.Color(0xef5350),  // red
  unseen: new THREE.Color(0x555577),  // grey
};

export class Viewer3D {
  constructor(container) {
    this._container = container;

    // Renderer
    this._renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this._renderer.setPixelRatio(window.devicePixelRatio);
    this._renderer.setClearColor(0x0a0a18, 1);
    container.appendChild(this._renderer.domElement);

    // Scene
    this._scene = new THREE.Scene();

    // Camera
    this._cam = new THREE.PerspectiveCamera(45, 1, 0.01, 100);
    this._cam.position.set(2, 1.5, 2);
    this._cam.lookAt(0, 0.5, 0);

    // Controls
    this._controls = new OrbitControls(this._cam, this._renderer.domElement);
    this._controls.enableDamping = true;
    this._controls.dampingFactor = 0.08;
    this._controls.target.set(0, 0.5, 0);

    // Grid helper
    const grid = new THREE.GridHelper(4, 20, 0x222244, 0x1a1a30);
    this._scene.add(grid);

    // Placeholder points
    this._dotCloud   = null;
    this._haloCloud  = null;
    this._suggGroup  = null;
    this._suggRing   = null;

    this._resize();
    this._raf = requestAnimationFrame(this._loop.bind(this));

    window.addEventListener("resize", () => this._resize());
  }

  _resize() {
    const w = this._container.clientWidth;
    const h = this._container.clientHeight;
    this._cam.aspect = w / h;
    this._cam.updateProjectionMatrix();
    this._renderer.setSize(w, h, false);
  }

  /**
   * Update the point cloud from a pixel array.
   * pixels: [{x, y, z, confidence, grade}]  (positioned pixels only)
   */
  update(pixels) {
    // Remove old clouds
    if (this._dotCloud)  { this._scene.remove(this._dotCloud);  this._dotCloud.geometry.dispose(); }
    if (this._haloCloud) { this._scene.remove(this._haloCloud); this._haloCloud.geometry.dispose(); }

    const positioned = pixels.filter(p => p.x != null);
    if (!positioned.length) return;

    const n = positioned.length;

    const dotPositions  = new Float32Array(n * 3);
    const dotColors     = new Float32Array(n * 3);
    const haloPositions = new Float32Array(n * 3);
    const haloColors    = new Float32Array(n * 3);
    const haloAlphas    = new Float32Array(n);
    const haloSizes     = new Float32Array(n);

    for (let i = 0; i < n; i++) {
      const p    = positioned[i];
      const conf = p.confidence ?? 0;
      const col  = GRADE_COLORS[p.grade] ?? GRADE_COLORS.unseen;

      dotPositions[i*3]   = p.x;
      dotPositions[i*3+1] = p.y;
      dotPositions[i*3+2] = p.z;
      dotColors[i*3]   = col.r;
      dotColors[i*3+1] = col.g;
      dotColors[i*3+2] = col.b;

      haloPositions[i*3]   = p.x;
      haloPositions[i*3+1] = p.y;
      haloPositions[i*3+2] = p.z;
      haloColors[i*3]   = col.r;
      haloColors[i*3+1] = col.g;
      haloColors[i*3+2] = col.b;

      const t = Math.pow(1 - conf, 1.2);
      haloAlphas[i] = t * 0.35;
      haloSizes[i]  = t * 60 + 6;
    }

    // Dot cloud (small solid points)
    {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(dotPositions, 3));
      geo.setAttribute("color",    new THREE.BufferAttribute(dotColors, 3));
      const mat = new THREE.PointsMaterial({
        size: 0.04, vertexColors: true, sizeAttenuation: true,
      });
      this._dotCloud = new THREE.Points(geo, mat);
      this._scene.add(this._dotCloud);
    }

    // Halo cloud (large semi-transparent circles for low-confidence pixels)
    {
      const geo = new THREE.BufferGeometry();
      geo.setAttribute("position", new THREE.BufferAttribute(haloPositions, 3));
      geo.setAttribute("color",    new THREE.BufferAttribute(haloColors, 3));
      geo.setAttribute("aAlpha",   new THREE.BufferAttribute(haloAlphas, 1));
      geo.setAttribute("aSize",    new THREE.BufferAttribute(haloSizes, 1));

      const mat = new THREE.ShaderMaterial({
        uniforms: {},
        vertexShader: `
          attribute float aAlpha;
          attribute float aSize;
          varying vec3  vColor;
          varying float vAlpha;
          void main() {
            vColor = color;
            vAlpha = aAlpha;
            vec4 mv = modelViewMatrix * vec4(position, 1.0);
            gl_Position  = projectionMatrix * mv;
            gl_PointSize = aSize * (300.0 / -mv.z);
          }
        `,
        fragmentShader: `
          varying vec3  vColor;
          varying float vAlpha;
          void main() {
            float d = length(gl_PointCoord - 0.5) * 2.0;
            if (d > 1.0) discard;
            float a = (1.0 - d) * vAlpha;
            gl_FragColor = vec4(vColor, a);
          }
        `,
        transparent: true,
        vertexColors: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      });

      this._haloCloud = new THREE.Points(geo, mat);
      this._scene.add(this._haloCloud);
    }
  }

  /**
   * Show a suggested camera position as a glowing marker + aim line.
   * angle_deg: horizontal angle around tree (same convention as sessions)
   * distance:  metres from trunk centre
   */
  setSuggestion(angle_deg, distance = 2.0) {
    // Remove previous suggestion objects
    if (this._suggGroup) {
      this._scene.remove(this._suggGroup);
      this._suggGroup.traverse(o => { if (o.geometry) o.geometry.dispose(); });
    }

    const rad  = (angle_deg * Math.PI) / 180;
    const x    = distance * Math.sin(rad);
    const z    = distance * Math.cos(rad);
    const y    = this._controls.target.y;   // match orbit target height

    const group = new THREE.Group();

    // Pulsing sphere at the suggested position
    const sphere = new THREE.Mesh(
      new THREE.SphereGeometry(0.08, 16, 16),
      new THREE.MeshBasicMaterial({ color: 0x4fc3f7, transparent: true, opacity: 0.9 })
    );
    sphere.position.set(x, y, z);
    group.add(sphere);

    // Outer ring (halo)
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.12, 0.16, 32),
      new THREE.MeshBasicMaterial({
        color: 0x4fc3f7, transparent: true, opacity: 0.45, side: THREE.DoubleSide
      })
    );
    ring.position.set(x, y, z);
    ring.lookAt(this._cam.position);   // always face the camera (updated in loop)
    group.add(ring);
    this._suggRing = ring;

    // Dashed aim line from suggested position to tree centre
    const linePts = [new THREE.Vector3(x, y, z), new THREE.Vector3(0, y, 0)];
    const lineGeo = new THREE.BufferGeometry().setFromPoints(linePts);
    const line    = new THREE.Line(
      lineGeo,
      new THREE.LineDashedMaterial({ color: 0x4fc3f7, opacity: 0.4,
                                     transparent: true, dashSize: 0.08, gapSize: 0.05 })
    );
    line.computeLineDistances();
    group.add(line);

    // Label arrow pointing down to sphere
    const arrowGeo = new THREE.ConeGeometry(0.04, 0.14, 8);
    const arrow    = new THREE.Mesh(arrowGeo,
      new THREE.MeshBasicMaterial({ color: 0x4fc3f7 }));
    arrow.position.set(x, y + 0.25, z);
    arrow.rotation.z = Math.PI;   // point downward
    group.add(arrow);

    this._scene.add(group);
    this._suggGroup = group;
  }

  _loop() {
    this._controls.update();
    // Keep suggestion ring facing the camera
    if (this._suggRing) this._suggRing.lookAt(this._cam.position);
    this._renderer.render(this._scene, this._cam);
    this._raf = requestAnimationFrame(this._loop.bind(this));
  }

  destroy() {
    cancelAnimationFrame(this._raf);
    this._controls.dispose();
    this._renderer.dispose();
  }
}

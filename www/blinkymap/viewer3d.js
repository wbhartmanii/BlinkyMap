/**
 * viewer3d.js — Three.js 3D viewer with confidence halos.
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const GRADE_COLORS = {
  high:   new THREE.Color(0x69f0ae),
  medium: new THREE.Color(0xffee58),
  low:    new THREE.Color(0xef5350),
  unseen: new THREE.Color(0x555577),
};

export class Viewer3D {
  constructor(container) {
    this._container = container;
    this._renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this._renderer.setPixelRatio(window.devicePixelRatio);
    this._renderer.setClearColor(0x0a0a18, 1);
    container.appendChild(this._renderer.domElement);
    this._scene = new THREE.Scene();
    this._cam = new THREE.PerspectiveCamera(45, 1, 0.01, 100);
    this._cam.position.set(2, 1.5, 2);
    this._cam.lookAt(0, 0.5, 0);
    this._controls = new OrbitControls(this._cam, this._renderer.domElement);
    this._controls.enableDamping = true;
    this._controls.dampingFactor = 0.08;
    this._controls.target.set(0, 0.5, 0);
    this._scene.add(new THREE.GridHelper(4, 20, 0x222244, 0x1a1a30));
    this._dotCloud = null;
    this._haloCloud = null;
    this._suggGroup = null;
    this._suggRing  = null;
    this._resize();
    this._raf = requestAnimationFrame(this._loop.bind(this));
    window.addEventListener("resize", () => this._resize());
  }

  _resize() {
    const w = this._container.clientWidth, h = this._container.clientHeight;
    this._cam.aspect = w / h;
    this._cam.updateProjectionMatrix();
    this._renderer.setSize(w, h, false);
  }

  update(pixels) {
    if (this._dotCloud)  { this._scene.remove(this._dotCloud);  this._dotCloud.geometry.dispose(); }
    if (this._haloCloud) { this._scene.remove(this._haloCloud); this._haloCloud.geometry.dispose(); }
    const positioned = pixels.filter(p => p.x != null);
    if (!positioned.length) return;
    const n = positioned.length;
    const dotPos = new Float32Array(n*3), dotCol = new Float32Array(n*3);
    const haloPos = new Float32Array(n*3), haloCol = new Float32Array(n*3);
    const haloAlpha = new Float32Array(n), haloSize = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const p = positioned[i], conf = p.confidence ?? 0;
      const col = GRADE_COLORS[p.grade] ?? GRADE_COLORS.unseen;
      dotPos[i*3]=p.x; dotPos[i*3+1]=p.y; dotPos[i*3+2]=p.z;
      dotCol[i*3]=col.r; dotCol[i*3+1]=col.g; dotCol[i*3+2]=col.b;
      haloPos[i*3]=p.x; haloPos[i*3+1]=p.y; haloPos[i*3+2]=p.z;
      haloCol[i*3]=col.r; haloCol[i*3+1]=col.g; haloCol[i*3+2]=col.b;
      const t = Math.pow(1-conf, 1.2);
      haloAlpha[i] = t*0.35; haloSize[i] = t*60+6;
    }
    const dotGeo = new THREE.BufferGeometry();
    dotGeo.setAttribute("position", new THREE.BufferAttribute(dotPos, 3));
    dotGeo.setAttribute("color",    new THREE.BufferAttribute(dotCol, 3));
    this._dotCloud = new THREE.Points(dotGeo, new THREE.PointsMaterial({ size: 0.04, vertexColors: true, sizeAttenuation: true }));
    this._scene.add(this._dotCloud);
    const haloGeo = new THREE.BufferGeometry();
    haloGeo.setAttribute("position", new THREE.BufferAttribute(haloPos, 3));
    haloGeo.setAttribute("color",    new THREE.BufferAttribute(haloCol, 3));
    haloGeo.setAttribute("aAlpha",   new THREE.BufferAttribute(haloAlpha, 1));
    haloGeo.setAttribute("aSize",    new THREE.BufferAttribute(haloSize, 1));
    const haloMat = new THREE.ShaderMaterial({
      vertexShader: `attribute float aAlpha; attribute float aSize; varying vec3 vColor; varying float vAlpha;
        void main() { vColor=color; vAlpha=aAlpha; vec4 mv=modelViewMatrix*vec4(position,1.0);
          gl_Position=projectionMatrix*mv; gl_PointSize=aSize*(300.0/-mv.z); }`,
      fragmentShader: `varying vec3 vColor; varying float vAlpha;
        void main() { float d=length(gl_PointCoord-0.5)*2.0; if(d>1.0)discard;
          gl_FragColor=vec4(vColor,(1.0-d)*vAlpha); }`,
      transparent: true, vertexColors: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    this._haloCloud = new THREE.Points(haloGeo, haloMat);
    this._scene.add(this._haloCloud);
  }

  setSuggestion(angle_deg, distance = 2.0) {
    if (this._suggGroup) {
      this._scene.remove(this._suggGroup);
      this._suggGroup.traverse(o => { if (o.geometry) o.geometry.dispose(); });
    }
    const rad = (angle_deg * Math.PI) / 180;
    const x = distance * Math.sin(rad), z = distance * Math.cos(rad), y = this._controls.target.y;
    const group = new THREE.Group();
    const sphere = new THREE.Mesh(new THREE.SphereGeometry(0.08, 16, 16),
      new THREE.MeshBasicMaterial({ color: 0x4fc3f7, transparent: true, opacity: 0.9 }));
    sphere.position.set(x, y, z);
    group.add(sphere);
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.12, 0.16, 32),
      new THREE.MeshBasicMaterial({ color: 0x4fc3f7, transparent: true, opacity: 0.45, side: THREE.DoubleSide }));
    ring.position.set(x, y, z); ring.lookAt(this._cam.position);
    group.add(ring); this._suggRing = ring;
    const linePts = [new THREE.Vector3(x,y,z), new THREE.Vector3(0,y,0)];
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(linePts),
      new THREE.LineDashedMaterial({ color: 0x4fc3f7, opacity: 0.4, transparent: true, dashSize: 0.08, gapSize: 0.05 }));
    line.computeLineDistances(); group.add(line);
    const arrow = new THREE.Mesh(new THREE.ConeGeometry(0.04, 0.14, 8),
      new THREE.MeshBasicMaterial({ color: 0x4fc3f7 }));
    arrow.position.set(x, y+0.25, z); arrow.rotation.z = Math.PI; group.add(arrow);
    this._scene.add(group); this._suggGroup = group;
  }

  _loop() {
    this._controls.update();
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

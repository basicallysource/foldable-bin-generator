"use client";

/* 3D viewer for a STEP model's planar CAD faces (the /api/model3d payload) —
   used by the model-preview modal. Cardboard-toned shells + orbit controls. */

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { CadScene } from "@/lib/api";

export default function CadViewer({ scene3 }: { scene3: CadScene }) {
  const elRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = elRef.current;
    if (!el) return;

    const W = el.clientWidth || 720;
    const H = el.clientHeight || 480;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(W, H);
    renderer.setPixelRatio(window.devicePixelRatio);
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x04060a);
    const cam = new THREE.PerspectiveCamera(45, W / H, 1, scene3.size * 20);
    const c = scene3.center;
    cam.position.set(c[0] + scene3.size * 0.9, c[1] - scene3.size * 0.9, c[2] + scene3.size * 0.7);
    cam.up.set(0, 0, 1);
    const controls = new OrbitControls(cam, renderer.domElement);
    controls.target.set(c[0], c[1], c[2]);

    scene.add(new THREE.HemisphereLight(0xffffff, 0x404060, 1.15));
    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const dl = new THREE.DirectionalLight(0xffffff, 1.1);
    dl.position.set(c[0] + scene3.size, c[1] + scene3.size, c[2] + scene3.size);
    scene.add(dl);
    const dl2 = new THREE.DirectionalLight(0xffffff, 0.5);
    dl2.position.set(c[0] - scene3.size, c[1] - scene3.size, c[2] + scene3.size * 0.5);
    scene.add(dl2);

    const mat = new THREE.MeshStandardMaterial({
      color: 0xb9a489,
      roughness: 0.85,
      metalness: 0,
      side: THREE.DoubleSide,
    });
    const edgeMat = new THREE.LineBasicMaterial({ color: 0x6e5e46 });
    for (const f of scene3.cad) {
      const sh = new THREE.Shape(f.poly.map((q) => new THREE.Vector2(q[0], q[1])));
      const geo = new THREE.ShapeGeometry(sh);
      const mesh = new THREE.Mesh(geo, mat);
      mesh.applyMatrix4(new THREE.Matrix4().fromArray(f.matrix));
      scene.add(mesh);
      const edge = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 20), edgeMat);
      edge.applyMatrix4(new THREE.Matrix4().fromArray(f.matrix));
      scene.add(edge);
    }

    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      controls.update();
      renderer.render(scene, cam);
    };
    loop();

    return () => {
      cancelAnimationFrame(raf);
      controls.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [scene3]);

  return <div className="cadviewer" ref={elRef} />;
}

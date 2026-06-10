"use client";

/* three.js port of rev01's renderRefold3D (templates/index.html), using the
   npm three package instead of the CDN import map — grey folded sheet vs
   translucent green CAD reference, orbit controls. */

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { RefoldScene, ScenePanel } from "@/lib/api";

export default function RefoldViewer({ scene3 }: { scene3: RefoldScene }) {
  const elRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = elRef.current;
    if (!el) return;

    const W = el.clientWidth || 800;
    const H = el.clientHeight || 440;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(W, H);
    renderer.setPixelRatio(window.devicePixelRatio);
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x04060a);
    const cam = new THREE.PerspectiveCamera(45, W / H, 1, scene3.size * 20);
    const c = scene3.center;
    cam.position.set(c[0] + scene3.size, c[1] - scene3.size * 0.8, c[2] + scene3.size * 0.8);
    cam.up.set(0, 0, 1);
    const controls = new OrbitControls(cam, renderer.domElement);
    controls.target.set(c[0], c[1], c[2]);

    scene.add(new THREE.HemisphereLight(0xffffff, 0x404060, 1.1));
    const dl = new THREE.DirectionalLight(0xffffff, 1.2);
    dl.position.set(c[0] + scene3.size, c[1] + scene3.size, c[2] + scene3.size);
    scene.add(dl);

    const toShape = (p: { poly: number[][]; holes?: number[][][] }) => {
      const sh = new THREE.Shape(p.poly.map((q) => new THREE.Vector2(q[0], q[1])));
      for (const h of p.holes || [])
        sh.holes.push(new THREE.Path(h.map((q) => new THREE.Vector2(q[0], q[1]))));
      return sh;
    };

    const sheetMat = new THREE.MeshStandardMaterial({
      color: 0xb9a489,
      roughness: 0.85,
      metalness: 0,
      side: THREE.DoubleSide,
    });
    for (const p of scene3.panels as ScenePanel[]) {
      const geo = new THREE.ExtrudeGeometry(toShape(p), {
        depth: p.thickness,
        bevelEnabled: false,
      });
      const mesh = new THREE.Mesh(geo, sheetMat);
      mesh.applyMatrix4(new THREE.Matrix4().fromArray(p.matrix));
      scene.add(mesh);
      const edge = new THREE.LineSegments(
        new THREE.EdgesGeometry(geo, 20),
        new THREE.LineBasicMaterial({ color: 0x6e5e46 })
      );
      edge.applyMatrix4(new THREE.Matrix4().fromArray(p.matrix));
      scene.add(edge);
    }

    const cadMat = new THREE.MeshBasicMaterial({
      color: 0x26a641,
      transparent: true,
      opacity: 0.18,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    for (const f of scene3.cad) {
      const geo = new THREE.ShapeGeometry(toShape(f));
      const mesh = new THREE.Mesh(geo, cadMat);
      mesh.applyMatrix4(new THREE.Matrix4().fromArray(f.matrix));
      scene.add(mesh);
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

  return (
    <div className="viewer3d" ref={elRef}>
      <div className="legend">
        3D refold — grey = folded sheet, green = CAD reference (drag to orbit)
      </div>
    </div>
  );
}

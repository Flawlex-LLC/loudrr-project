"use client";

import { useEffect, useRef, useCallback } from "react";
import * as THREE from "three";

export default function AudioWaveGL() {
  const containerRef = useRef<HTMLDivElement>(null);
  const mouseRef = useRef({ x: 0.5, y: 0.5, targetX: 0.5, targetY: 0.5 });
  const glowPositionRef = useRef({ x: 0.5, y: 0.5 });
  const dimensionsRef = useRef({ width: 0, height: 0 });

  const handleMouseMove = useCallback((e: MouseEvent) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    mouseRef.current.targetX = (e.clientX - rect.left) / rect.width;
    mouseRef.current.targetY = (e.clientY - rect.top) / rect.height;
  }, []);

  const handleTouchMove = useCallback((e: TouchEvent) => {
    if (!containerRef.current) return;
    const touch = e.touches[0];
    if (!touch) return;
    const rect = containerRef.current.getBoundingClientRect();
    mouseRef.current.targetX = (touch.clientX - rect.left) / rect.width;
    mouseRef.current.targetY = (touch.clientY - rect.top) / rect.height;
  }, []);

  const handleTouchEnd = useCallback(() => {
    // Reset to default position when touch ends
    mouseRef.current.targetX = 0.5;
    mouseRef.current.targetY = 0.5;
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;

    const container = containerRef.current;
    let width = container.clientWidth;
    let height = container.clientHeight;
    dimensionsRef.current = { width, height };

    // Scene
    const scene = new THREE.Scene();

    // Camera
    const camera = new THREE.OrthographicCamera(
      -width / 2, width / 2,
      height / 2, -height / 2,
      0.1, 1000
    );
    camera.position.z = 100;

    // Renderer
    const renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: true,
      powerPreference: "high-performance",
    });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);

    // Brand color
    const brandColor = new THREE.Color(0xf95400);

    // Create atmospheric glow plane
    let glowGeometry = new THREE.PlaneGeometry(width * 1.5, height * 1.5);
    const glowMaterial = new THREE.ShaderMaterial({
      uniforms: {
        uColor: { value: brandColor.clone() },
        uGlowPosition: { value: new THREE.Vector2(0.5, 0.5) },
        uIntensity: { value: 0.45 },
        uTime: { value: 0 },
        uResolution: { value: new THREE.Vector2(width, height) },
      },
      vertexShader: `
        varying vec2 vUv;
        void main() {
          vUv = uv;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: `
        uniform vec3 uColor;
        uniform vec2 uGlowPosition;
        uniform float uIntensity;
        uniform float uTime;
        uniform vec2 uResolution;
        varying vec2 vUv;

        // Halftone dot pattern function
        float halftone(vec2 coord, float dotSize) {
          vec2 nearest = floor(coord / dotSize) * dotSize + dotSize * 0.5;
          float dist = distance(coord, nearest);
          float dotRadius = dotSize * 0.35;
          return smoothstep(dotRadius, dotRadius * 0.7, dist);
        }

        void main() {
          vec2 glowCenter = vec2(uGlowPosition.x, 0.35 + uGlowPosition.y * 0.15);
          float dist = distance(vUv, glowCenter);
          float glow = smoothstep(0.35, 0.0, dist) * uIntensity;

          // Create aspect-corrected coordinates for perfect circular dots
          float aspect = uResolution.x / uResolution.y;
          vec2 halftoneCoord = vUv * 400.0; // Even higher frequency for super tiny dots
          halftoneCoord.x *= aspect; // Correct aspect ratio for circular dots

          float halftonePattern = halftone(halftoneCoord, 1.0);

          // Invert so dots are bright, gaps are dark
          halftonePattern = 1.0 - halftonePattern;

          // Subtle dot brightness that follows glow
          float dotBrightness = halftonePattern * glow * 1.8;

          // Combine soft glow with very fine halftone texture
          float combinedGlow = glow * 0.25 + dotBrightness * 0.4;

          vec3 finalColor = uColor * (1.1 + combinedGlow * 0.3);
          float alpha = (glow * 0.35 + dotBrightness * 0.3) * 0.85;
          gl_FragColor = vec4(finalColor * combinedGlow, alpha);
        }
      `,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });

    const glowPlane = new THREE.Mesh(glowGeometry, glowMaterial);
    glowPlane.position.z = -20;
    scene.add(glowPlane);

    // Create bars
    const barCount = 80;
    const bars: THREE.Mesh[] = [];
    const barData: { baseHeight: number; targetHeight: number; currentHeight: number }[] = [];

    // Function to calculate bar properties based on current dimensions
    const calculateBarLayout = (w: number, h: number) => {
      // Increase bar width for better visibility, especially on mobile
      const barWidth = w / barCount * 0.85;
      const barSpacing = w / barCount;
      return { barWidth, barSpacing };
    };

    let { barWidth, barSpacing } = calculateBarLayout(width, height);

    for (let i = 0; i < barCount; i++) {
      const geometry = new THREE.PlaneGeometry(barWidth, 1);

      const material = new THREE.ShaderMaterial({
        uniforms: {
          uColor: { value: brandColor.clone() },
          uGlow: { value: 0.3 },
          uOpacity: { value: 0.5 },
          uLightInfluence: { value: 0.0 },
        },
        vertexShader: `
          varying vec2 vUv;
          void main() {
            vUv = uv;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
          }
        `,
        fragmentShader: `
          uniform vec3 uColor;
          uniform float uGlow;
          uniform float uOpacity;
          uniform float uLightInfluence;
          varying vec2 vUv;

          void main() {
            float gradient = smoothstep(0.0, 1.0, vUv.y);
            float edgeGlow = 1.0 - abs(vUv.x - 0.5) * 2.0;
            edgeGlow = pow(edgeGlow, 0.3);

            vec3 color = uColor * (0.7 + gradient * 0.3);
            color += uColor * uLightInfluence * gradient * 1.5;
            color += uColor * uGlow * gradient * 0.4;

            float alpha = (uOpacity + uLightInfluence * 0.4) * edgeGlow * (0.4 + gradient * 0.6);
            gl_FragColor = vec4(color, alpha);
          }
        `,
        transparent: true,
        blending: THREE.AdditiveBlending,
      });

      const bar = new THREE.Mesh(geometry, material);
      bar.position.x = (i - barCount / 2) * barSpacing + barSpacing / 2;
      bar.position.y = -height / 2;
      scene.add(bar);
      bars.push(bar);

      // Trading chart style - rising trend
      const normalizedIndex = i / barCount;
      const trendHeight = normalizedIndex * 0.7 + 0.1;
      const waveVariation = Math.sin(normalizedIndex * Math.PI * 3) * 0.08;
      const baseHeight = (trendHeight + waveVariation) * height * 0.55;

      barData.push({
        baseHeight,
        targetHeight: baseHeight,
        currentHeight: baseHeight,
      });
    }

    // Animation
    let animationId: number;
    let time = 0;

    const animate = () => {
      animationId = requestAnimationFrame(animate);
      time += 0.016;

      const currentWidth = dimensionsRef.current.width;
      const currentHeight = dimensionsRef.current.height;

      // Smooth mouse interpolation (slower for smoother transitions)
      mouseRef.current.x += (mouseRef.current.targetX - mouseRef.current.x) * 0.05;
      mouseRef.current.y += (mouseRef.current.targetY - mouseRef.current.y) * 0.05;

      // Glow position follows mouse horizontally - stays on right side, away from cursor
      const targetGlowX = 0.55 + mouseRef.current.x * 0.4; // Offset to right, follows mouse
      glowPositionRef.current.x += (targetGlowX - glowPositionRef.current.x) * 0.02;

      // Clamp glow to right half only (0.5 to 0.95)
      glowPositionRef.current.x = Math.max(0.5, Math.min(glowPositionRef.current.x, 0.95));

      const mouseX = mouseRef.current.x;
      const glowX = glowPositionRef.current.x;
      const glowY = 0.5; // Fixed vertical position

      // Update glow
      (glowMaterial.uniforms.uGlowPosition.value as THREE.Vector2).set(glowX, glowY);
      glowMaterial.uniforms.uTime.value = time;
      (glowMaterial.uniforms.uResolution.value as THREE.Vector2).set(currentWidth, currentHeight);

      bars.forEach((bar, i) => {
        const data = barData[i];
        const normalizedIndex = i / barCount;

        const glowDistance = Math.abs(normalizedIndex - glowX);
        const lightInfluence = Math.max(0, 1 - glowDistance * 2.5);

        const mouseDistance = Math.abs(normalizedIndex - mouseX);
        const mouseInfluence = Math.max(0, 1 - mouseDistance * 4);

        const mouseBoost = mouseInfluence * (1 - mouseRef.current.y) * currentHeight * 0.25;
        const lightBoost = lightInfluence * currentHeight * 0.1;

        data.targetHeight = data.baseHeight + mouseBoost + lightBoost;
        data.currentHeight += (data.targetHeight - data.currentHeight) * 0.08;

        bar.scale.y = Math.max(5, data.currentHeight);
        bar.position.y = -currentHeight / 2 + bar.scale.y / 2;

        const material = bar.material as THREE.ShaderMaterial;
        material.uniforms.uOpacity.value = 0.35 + lightInfluence * 0.25;
        material.uniforms.uGlow.value = 0.2 + lightInfluence * 0.5;
        material.uniforms.uLightInfluence.value = lightInfluence;
      });

      renderer.render(scene, camera);
    };

    animate();

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("touchmove", handleTouchMove, { passive: true });
    window.addEventListener("touchstart", handleTouchMove, { passive: true });
    window.addEventListener("touchend", handleTouchEnd, { passive: true });

    const handleResize = () => {
      if (!container) return;
      const newWidth = container.clientWidth;
      const newHeight = container.clientHeight;

      // Update dimensions ref
      dimensionsRef.current = { width: newWidth, height: newHeight };

      // Update camera
      camera.left = -newWidth / 2;
      camera.right = newWidth / 2;
      camera.top = newHeight / 2;
      camera.bottom = -newHeight / 2;
      camera.updateProjectionMatrix();

      // Update renderer
      renderer.setSize(newWidth, newHeight);

      // Update glow plane geometry
      glowPlane.geometry.dispose();
      glowPlane.geometry = new THREE.PlaneGeometry(newWidth * 1.5, newHeight * 1.5);

      // Update resolution uniform for aspect-corrected halftone
      (glowMaterial.uniforms.uResolution.value as THREE.Vector2).set(newWidth, newHeight);

      // Update bar positions and geometries
      const { barWidth: newBarWidth, barSpacing: newBarSpacing } = calculateBarLayout(newWidth, newHeight);

      bars.forEach((bar, i) => {
        // Update position
        bar.position.x = (i - barCount / 2) * newBarSpacing + newBarSpacing / 2;

        // Update geometry width
        bar.geometry.dispose();
        bar.geometry = new THREE.PlaneGeometry(newBarWidth, 1);

        // Recalculate base height
        const normalizedIndex = i / barCount;
        const trendHeight = normalizedIndex * 0.7 + 0.1;
        const waveVariation = Math.sin(normalizedIndex * Math.PI * 3) * 0.08;
        barData[i].baseHeight = (trendHeight + waveVariation) * newHeight * 0.55;
      });
    };

    window.addEventListener("resize", handleResize);

    return () => {
      cancelAnimationFrame(animationId);
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("touchmove", handleTouchMove);
      window.removeEventListener("touchstart", handleTouchMove);
      window.removeEventListener("touchend", handleTouchEnd);
      window.removeEventListener("resize", handleResize);

      if (container && renderer.domElement) {
        container.removeChild(renderer.domElement);
      }

      renderer.dispose();
      bars.forEach((bar) => {
        bar.geometry.dispose();
        (bar.material as THREE.ShaderMaterial).dispose();
      });
      glowPlane.geometry.dispose();
      glowMaterial.dispose();
    };
  }, [handleMouseMove, handleTouchMove, handleTouchEnd]);

  return (
    <div
      ref={containerRef}
      className="absolute inset-0"
      style={{ zIndex: 0 }}
    />
  );
}

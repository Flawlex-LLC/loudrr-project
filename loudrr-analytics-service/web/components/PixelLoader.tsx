// loudrr's PixelLoader — the logo with an expanding masked ring + pulse. Ported verbatim
// from the loudrr Telegram app so the loading state matches the product exactly.
export function PixelLoader({
  isComplete,
  size: sizeProp = "default",
}: {
  isComplete?: boolean;
  progress?: number;
  size?: "default" | "sm" | "xs";
}) {
  const sizeMap = { default: 72, sm: 32, xs: 20 } as const;
  const size = sizeMap[sizeProp];
  const dotSize = Math.max(2, Math.round(size / 18));
  return (
    <div className="loader-container" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
      <style>{`
        @keyframes circle-grow-${size} {
          0% {
            width: ${dotSize}px;
            height: ${dotSize}px;
            top: calc(50% - ${dotSize / 2}px);
            right: 0;
            opacity: 0.6;
          }
          100% {
            width: ${size}px;
            height: ${size}px;
            top: 0;
            right: 0;
            opacity: 0;
          }
        }
        @keyframes loudrr-logo-pulse {
          0% { opacity: 0.6; }
          50% { opacity: 1; }
          100% { opacity: 0.6; }
        }
      `}</style>
      <div style={{ position: "relative", width: size, height: size }}>
        {/* base logo */}
        <img
          src="/loudrr-icon.png"
          alt=""
          width={size}
          height={size}
          style={{
            width: size,
            height: size,
            display: "block",
            animation: isComplete ? "none" : "loudrr-logo-pulse 1.5s ease-in-out infinite",
          }}
        />
        {/* expanding circle — masked to the logo shape */}
        {!isComplete && (
          <div
            style={
              {
                position: "absolute",
                top: 0,
                left: 0,
                width: size,
                height: size,
                overflow: "hidden",
                maskImage: "url(/loudrr-icon.png)",
                maskSize: "100% 100%",
                maskRepeat: "no-repeat",
                WebkitMaskImage: "url(/loudrr-icon.png)",
                WebkitMaskSize: "100% 100%",
                WebkitMaskRepeat: "no-repeat",
              } as React.CSSProperties
            }
          >
            <div
              style={{
                position: "absolute",
                borderRadius: "50%",
                background:
                  "radial-gradient(circle, rgba(255,255,255,0.5) 0%, rgba(255,255,255,0.25) 50%, rgba(255,255,255,0.1) 100%)",
                mixBlendMode: "color",
                animation: `circle-grow-${size} 1.5s ease-out infinite`,
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}

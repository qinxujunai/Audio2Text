import {
  AbsoluteFill,
  Easing,
  Img,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";

const ease = Easing.bezier(0.16, 1, 0.3, 1);

function sceneOpacity(frame: number, start: number, end: number) {
  return interpolate(
    frame,
    [start, start + 15, end - 15, end],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease },
  );
}

const Brand = () => (
  <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
    <Img
      src={staticFile("brand-mark.svg")}
      style={{
        width: 54,
        height: 54,
        filter: "drop-shadow(0 12px 18px rgba(17,17,17,0.14))",
      }}
    />
    <div style={{ fontSize: 28, fontWeight: 650 }}>Praxis AI / 万象成文</div>
  </div>
);

const BrowserFrame = ({ src }: { src: string }) => (
  <div
    style={{
      width: 1340,
      height: 754,
      overflow: "hidden",
      borderRadius: 24,
      border: "1px solid rgba(17,17,17,0.12)",
      background: "#ffffff",
      boxShadow: "0 40px 100px rgba(17,17,17,0.14)",
    }}
  >
    <Img src={staticFile(src)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
  </div>
);

export const WanxiangDemo = () => {
  const frame = useCurrentFrame();

  return (
    <AbsoluteFill
      style={{
        background: "#f7f7f5",
        color: "#111111",
        fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif',
        letterSpacing: 0,
      }}
    >
      <AbsoluteFill
        style={{
          opacity: sceneOpacity(frame, 0, 120),
          padding: "86px 110px",
          display: "flex",
          flexDirection: "column",
          gap: 54,
        }}
      >
        <Brand />
        <div style={{ fontSize: 82, lineHeight: 1.12, fontWeight: 430, maxWidth: 1180 }}>
          把链接或文件，直接变成文字。
        </div>
        <div style={{ fontSize: 34, color: "#666666" }}>
          视频、播客、图文，一处整理。
        </div>
        <div
          style={{
            position: "absolute",
            right: -120,
            bottom: -260,
            scale: interpolate(frame, [0, 90], [0.92, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
              easing: ease,
              output: "perceptual-scale",
            }),
          }}
        >
          <BrowserFrame src="home-desktop.png" />
        </div>
      </AbsoluteFill>

      <AbsoluteFill
        style={{
          opacity: sceneOpacity(frame, 105, 255),
          padding: "72px 100px",
          display: "flex",
          alignItems: "center",
          gap: 72,
        }}
      >
        <div style={{ flex: "0 0 480px", display: "flex", flexDirection: "column", gap: 28 }}>
          <Brand />
          <div style={{ fontSize: 66, lineHeight: 1.13, fontWeight: 430 }}>
            文字先到，媒体随后。
          </div>
          <div style={{ fontSize: 30, lineHeight: 1.6, color: "#666666" }}>
            字幕与正文优先交付，视频、音频和图片继续准备。
          </div>
        </div>
        <div style={{ scale: 0.9 }}>
          <BrowserFrame src="result-video-desktop.png" />
        </div>
      </AbsoluteFill>

      <AbsoluteFill
        style={{
          opacity: sceneOpacity(frame, 240, 360),
          padding: "94px 120px",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
        }}
      >
        <Brand />
        <div>
          <div style={{ fontSize: 78, lineHeight: 1.12, fontWeight: 430, maxWidth: 1260 }}>
            本地优先，也能在线体验。
          </div>
          <div style={{ marginTop: 36, fontSize: 31, color: "#666666" }}>
            哔哩哔哩 · 小宇宙 · 抖音 · 小红书 · YouTube · 微信公众号
          </div>
        </div>
        <div
          style={{
            alignSelf: "flex-start",
            padding: "22px 34px",
            borderRadius: 999,
            background: "#111111",
            color: "#ffffff",
            fontSize: 27,
            fontWeight: 650,
          }}
        >
          下载 Windows 版
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

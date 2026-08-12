import {
  AbsoluteFill,
  Easing,
  Img,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";

const ink = "#111113";
const muted = "#6b6b70";
const soft = "#f5f6f7";
const blue = "#0a84ff";
const green = "#30b38c";
const amber = "#f2b84b";
const ease = Easing.bezier(0.16, 1, 0.3, 1);

const clamp = { extrapolateLeft: "clamp" as const, extrapolateRight: "clamp" as const };

function opacity(frame: number, start: number, end: number, fade = 12) {
  return interpolate(frame, [start, start + fade, end - fade, end], [0, 1, 1, 0], {
    ...clamp,
    easing: ease,
  });
}

const Brand = ({ compact = false }: { compact?: boolean }) => (
  <div style={{ display: "flex", alignItems: "center", gap: compact ? 14 : 20 }}>
    <Img src={staticFile("brand-mark.svg")} style={{ width: compact ? 44 : 62, height: compact ? 44 : 62 }} />
    <div style={{ fontSize: compact ? 25 : 32, fontWeight: 680 }}>Praxis AI / 万象成文</div>
  </div>
);

const SourceSymbol = ({ type, color }: { type: "circle" | "square" | "triangle"; color: string }) => {
  if (type === "triangle") {
    return <div style={{ width: 0, height: 0, borderLeft: "18px solid transparent", borderRight: "18px solid transparent", borderBottom: `32px solid ${color}` }} />;
  }
  return <div style={{ width: 34, height: 34, borderRadius: type === "circle" ? "50%" : 4, background: color }} />;
};

const LogoConvergence = ({ frame }: { frame: number }) => {
  const progress = interpolate(frame, [4, 42], [0, 1], { ...clamp, easing: ease });
  const origins = [170, 300, 430];
  const targets = [258, 300, 342];
  return (
    <div style={{ position: "relative", width: 620, height: 600 }}>
      <div style={{ position: "absolute", left: 50, top: 88, fontSize: 21, color: muted }}>视频</div>
      <div style={{ position: "absolute", left: 50, top: 278, fontSize: 21, color: muted }}>播客</div>
      <div style={{ position: "absolute", left: 50, top: 468, fontSize: 21, color: muted }}>图文</div>
      {[
        ["square", blue],
        ["circle", green],
        ["triangle", amber],
      ].map(([type, color], index) => (
        <div
          key={String(type)}
          style={{
            position: "absolute",
            left: interpolate(progress, [0, 1], [130, 250]),
            top: interpolate(progress, [0, 1], [origins[index], targets[index]]),
            translate: "-50% -50%",
          }}
        >
          <SourceSymbol type={type as "circle" | "square" | "triangle"} color={String(color)} />
        </div>
      ))}
      <svg width="620" height="600" viewBox="0 0 620 600" style={{ position: "absolute", inset: 0 }}>
        <path d="M278 258 C360 258 350 300 430 300" fill="none" stroke={ink} strokeWidth="30" strokeLinecap="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - progress} />
        <path d="M278 300 H430" fill="none" stroke={ink} strokeWidth="30" strokeLinecap="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - progress} />
        <path d="M278 342 C360 342 350 300 430 300" fill="none" stroke={ink} strokeWidth="30" strokeLinecap="round" pathLength="1" strokeDasharray="1" strokeDashoffset={1 - progress} />
        {[250, 300, 350].map((y) => <path key={y} d={`M460 ${y} H570`} fill="none" stroke={ink} strokeWidth="30" strokeLinecap="round" opacity={progress} />)}
      </svg>
    </div>
  );
};

const Window = ({ children }: { children: React.ReactNode }) => (
  <div style={{ width: 1450, height: 800, overflow: "hidden", border: "1px solid rgba(17,17,19,0.12)", borderRadius: 18, background: "#fff", boxShadow: "0 34px 90px rgba(17,17,19,0.12)" }}>
    <div style={{ height: 58, display: "flex", alignItems: "center", gap: 10, padding: "0 22px", borderBottom: "1px solid rgba(17,17,19,0.1)", background: "#fbfbfc" }}>
      {["#ff5f57", "#febc2e", "#28c840"].map((color) => <span key={color} style={{ width: 12, height: 12, borderRadius: "50%", background: color }} />)}
      <div style={{ marginLeft: 18, color: muted, fontSize: 16 }}>万象成文</div>
    </div>
    <div style={{ height: 742, position: "relative" }}>{children}</div>
  </div>
);

const InputScene = ({ frame }: { frame: number }) => {
  const text = "https://www.bilibili.com/video/BV1sHU9BmEne/?p=114";
  const length = Math.floor(interpolate(frame, [55, 92], [0, text.length], clamp));
  const pressed = frame >= 98;
  return (
    <AbsoluteFill style={{ opacity: opacity(frame, 38, 142), alignItems: "center", justifyContent: "center", paddingTop: 28 }}>
      <Window>
        <div style={{ padding: "64px 105px" }}>
          <Brand compact />
          <h2 style={{ margin: "52px 0 12px", fontSize: 61, fontWeight: 600 }}>把链接或文件，直接变成文字。</h2>
          <p style={{ margin: 0, color: muted, fontSize: 24 }}>视频、播客、图文，一处整理。</p>
          <div style={{ marginTop: 48, minHeight: 190, padding: "30px 34px", border: "1px solid rgba(17,17,19,0.15)", borderRadius: 16, background: "#fff", fontSize: 24, color: length ? ink : "#999" }}>
            {text.slice(0, length)}<span style={{ opacity: frame % 18 < 9 ? 1 : 0, color: blue }}>|</span>
            <div style={{ position: "absolute", right: 134, top: 330, width: 58, height: 58, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: "50%", background: pressed ? ink : "#d1d1d3", color: "#fff", fontSize: 28, scale: pressed ? 0.92 : 1 }}>→</div>
          </div>
          <p style={{ marginTop: 22, color: muted, textAlign: "center", fontSize: 18 }}>公开内容入页，片刻之后，自会落字成文。</p>
        </div>
      </Window>
    </AbsoluteFill>
  );
};

const ProcessingScene = ({ frame }: { frame: number }) => {
  const steps = [
    { at: 145, label: "识别来源", detail: "哔哩哔哩 · 视频" },
    { at: 168, label: "提取内容", detail: "字幕与媒体已取得" },
    { at: 191, label: "转写音频", detail: "自动选择本机识别路径" },
  ];
  return (
    <AbsoluteFill style={{ opacity: opacity(frame, 130, 220), alignItems: "center", justifyContent: "center" }}>
      <div style={{ width: 1120 }}>
        <Brand />
        <h2 style={{ margin: "54px 0 16px", fontSize: 70, fontWeight: 560 }}>文字先到，媒体随后。</h2>
        <p style={{ margin: 0, color: muted, fontSize: 27 }}>每一步都说明正在发生什么。</p>
        <div style={{ marginTop: 62, borderTop: "1px solid rgba(17,17,19,0.14)" }}>
          {steps.map((step, index) => {
            const complete = frame >= step.at;
            return (
              <div key={step.label} style={{ minHeight: 88, display: "grid", gridTemplateColumns: "52px 240px 1fr", alignItems: "center", borderBottom: "1px solid rgba(17,17,19,0.14)", opacity: interpolate(frame, [step.at - 18, step.at], [0.28, 1], clamp) }}>
                <span style={{ width: 28, height: 28, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", background: complete ? green : "#e5e5e7", color: "#fff", fontSize: 16 }}>{complete ? "✓" : index + 1}</span>
                <strong style={{ fontSize: 24 }}>{step.label}</strong>
                <span style={{ color: muted, fontSize: 21 }}>{step.detail}</span>
              </div>
            );
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
};

const ResultScene = ({ frame }: { frame: number }) => (
  <AbsoluteFill style={{ opacity: opacity(frame, 205, 312), alignItems: "center", justifyContent: "center" }}>
    <div style={{ position: "relative", width: 1490, height: 838, overflow: "hidden", borderRadius: 18, border: "1px solid rgba(17,17,19,0.12)", background: "#fff", boxShadow: "0 34px 90px rgba(17,17,19,0.12)" }}>
      <Img src={staticFile("result-video-desktop.png")} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      {[
        { frameAt: 225, left: 330, top: 658, label: "正文" },
        { frameAt: 245, left: 995, top: 322, label: "音频" },
        { frameAt: 265, left: 1146, top: 322, label: "视频" },
      ].map((item) => (
        <div key={item.label} style={{ position: "absolute", left: item.left, top: item.top, minWidth: 112, height: 48, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 8, background: ink, color: "#fff", fontSize: 18, fontWeight: 650, opacity: interpolate(frame, [item.frameAt - 8, item.frameAt], [0, 1], clamp), translate: `0 ${interpolate(frame, [item.frameAt - 8, item.frameAt], [10, 0], clamp)}px` }}>{item.label}已就绪</div>
      ))}
    </div>
  </AbsoluteFill>
);

const ClosingScene = ({ frame }: { frame: number }) => (
  <AbsoluteFill style={{ opacity: interpolate(frame, [292, 314], [0, 1], { ...clamp, easing: ease }), alignItems: "center", justifyContent: "center", background: ink, color: "#fff" }}>
    <Img src={staticFile("brand-mark.svg")} style={{ width: 112, height: 112 }} />
    <h2 style={{ margin: "36px 0 0", fontSize: 78, lineHeight: 1.1, fontWeight: 680 }}>万象成文</h2>
    <p style={{ margin: "20px 0 0", color: "#b9b9be", fontSize: 30 }}>把内容带回来。</p>
    <div style={{ marginTop: 48, display: "flex", gap: 18 }}>
      <div style={{ minHeight: 58, padding: "0 26px", display: "flex", alignItems: "center", borderRadius: 8, background: "#fff", color: ink, fontSize: 21, fontWeight: 650 }}>下载 Windows 版</div>
      <div style={{ minHeight: 58, padding: "0 26px", display: "flex", alignItems: "center", border: "1px solid rgba(255,255,255,0.32)", borderRadius: 8, color: "#fff", fontSize: 21, fontWeight: 650 }}>在线试用</div>
    </div>
  </AbsoluteFill>
);

export const WanxiangDemo = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ background: "#fff", color: ink, fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif', letterSpacing: 0 }}>
      <AbsoluteFill style={{ opacity: interpolate(frame, [38, 54], [1, 0], { ...clamp, easing: ease }), display: "grid", gridTemplateColumns: "1fr 1fr", alignItems: "center", padding: "70px 150px" }}>
        <div>
          <Brand />
          <h1 style={{ margin: "58px 0 18px", fontSize: 78, lineHeight: 1.1, fontWeight: 650 }}>万象入页，<br />落字成文。</h1>
          <p style={{ margin: 0, color: muted, fontSize: 29 }}>视频、播客与图文，收拢为可用结果。</p>
        </div>
        <LogoConvergence frame={frame} />
      </AbsoluteFill>
      <InputScene frame={frame} />
      <ProcessingScene frame={frame} />
      <ResultScene frame={frame} />
      <ClosingScene frame={frame} />
    </AbsoluteFill>
  );
};

export const WanxiangSocial = () => (
  <AbsoluteFill style={{ background: "#fff", color: ink, fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif', padding: "70px 82px", display: "grid", gridTemplateColumns: "1fr 0.9fr", alignItems: "center", letterSpacing: 0 }}>
    <div>
      <Brand />
      <h1 style={{ margin: "54px 0 18px", fontSize: 58, lineHeight: 1.14, fontWeight: 680, whiteSpace: "nowrap" }}>把视频、播客和图文，<br />直接变成可用结果。</h1>
      <p style={{ margin: 0, color: muted, fontSize: 25 }}>Windows 本地优先 · 开源发布</p>
    </div>
    <div style={{ justifySelf: "end", width: 400, height: 400, borderRadius: 88, background: soft, display: "flex", alignItems: "center", justifyContent: "center" }}>
      <Img src={staticFile("brand-mark.svg")} style={{ width: 300, height: 300 }} />
    </div>
  </AbsoluteFill>
);

const copy = {
  zh: {
    title: "万象成文 — 视频、播客与图文，一处整理",
    description: "万象成文把视频、播客和图文，直接变成可用的文字与媒体。Windows 本地优先。",
    skip: "跳到主要内容", brandHome: "万象成文首页", primaryNav: "主要导航", languageSwitch: "语言",
    navWorkflow: "产品", navProof: "验证", eyebrow: "Windows 本地优先的内容整理工具",
    heroLede: "把视频、播客和图文，直接变成可用的文字与媒体。", download: "下载 Windows 版", trial: "在线试用", github: "查看 GitHub",
    demoLabel: "万象成文从链接输入到文字与媒体交付的十二秒演示", playDemo: "播放或暂停演示", play: "播放演示", pause: "暂停演示",
    workflowKicker: "一个入口，四类交付", workflowTitle: "内容进来，结果留下。", workflowBody: "粘贴公开链接或选择本地文件。能直接取得正文或字幕时立即交付；需要识别时，再自动选择当前电脑适合的路径。",
    flowLabel: "万象成文处理流程", flowInput: "视频、播客、图文", flowProcess: "提取与识别", flowOutput: "文字与媒体",
    textTitle: "可用的正文", textBody: "复制全文，或下载 TXT 与 Markdown。", mediaTitle: "原始媒体", mediaBody: "视频、音频和图片分别交付，不混在一起。", localTitle: "本地优先", localBody: "Windows 版在本机处理，自动检测 CPU 与 GPU。",
    platformKicker: "真实平台，明确状态", platformTitle: "不是一张“支持全部”的海报。", stable: "已验证", variable: "高波动", network: "视网络而定", platformNote: "最近验证：2026-08-11。平台页面会变化；失败时产品会说明原因和恢复方式。",
    proofKicker: "发布证据", proofTitle: "安装包与页面，来自同一个版本。", proofBody: "网站只从已发布的 GitHub Release 读取下载地址与校验值。安装包尚未代码签名，Windows 可能显示安全提示。", version: "版本", size: "安装包",
    closingTitle: "把内容带回来。", closingBody: "在自己的电脑上，整理成真正能继续使用的结果。", security: "安全", feedback: "反馈"
  },
  en: {
    title: "Wanxiang Chengwen — Turn media into usable text",
    description: "Turn videos, podcasts, and visual articles into usable text and media. Local-first on Windows.",
    skip: "Skip to main content", brandHome: "Wanxiang Chengwen home", primaryNav: "Primary navigation", languageSwitch: "Language",
    navWorkflow: "Product", navProof: "Verification", eyebrow: "Local-first content capture for Windows",
    heroLede: "Turn videos, podcasts, and visual articles into usable text and media.", download: "Download for Windows", trial: "Try online", github: "View on GitHub",
    demoLabel: "A twelve-second demo from pasted link to text and media deliverables", playDemo: "Play or pause demo", play: "Play demo", pause: "Pause demo",
    workflowKicker: "One input, four deliverables", workflowTitle: "Bring content in. Keep the result.", workflowBody: "Paste a public link or choose a local file. Existing text and captions arrive first; when transcription is needed, the app selects a compatible path for this PC.",
    flowLabel: "Wanxiang Chengwen workflow", flowInput: "Video, podcast, article", flowProcess: "Extract and transcribe", flowOutput: "Text and media",
    textTitle: "Usable text", textBody: "Copy everything or download TXT and Markdown.", mediaTitle: "Original media", mediaBody: "Video, audio, and images remain distinct deliverables.", localTitle: "Local first", localBody: "The Windows app processes locally and detects CPU and GPU automatically.",
    platformKicker: "Real platforms, explicit status", platformTitle: "More honest than a “supports everything” poster.", stable: "Verified", variable: "Variable", network: "Network dependent", platformNote: "Last verified: 2026-08-11. Platform pages change; failures explain the cause and the recovery path.",
    proofKicker: "Release evidence", proofTitle: "The page and installer come from one release.", proofBody: "This site reads its download URL and checksum only from a published GitHub Release. The installer is currently unsigned, so Windows may show a security prompt.", version: "Version", size: "Installer",
    closingTitle: "Bring the content back.", closingBody: "Turn it into something you can keep using, on your own PC.", security: "Security", feedback: "Feedback"
  }
};

const languageButtons = [...document.querySelectorAll("[data-language]")];
const film = document.querySelector("video");
const filmControl = document.querySelector(".film-control");
const filmControlText = filmControl?.querySelector("[data-i18n]");
let language = localStorage.getItem("wanxiang-language") === "en" ? "en" : "zh";

function applyLanguage(nextLanguage) {
  language = nextLanguage;
  const selected = copy[language];
  document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  document.title = selected.title;
  document.querySelector('meta[name="description"]').content = selected.description;
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    const key = element.dataset.i18n;
    if (selected[key]) element.textContent = selected[key];
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((element) => {
    const key = element.dataset.i18nAria;
    if (selected[key]) element.setAttribute("aria-label", selected[key]);
  });
  languageButtons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.language === language)));
  if (filmControlText) filmControlText.textContent = film && !film.paused ? selected.pause : selected.play;
  localStorage.setItem("wanxiang-language", language);
}

languageButtons.forEach((button) => button.addEventListener("click", () => applyLanguage(button.dataset.language)));
filmControl?.addEventListener("click", async () => {
  if (!film) return;
  if (film.paused) await film.play(); else film.pause();
  filmControl.setAttribute("aria-pressed", String(!film.paused));
  filmControlText.textContent = film.paused ? copy[language].play : copy[language].pause;
});
film?.addEventListener("ended", () => filmControl?.setAttribute("aria-pressed", "false"));

applyLanguage(language);
if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
  film?.play().then(() => {
    filmControl?.setAttribute("aria-pressed", "true");
    if (filmControlText) filmControlText.textContent = copy[language].pause;
  }).catch(() => {});
}

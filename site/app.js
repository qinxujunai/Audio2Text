const copy = {
  zh: {
    title: "万象成文 — 视频、播客与图文，一处整理",
    description: "万象成文把视频、播客和图文，直接变成可用的文字与媒体。Windows 本地优先。",
    skip: "跳到主要内容", brandHome: "万象成文首页", primaryNav: "主要导航", languageSwitch: "语言",
    navWorkflow: "产品", navProof: "验证", eyebrow: "Windows 本地优先的内容整理工具",
    heroLede: "把视频、播客和图文，直接变成可用的文字与媒体。", download: "下载 Windows 版", trial: "在线试用", github: "查看 GitHub",
    demoNote: "演示使用开发预览版与预置示例，等待过程已缩短；正式下载版本见上方。", demoLabel: "万象成文从链接输入到文字与媒体交付的十二秒演示", playDemo: "播放或暂停演示", play: "播放演示", pause: "暂停演示",
    workflowKicker: "一个入口", workflowTitle: "贴一个链接，拿回全部文字。", workflowBody: "粘贴公开链接或选择本地文件。能直接取得正文或字幕时立即交付；需要识别时，再自动选择当前电脑适合的路径。",
    flowLabel: "万象成文处理流程", flowInput: "视频、播客、图文", flowProcess: "提取与识别", flowOutput: "文字与媒体",
    textTitle: "可用的正文", textBody: "复制全文，或下载 TXT 与 Markdown。", mediaTitle: "原始媒体", mediaBody: "视频、音频和图片分别交付，不混在一起。", localTitle: "本地优先", localBody: "Windows 版在本机处理，自动检测 CPU 与 GPU。",
    platformKicker: "支持的平台", platformTitle: "来自你熟悉的平台。", stable: "已验证", variable: "高波动", network: "视网络而定", platformNote: "平台内容和访问条件会变化。遇到无法读取的链接时，应用会说明下一步。",
    proofKicker: "下载", proofTitle: "下载的每个文件，都能核对。", proofBody: "网站只从已发布的 GitHub Release 读取下载地址与校验值。安装包尚未代码签名，Windows 可能显示安全提示。", version: "版本", size: "安装包",
    closingTitle: "下一条链接，交给它。", closingBody: "在你自己的电脑上，变成能继续用的文字。", security: "安全", feedback: "反馈"
  },
  en: {
    title: "Wanxiang Chengwen — Turn media into usable text",
    description: "Turn videos, podcasts, and visual articles into usable text and media. Local-first on Windows.",
    skip: "Skip to main content", brandHome: "Wanxiang Chengwen home", primaryNav: "Primary navigation", languageSwitch: "Language",
    navWorkflow: "Product", navProof: "Verification", eyebrow: "Local-first content capture for Windows",
    heroLede: "Turn videos, podcasts, and visual articles into usable text and media.", download: "Download for Windows", trial: "Try online", github: "View on GitHub",
    demoNote: "The demo uses a development preview with prepared examples and shortened waits. The public download version is shown above.", demoLabel: "A twelve-second demo from pasted link to text and media deliverables", playDemo: "Play or pause demo", play: "Play demo", pause: "Pause demo",
    workflowKicker: "One input", workflowTitle: "Paste a link. Get every word back.", workflowBody: "Paste a public link or choose a local file. Existing text and captions arrive first; when transcription is needed, the app selects a compatible path for this PC.",
    flowLabel: "Wanxiang Chengwen workflow", flowInput: "Video, podcast, article", flowProcess: "Extract and transcribe", flowOutput: "Text and media",
    textTitle: "Usable text", textBody: "Copy everything or download TXT and Markdown.", mediaTitle: "Original media", mediaBody: "Video, audio, and images remain distinct deliverables.", localTitle: "Local first", localBody: "The Windows app processes locally and detects CPU and GPU automatically.",
    platformKicker: "Supported platforms", platformTitle: "From the platforms you use.", stable: "Verified", variable: "Variable", network: "Network dependent", platformNote: "Content and access conditions change. If a link cannot be read, the app explains the next step.",
    proofKicker: "Download", proofTitle: "Every download, verifiable.", proofBody: "This site reads its download URL and checksum only from a published GitHub Release. The installer is currently unsigned, so Windows may show a security prompt.", version: "Version", size: "Installer",
    closingTitle: "Hand it the next link.", closingBody: "On your own PC, turned into text you can keep using.", security: "Security", feedback: "Feedback"
  }
};

Object.assign(copy.zh, {
  navProof: "下载", demoNote: "演示使用开发预览版与预置示例，等待过程已缩短；正式下载版本见上方。",
  workflowKicker: "从输入到保存", workflowBody: "粘贴链接或选择文件，查看正文与媒体，再复制或保存。已有正文或字幕时优先提取；需要识别时，在本机完成。",
  localBody: "本地文件在电脑上识别，支持可选 CUDA 加速。",
  scenariosKicker: "让有价值的内容，留得下来", scenariosTitle: "听过、看过。也能找回。",
  scenarioVideo: "长视频", scenarioVideoBody: "留下完整文字，回看视频，复制值得保留的那一段。",
  scenarioPodcast: "播客", scenarioPodcastBody: "把对话变成正文，让听到的内容也能被搜索和引用。",
  scenarioFile: "本地音视频", scenarioFileBody: "选择自己的录音或视频，在电脑上完成语音识别。",
  scenarioArticle: "图文", scenarioArticleBody: "正文与图片一起留下，阅读、查看与保存各得其所。",
  localProofKicker: "识别在本地，修正由你决定", localProofTitle: "保留原话。只改明确的错。",
  localProofBody: "AI 校对提出小范围修正，逐处确认后另存一份。原文一直保留，不润色、不总结、不删段。",
  proofDemoLabel: "校对操作示例", proofDemoTitle: "确认每一处修正", exampleOnly: "示例文字",
  demoOriginal: "这里有一个错别宇。金额三百元。保留所有原话。", demoCorrected: "这里有一个错别字。金额三百元。保留所有原话。",
  demoReason: "仅修正错别字", demoPreserved: "勾选建议预览校对版，原文保持不变。",
  gpuBody: "支持 NVIDIA CUDA 加速，实际运行设备在本地设置中可查看。也可选择 CPU。",
  privacyBody: "本地识别不上传音频。启用远程 AI 校对时，会把待校对文字发送给你配置的模型服务；费用由该服务收取。",
  proofKicker: "下载与版本", proofTitle: "在自己的电脑上，开始整理。",
  proofBody: "下载来自 GitHub Release，版本与校验值自动同步。当前安装包尚未代码签名，Windows 可能显示安全提示。",
  faqTitle: "开始之前，了解这些。", faqSystem: "需要什么电脑？",
  faqSystemBody: "Windows 10 / 11，64 位。CPU 可完成本地识别；NVIDIA 显卡可使用可选 CUDA 组件。首次启动需要联网准备模型与运行组件，之后本地文件可离线识别。",
  faqSize: "为什么首次启动还要下载？", faqSizeBody: "安装包包含基础应用，识别模型与媒体组件按需下载并核对校验值。CUDA 模型与组件独立准备，不会自动更改显卡驱动或系统环境。",
  faqPlatforms: "哪些链接可以整理？", faqPlatformsBody: "支持哔哩哔哩、小宇宙、抖音、小红书与微信公众号图文。能否读取也取决于网络、登录与平台访问规则；受限内容会提示恢复方式。平台支持不代表每条链接都能成功。",
  faqAI: "AI 校对能保证正确吗？", faqAIBody: "不能。模型只提出建议，你逐处确认后才能保存。数字与单位有额外保护，但人名、事实和语义仍需自己核对；原文与校对版始终分别保留。",
  faqSafety: "Windows 为什么提示安全警告？", faqSafetyBody: "当前安装包尚未代码签名。请从本页的 GitHub Release 下载，并核对上方 SHA-256；遇到问题可通过页脚反馈入口提交。"
});
Object.assign(copy.en, {
  navProof: "Download", demoNote: "The demo uses a development preview with prepared examples and shortened waits. The public download version is shown above.",
  workflowKicker: "From input to saved result", workflowBody: "Paste a link or choose a file. Read the text, preview the media, then copy or save. Existing text and captions come first; local recognition handles the rest.",
  localBody: "Recognize local files on your PC, with optional CUDA acceleration.",
  scenariosKicker: "Keep what matters", scenariosTitle: "Heard it. Seen it. Find it again.",
  scenarioVideo: "Long videos", scenarioVideoBody: "Keep the full transcript, revisit the video, and copy the passage that matters.",
  scenarioPodcast: "Podcasts", scenarioPodcastBody: "Turn conversations into text you can search and quote.",
  scenarioFile: "Local recordings", scenarioFileBody: "Choose your audio or video and recognize speech on your own computer.",
  scenarioArticle: "Visual articles", scenarioArticleBody: "Keep text and images together, ready to read, view, and save.",
  localProofKicker: "Local recognition. Your corrections.", localProofTitle: "Keep the words. Correct clear errors.",
  localProofBody: "AI suggests small corrections. Review each one and save a separate copy. The original stays intact, without rewriting, summaries, or omitted passages.",
  proofDemoLabel: "Proofreading interaction example", proofDemoTitle: "Review each correction", exampleOnly: "Example text",
  demoOriginal: "A Chinese typo: 错别宇. Amount: 三百元. Keep every word.", demoCorrected: "A Chinese typo: 错别字. Amount: 三百元. Keep every word.",
  demoReason: "Typo correction only", demoPreserved: "Select the suggestion to preview a corrected copy. The original stays unchanged.",
  gpuBody: "Optional NVIDIA CUDA acceleration. View the actual execution device in local settings, or choose CPU.",
  privacyBody: "Local recognition does not upload audio. Remote AI proofreading sends transcript text to your configured model service; that service determines the cost.",
  proofKicker: "Download and version", proofTitle: "Start on your own computer.",
  proofBody: "Downloads come from GitHub Releases, with synchronized versions and checksums. The installer is currently unsigned; Windows may show a security prompt.",
  faqTitle: "Before you start.", faqSystem: "What computer do I need?",
  faqSystemBody: "Windows 10 / 11, 64-bit. CPU recognition is supported; NVIDIA GPUs can use optional CUDA components. Initial setup downloads models and runtime components. Local files can then be recognized offline.",
  faqSize: "Why are there first-run downloads?", faqSizeBody: "The installer contains the base app. Models and media components are downloaded separately and checked against their hashes. GPU models and components are optional; the app does not change drivers or system settings.",
  faqPlatforms: "Which links are supported?", faqPlatformsBody: "Bilibili, Xiaoyuzhou, Douyin, Xiaohongshu, and WeChat articles. Access depends on your network, login, and platform rules. Support does not guarantee every link will work.",
  faqAI: "Are AI corrections always right?", faqAIBody: "No. Review each suggestion before saving. Numbers and units have extra guards, but names, facts, and meaning still need your judgment. Original and corrected versions remain separate.",
  faqSafety: "Why does Windows show a warning?", faqSafetyBody: "The installer is currently unsigned. Download from this page's GitHub Release and check the SHA-256 above. Use the feedback link if you encounter a problem."
});

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
  updateCorrectionDemo();
}

function updateCorrectionDemo() {
  const result = document.querySelector(".demo-result");
  if (result) result.textContent = copy[language][document.querySelector("#demo-correction")?.checked ? "demoCorrected" : "demoOriginal"];
}
document.querySelector("#demo-correction")?.addEventListener("change", updateCorrectionDemo);

languageButtons.forEach((button) => button.addEventListener("click", () => applyLanguage(button.dataset.language)));
filmControl?.addEventListener("click", async () => {
  if (!film) return;
  if (film.paused) { try { await film.play(); } catch { return; } } else film.pause();
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

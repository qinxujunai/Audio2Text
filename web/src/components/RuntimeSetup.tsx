import { Download, LoaderCircle } from "lucide-react";

import type { RuntimePack } from "../types";

type RuntimeSetupProps = {
  packs: RuntimePack[];
  busy: boolean;
  error: string;
  onInstall: () => void;
};

function formatSize(bytes?: number) {
  if (!bytes) return "";
  return `${Math.ceil(bytes / 1024 / 1024)} MB`;
}

export function RuntimeSetup({ packs, busy, error, onInstall }: RuntimeSetupProps) {
  const downloadBytes = packs.reduce((total, pack) => total + pack.size_bytes, 0);

  return (
    <section className="runtime-setup" aria-labelledby="runtime-setup-title">
      <div className="runtime-setup-mark" aria-hidden="true">
        {busy ? <LoaderCircle size={24} /> : <Download size={24} />}
      </div>
      <p className="runtime-setup-eyebrow">首次使用</p>
      <h1 id="runtime-setup-title">准备本地转写</h1>
      <p className="runtime-setup-copy">
        安装本地识别与媒体组件后，音视频会直接在这台电脑上处理。
        {downloadBytes ? ` 共下载约 ${formatSize(downloadBytes)}。` : ""}
      </p>
      {error ? <p className="runtime-setup-error" role="alert">{error}</p> : null}
      <button
        type="button"
        className="runtime-setup-action"
        disabled={busy || !packs.length}
        onClick={onInstall}
      >
        {busy ? <LoaderCircle className="is-spinning" size={18} /> : <Download size={18} />}
        <span>{busy ? "正在安全安装" : "安装并继续"}</span>
      </button>
      <p className="runtime-setup-note">文件会经过完整性校验，安装失败不会覆盖现有组件。</p>
    </section>
  );
}

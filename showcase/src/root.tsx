import { Composition } from "remotion";

import { WanxiangDemo, WanxiangSocial } from "./wanxiang-demo";

export const RemotionRoot = () => (
  <>
    <Composition id="WanxiangDemo" component={WanxiangDemo} durationInFrames={360} fps={30} width={1920} height={1080} />
    <Composition id="WanxiangSocial" component={WanxiangSocial} durationInFrames={1} fps={30} width={1280} height={640} />
  </>
);

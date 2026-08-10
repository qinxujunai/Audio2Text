import { Composition } from "remotion";

import { WanxiangDemo } from "./wanxiang-demo";

export const RemotionRoot = () => (
  <Composition
    id="WanxiangDemo"
    component={WanxiangDemo}
    durationInFrames={360}
    fps={30}
    width={1920}
    height={1080}
  />
);

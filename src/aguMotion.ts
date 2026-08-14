import aguCompleteUrl from "../assets/brand/agu/agu-complete-v1.webp";
import aguDynamicUrl from "../assets/brand/agu/agu-dynamic-v1.webp";
import aguStandardUrl from "../assets/brand/agu/agu-standard-v1.webp";

export type AguMotionMode = "welcome" | "working" | "complete";

const motionLabels: Record<AguMotionMode, string> = {
  welcome: "阿谷跑来挥手欢迎",
  working: "阿谷正在处理课表",
  complete: "阿谷举起已完成的课表庆祝"
};

export function renderAguMotion(mode: AguMotionMode, compact = false) {
  const modifier = compact ? " aguMotion--compact" : "";
  const restSource = mode === "complete" ? aguCompleteUrl : aguStandardUrl;
  const actionSource = mode === "complete" ? aguCompleteUrl : aguDynamicUrl;

  return `<figure class="aguMotion aguMotion--${mode}${modifier}" role="img" aria-label="${motionLabels[mode]}">
    <span class="aguMotion__ground" aria-hidden="true"></span>
    <span class="aguMotion__coreGlow" aria-hidden="true"></span>
    <img class="aguMotion__pose aguMotion__pose--rest" src="${restSource}" alt="" aria-hidden="true" />
    <img class="aguMotion__pose aguMotion__pose--action" src="${actionSource}" alt="" aria-hidden="true" />
    <span class="aguMotion__spark aguMotion__spark--one" aria-hidden="true"></span>
    <span class="aguMotion__spark aguMotion__spark--two" aria-hidden="true"></span>
    <span class="aguMotion__spark aguMotion__spark--three" aria-hidden="true"></span>
  </figure>`;
}

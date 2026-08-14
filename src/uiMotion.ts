export const motionDurations = {
  press: 90,
  hover: 140,
  content: 220,
  panel: 300
} as const;

export function createFrameScheduler(callback: () => void) {
  let frame: number | null = null;
  return () => {
    if (frame !== null) return;
    frame = window.requestAnimationFrame(() => {
      frame = null;
      callback();
    });
  };
}

export function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function commitAfterExit(element: HTMLElement, commit: () => void) {
  if (prefersReducedMotion()) {
    commit();
    return;
  }
  let completed = false;
  const finish = () => {
    if (completed) return;
    completed = true;
    element.removeEventListener("transitionend", finish);
    commit();
  };
  element.addEventListener("transitionend", finish, { once: true });
  element.classList.add("is-removing");
  window.setTimeout(finish, motionDurations.content + 60);
}

export function onNextFrame(callback: () => void) {
  window.requestAnimationFrame(() => window.requestAnimationFrame(callback));
}

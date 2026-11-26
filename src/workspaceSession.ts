import type { SidecarClient } from "./sidecarClient";
import type { CalendarSettings } from "./types";

export type SavedWorkspace = {
  calendar?: CalendarSettings;
  start_date?: string;
  end_date?: string;
  job_id: string;
  status: string;
  files: { name: string; path: string }[];
  remember: boolean;
};

export function needsOriginal(path: string) {
  return !path.includes("/") && !path.includes("\\");
}

export function rememberedChoice(checked: boolean) {
  return `<label class="rememberChoice"><input type="checkbox" data-remember-sources ${checked ? "checked" : ""} />在这台电脑记住原件位置 30 天</label>`;
}

export async function readLinkedPdf(client: SidecarClient, jobId: string, hash: string, name: string) {
  const { data } = await client.request<{data: string}>("review.source_pdf", {
    job_id: jobId, source_hash: hash, file_name: name
  });
  return Uint8Array.from(atob(data), c => c.charCodeAt(0));
}

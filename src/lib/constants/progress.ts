export const PROGRESS_STATUSES = [
  "not_started",
  "in_progress",
  "skipped",
  "done",
] as const;

export type ProgressStatus = (typeof PROGRESS_STATUSES)[number];

export const PROGRESS_STATUS_LABEL: Record<ProgressStatus, string> = {
  not_started: "Not started",
  in_progress: "In progress",
  skipped: "Skipped",
  done: "Done",
};

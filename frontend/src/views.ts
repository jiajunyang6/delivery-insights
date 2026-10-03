import type { Audience } from "./types";

export const viewLabels: Record<Audience, string> = {
  director: "Delivery Overview",
  manager: "PR & Review Details",
};

export const viewDescriptions: Record<Audience, string> = {
  director: "Delivery outcomes and the top three bottlenecks",
  manager: "All bottlenecks, review queues, areas, and at-risk pull requests",
};

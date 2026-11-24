// Private scheduled function: Vercel Schedules -> GitHub workflow_dispatch.
// See api/schedules/_core.mjs for what it dispatches and why. Only Vercel's
// scheduler can invoke this function (schedule/v1beta trigger in
// vercel.json); it has no public route.
import { handleSchedule } from '@vercel/schedules';

import { runSchedule } from './_core.mjs';

export const POST = handleSchedule(async (metadata) => {
  await runSchedule(metadata);
});

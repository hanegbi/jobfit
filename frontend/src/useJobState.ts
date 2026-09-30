import { useMutation, useQueryClient } from "@tanstack/react-query";

import { setJobState } from "./api";
import type { JobState } from "./types";

export type StateFlag = keyof JobState;

/** The state a job would have after flipping one flag. Pure, so the optimistic
 * update and the server agree on what "toggle" means. */
export function nextState(current: JobState, flag: StateFlag): JobState {
  return { ...current, [flag]: !current[flag] };
}

/** Toggling is optimistic and rolls back on failure: a like that silently
 * fails is worse than one that visibly does. */
export function useToggleJobState() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId, flag, current }: { jobId: string; flag: StateFlag; current: JobState }) =>
      setJobState(jobId, { [flag]: !current[flag] }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["job"] });
    },
  });
}

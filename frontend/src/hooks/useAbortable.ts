import { useCallback, useEffect, useRef, type DependencyList } from "react";

/**
 * Abort superseded requests and release the current request on unmount, so a slow
 * response for old inputs cannot overwrite state set for newer ones.
 */
export function useAbortable(
  effect?: (signal: AbortSignal) => void,
  dependencies: DependencyList = [],
) {
  const controller = useRef<AbortController>();
  const start = useCallback(() => {
    controller.current?.abort();
    controller.current = new AbortController();
    return controller.current.signal;
  }, []);
  useEffect(() => {
    if (effect) effect(start());
    return () => controller.current?.abort();
  }, [start, ...dependencies]);
  return start;
}

import { useEffect, useEffectEvent, useState } from "react";
import { isAbortError } from "@/lib/api";
import type { Page } from "@/types/api";

/*
 * Small data-loading hooks. `loading` is derived from the request key rather than
 * set inside the effect, so a changed key shows the loading state immediately and
 * stale data is never presented under a new filter.
 */

interface ResourceState<T> {
  key: string;
  data: T | null;
  error: unknown;
}

export function useResource<T>(
  key: string,
  fetcher: (signal: AbortSignal) => Promise<T>,
): {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
  setData: (data: T) => void;
} {
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<ResourceState<T> | null>(null);
  const queryKey = `${key}#${tick}`;
  const run = useEffectEvent((signal: AbortSignal) => fetcher(signal));

  useEffect(() => {
    const controller = new AbortController();
    run(controller.signal)
      .then((data) => setState({ key: queryKey, data, error: null }))
      .catch((error: unknown) => {
        if (!isAbortError(error))
          setState({ key: queryKey, data: null, error });
      });
    return () => controller.abort();
  }, [queryKey]);

  const current = state?.key === queryKey ? state : null;
  return {
    data: current?.data ?? null,
    error: current?.error ?? null,
    loading: current === null,
    reload: () => setTick((n) => n + 1),
    setData: (data) => setState({ key: queryKey, data, error: null }),
  };
}

interface PagedState<T> {
  key: string;
  items: T[];
  next: string | null;
  error: unknown;
}

/** Keyset-paginated list with Load More. */
export function usePagedResource<T>(
  key: string,
  fetchPage: (cursor: string | null, signal?: AbortSignal) => Promise<Page<T>>,
): {
  items: T[];
  loading: boolean;
  error: unknown;
  hasMore: boolean;
  moreBusy: boolean;
  moreError: unknown;
  loadMore: () => Promise<void>;
  reload: () => void;
  update: (mutate: (items: T[]) => T[]) => void;
} {
  const [tick, setTick] = useState(0);
  const [state, setState] = useState<PagedState<T> | null>(null);
  const [moreBusy, setMoreBusy] = useState(false);
  const [moreError, setMoreError] = useState<unknown>(null);
  const queryKey = `${key}#${tick}`;
  const first = useEffectEvent((signal: AbortSignal) =>
    fetchPage(null, signal),
  );

  useEffect(() => {
    const controller = new AbortController();
    first(controller.signal)
      .then((page) =>
        setState({
          key: queryKey,
          items: page.items,
          next: page.next_cursor,
          error: null,
        }),
      )
      .catch((error: unknown) => {
        if (!isAbortError(error))
          setState({ key: queryKey, items: [], next: null, error });
      });
    return () => controller.abort();
  }, [queryKey]);

  const current = state?.key === queryKey ? state : null;

  async function loadMore() {
    if (!current?.next) return;
    setMoreBusy(true);
    setMoreError(null);
    try {
      const page = await fetchPage(current.next);
      setState({
        key: queryKey,
        items: [...current.items, ...page.items],
        next: page.next_cursor,
        error: null,
      });
    } catch (err) {
      setMoreError(err);
    } finally {
      setMoreBusy(false);
    }
  }

  return {
    items: current?.items ?? [],
    loading: current === null,
    error: current?.error ?? null,
    hasMore: Boolean(current?.next),
    moreBusy,
    moreError,
    loadMore,
    reload: () => setTick((n) => n + 1),
    update: (mutate) =>
      setState((s) =>
        s && s.key === queryKey ? { ...s, items: mutate(s.items) } : s,
      ),
  };
}

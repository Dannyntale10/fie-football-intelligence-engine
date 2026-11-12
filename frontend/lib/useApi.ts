"use client";

import { useCallback, useEffect, useState } from "react";

import { apiGet } from "./api";

export type ApiState<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
};

export function useApi<T>(path: string | null): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(Boolean(path));
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!path) {
      setData(null);
      setLoading(false);
      setError(null);
      return;
    }

    let active = true;
    setLoading(true);
    setError(null);

    apiGet<T>(path)
      .then((value) => {
        if (!active) return;
        setData(value);
        setLoading(false);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setData(null);
        setError(reason instanceof Error ? reason.message : String(reason));
        setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [path, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  return { data, loading, error, reload };
}

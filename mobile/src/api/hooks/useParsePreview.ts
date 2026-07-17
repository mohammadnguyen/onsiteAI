import { useEffect, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { api } from '../client';
import { useAuthStore } from '../../store/auth';
import type { components } from '../types';

/**
 * forey F2: live parse preview for the capture screen.
 *
 * POST /expenses/parse runs the SAME parser as POST /expenses but
 * persists NOTHING — it returns the draft + diagnostics so the UI can
 * show what it recognised (job / amount / supplier) as the user types,
 * before they commit. The backend endpoint already existed; this is
 * its first client.
 *
 * Weak-network posture (the whole point of the debounce + silent
 * failure): the operator is on-site with unstable signal. This is a
 * PROGRESSIVE ENHANCEMENT — a slow or failed parse just means no chips
 * appear; the form still submits through POST /expenses exactly as
 * before, where the real parse + validation happen. So: retry:false,
 * no error surfaced, and the query only fires after typing settles.
 */

export type ParsePreview = components['schemas']['ParsePreview'];
export type ParseDiagnostics = components['schemas']['ParseDiagnostics'];

const DEBOUNCE_MS = 600;
// Below this the parser has nothing to work with — don't spend a
// request (or flash chips) on "b".
const MIN_CHARS = 3;

/** Debounce a value: returns the input only after it has been stable
 *  for `ms`. */
function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}

export function useParsePreview(rawText: string, expenseDate: string) {
  const accessToken = useAuthStore((s) => s.accessToken);
  const trimmed = rawText.trim();
  const debouncedText = useDebounced(trimmed, DEBOUNCE_MS);
  // Multi-line input is the batch path — its own submit handles each
  // line; a whole-blob parse would be meaningless, so skip the preview.
  const eligible =
    debouncedText.length >= MIN_CHARS && !debouncedText.includes('\n');

  const query = useQuery<ParsePreview>({
    // Date is part of the key: the parser's date sanity/relative-date
    // handling depends on it.
    queryKey: ['expense-parse', debouncedText, expenseDate],
    queryFn: async () => {
      const { data } = await api.post<ParsePreview>('/expenses/parse', {
        raw_input_text: debouncedText,
        expense_date: expenseDate,
        expense_type: 'supplier_expense',
      });
      return data;
    },
    enabled: !!accessToken && eligible,
    // Hold the previous draft across a key change so the chips dim
    // (isSettling) instead of flashing to empty on every settled edit.
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: 60_000,
    gcTime: 60_000,
  });

  return {
    draft: query.data?.draft ?? null,
    diagnostics: query.data?.diagnostics ?? null,
    // "settling" = the user is still typing (text changed but the
    // debounce hasn't fired) OR the request is in flight. Lets the UI
    // hold the old chips instead of flickering to empty.
    isSettling: eligible && (trimmed !== debouncedText || query.isFetching),
    hasPreview: eligible && query.data != null,
  };
}

/**
 * Potential Variations (DEC-PV-001): a person's flag on a Site Log entry,
 * for the builder to check. The API surface, typed locally.
 *
 * Mirrors backend/app/schemas/potential_variation.py. Like src/api/tasks.ts,
 * not generated from the OpenAPI types: regenerating them needs a running
 * backend, and these shapes are small.
 *
 * A flag is not client approval, a price or approval to start work: no
 * shape here carries an amount, an approver or a signature. The server
 * decides what a caller may do and says so in `permissions`; screens show
 * controls from that and never restate the rules.
 */
import { api } from './client';

export type PvReviewState = 'to_check' | 'following_up' | 'ended';
export const PV_REVIEW_STATES: readonly PvReviewState[] = ['to_check', 'following_up', 'ended'];

export type PvEndReason =
  | 'already_in_contract'
  | 'client_not_proceeding'
  | 'handled_outside_forey'
  | 'flagged_in_error'
  | 'other';
export const PV_END_REASONS: readonly PvEndReason[] = [
  'already_in_contract',
  'client_not_proceeding',
  'handled_outside_forey',
  'flagged_in_error',
  'other',
];

/** Server limits (backend/app/models/potential_variation.py). */
export const PV_NOTE_MAX_CHARS = 500;

export type PvUser = { user_id: string; full_name: string };

/** Job identity only — no money. */
export type PvJob = {
  job_id: string;
  job_name: string;
  job_code: string | null;
  status: 'active' | 'completed';
};

export type PvSource = {
  site_log_event_id: string;
  /** The entry version the person saw and confirmed when flagging. */
  revision_no: number;
  /** False: this user may not read the entry. Nothing taken from it is sent. */
  readable: boolean;
  /** The entry's current job, only when `readable`. */
  job_id: string | null;
};

export type PvPinnedRevision = {
  revision_no: number;
  body_text: string | null;
  internal_location: string | null;
  occurred_at: string | null;
  /** When that version was recorded. */
  created_at: string;
};

/** One of the entry's attachments as it is NOW - never a snapshot of the pinned version. */
export type PvAttachment = {
  declared_media_type: 'audio' | 'image' | 'text' | 'document';
  state: 'awaiting_upload' | 'pending' | 'stored' | 'failed';
};

/** Detail-only additions; all null unless `readable`. */
export type PvSourceDetail = PvSource & {
  pinned: PvPinnedRevision | null;
  current_revision_no: number | null;
  current_withdrawn: boolean | null;
  current_attachments: PvAttachment[] | null;
};

export type PvPermissions = {
  /** State, end reason and note - one decision. The builder's. */
  decide: boolean;
  read_note: boolean;
  /** Set the job while the flag has none. */
  set_job: boolean;
  /** Move a flag that already has a job. */
  change_job: boolean;
};

export type PvOut = {
  potential_variation_id: string;
  /** Null when this user may not read the source entry. */
  title: string | null;
  review_state: PvReviewState;
  ended_reason: PvEndReason | null;
  /** The builder's note; null for everyone else. */
  decision_note: string | null;
  origin: 'manual';
  job: PvJob | null;
  created_by: PvUser | null;
  decided_by: PvUser | null;
  decided_at: string | null;
  source: PvSource;
  permissions: PvPermissions;
  created_at: string;
  updated_at: string;
};

export type PvDetailOut = Omit<PvOut, 'source'> & { source: PvSourceDetail };

export type PvCreateBody = {
  create_client_id: string;
  title: string;
  /** The entry version shown to the person when they tapped Save. */
  source_revision_no: number;
};

/** State, end reason and note travel together, as one decision. */
export type PvDecision = {
  review_state: PvReviewState;
  ended_reason?: PvEndReason | null;
  note?: string | null;
};

/** Only the fields sent are changed. `job_id` can be set, never cleared. */
export type PvPatch = { decision?: PvDecision; job_id?: string };

export const PV_PAGE_SIZE = 20;
/** The largest page the server accepts (`limit` is 1..50). */
export const PV_PAGE_MAX = 50;

export type PvListParams = {
  review_state?: PvReviewState;
  job_id?: string;
  /** 1..PV_PAGE_MAX; PV_PAGE_SIZE when left out. */
  limit?: number;
  offset?: number;
};

export async function createPotentialVariation(
  eventId: string,
  body: PvCreateBody,
): Promise<PvOut> {
  const r = await api.post<PvOut>(`/site-log-events/${eventId}/potential-variations`, body);
  return r.data;
}

/** The flags made from one entry that this user may see. */
export async function listEventPotentialVariations(eventId: string): Promise<PvOut[]> {
  const r = await api.get<PvOut[]>(`/site-log-events/${eventId}/potential-variations`);
  return r.data;
}

/** Flags this user may see, newest first. */
export async function listPotentialVariations(params: PvListParams = {}): Promise<PvOut[]> {
  const r = await api.get<PvOut[]>('/potential-variations', {
    params: { ...params, limit: params.limit ?? PV_PAGE_SIZE },
  });
  return r.data;
}

/**
 * The flag this account created with this key, if the server has it.
 *
 * How a create whose answer was lost finds out what happened: asked BEFORE
 * anything is sent again, never instead of the original request.
 */
export async function findMyPvCreate(createClientId: string): Promise<PvOut | null> {
  const r = await api.get<PvOut[]>('/potential-variations', {
    params: { create_client_id: createClientId },
  });
  return r.data.length > 0 ? r.data[0] : null;
}

export async function getPotentialVariation(pvId: string): Promise<PvDetailOut> {
  const r = await api.get<PvDetailOut>(`/potential-variations/${pvId}`);
  return r.data;
}

export async function updatePotentialVariation(
  pvId: string,
  patch: PvPatch,
): Promise<PvDetailOut> {
  const r = await api.patch<PvDetailOut>(`/potential-variations/${pvId}`, patch);
  return r.data;
}

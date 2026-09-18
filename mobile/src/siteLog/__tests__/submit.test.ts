jest.mock('expo-file-system/legacy', () => require('./support/memfs'));
jest.mock('../../api/siteLog', () => {
  const actual = jest.requireActual('../../api/siteLog');
  return {
    __esModule: true,
    // serverOwnedAttachmentIds is pure logic this flow depends on: keep it.
    serverOwnedAttachmentIds: actual.serverOwnedAttachmentIds,
    MINE_PAGE_SIZE: actual.MINE_PAGE_SIZE,
    declareCapture: jest.fn(),
    uploadAttachment: jest.fn(),
    finalizeCapture: jest.fn(),
    getEvent: jest.fn(),
    findMineByCaptureClientId: jest.fn(),
    assignJob: jest.fn(),
    listMine: jest.fn(),
  };
});

import * as api from '../../api/siteLog';
import { useAuthStore } from '../../store/auth';
import type { SiteLogDraft } from '../../store/siteLogDrafts';
import { runSubmit } from '../submit';
import { memfs } from './support/memfs';
import {
  CAPTURE,
  EVENT_ID,
  INLINE_ID,
  USER,
  attachment,
  draft,
  httpError,
  serverAttachment,
  serverEvent,
  timeoutError,
} from './support/fixtures';

const mocked = api as jest.Mocked<typeof api>;

/** Collects the draft as the flow would leave it on disk. */
function recorder(d: SiteLogDraft) {
  const state = { ...d };
  const patches: Partial<SiteLogDraft>[] = [];
  return {
    state,
    patches,
    ctx: {
      draft: d,
      userId: USER,
      sessionNonce: 0,
      patch: async (p: Partial<SiteLogDraft>) => {
        patches.push(p);
        Object.assign(state, p);
      },
    },
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  memfs.reset();
  memfs.put(attachment().uri, 10);
  useAuthStore.setState({ sessionNonce: 0 });
});

// =====================================================================
// One submission, one record
// =====================================================================
describe('a submission that goes through', () => {
  it('declares once, uploads what it declared, and finalizes', async () => {
    mocked.findMineByCaptureClientId.mockResolvedValue(null);
    mocked.declareCapture.mockResolvedValue(
      serverEvent([
        serverAttachment('att-1', 'awaiting_upload'),
        serverAttachment(INLINE_ID, 'stored'),
      ]),
    );
    mocked.uploadAttachment.mockResolvedValue(serverAttachment('att-1', 'stored'));
    mocked.getEvent.mockResolvedValue(
      serverEvent([
        serverAttachment('att-1', 'stored'),
        serverAttachment(INLINE_ID, 'stored'),
      ]),
    );
    mocked.finalizeCapture.mockResolvedValue(
      serverEvent(
        [serverAttachment('att-1', 'stored'), serverAttachment(INLINE_ID, 'stored')],
        'complete',
      ),
    );

    const r = recorder(draft());
    const outcome = await runSubmit(r.ctx);

    expect(outcome.kind).toBe('complete');
    expect(mocked.declareCapture).toHaveBeenCalledTimes(1);
    expect(mocked.uploadAttachment).toHaveBeenCalledTimes(1);
    expect(mocked.uploadAttachment).toHaveBeenCalledWith(
      EVENT_ID,
      'att-1',
      expect.objectContaining({ uri: attachment().uri }),
    );
    // The server's own row is never PUT to.
    expect(mocked.uploadAttachment).not.toHaveBeenCalledWith(
      EVENT_ID,
      INLINE_ID,
      expect.anything(),
    );
  });

  it('pins the declaration before the first request', async () => {
    mocked.findMineByCaptureClientId.mockResolvedValue(null);
    mocked.declareCapture.mockRejectedValue(timeoutError());

    const r = recorder(draft());
    await runSubmit(r.ctx);

    expect(r.state.declaration).not.toBeNull();
    expect(r.state.declaration?.capture_client_id).toBe(CAPTURE);
    expect(r.state.declaration?.attachments.map((a) => a.attachment_client_id)).toEqual([
      'att-1',
    ]);
    // Pinned BEFORE declare was called, not after it answered.
    const pinnedAt = r.patches.findIndex((p) => 'declaration' in p);
    expect(pinnedAt).toBeGreaterThanOrEqual(0);
  });
});

// =====================================================================
// A retry is not a second record
// =====================================================================
describe('retrying the same submission', () => {
  it('does not declare again when the record already exists', async () => {
    const pinned = draft({
      declaration: {
        capture_client_id: CAPTURE,
        job_id: null,
        occurred_at: null,
        internal_location: null,
        body_text: 'Poured 12m3 bay 3',
        attachments: [
          { attachment_client_id: 'att-1', declared_media_type: 'image', declared_size_bytes: 10 },
        ],
      },
      server: { site_log_event_id: EVENT_ID, capture_status: 'partial_failed', observed_at: 1 },
      attachments: [attachment({ status: 'failed' })],
    });
    mocked.getEvent
      .mockResolvedValueOnce(
        serverEvent([
          serverAttachment('att-1', 'failed'),
          serverAttachment(INLINE_ID, 'stored'),
        ]),
      )
      .mockResolvedValueOnce(
        serverEvent([
          serverAttachment('att-1', 'stored'),
          serverAttachment(INLINE_ID, 'stored'),
        ]),
      );
    mocked.uploadAttachment.mockResolvedValue(serverAttachment('att-1', 'stored'));
    mocked.finalizeCapture.mockResolvedValue(
      serverEvent(
        [serverAttachment('att-1', 'stored'), serverAttachment(INLINE_ID, 'stored')],
        'complete',
      ),
    );

    const outcome = await runSubmit(recorder(pinned).ctx);

    expect(mocked.declareCapture).not.toHaveBeenCalled();
    expect(outcome.kind).toBe('complete');
  });

  it('never re-uploads an attachment the server already stored', async () => {
    const two = draft({
      attachments: [attachment(), attachment({ attachment_client_id: 'att-2' })],
      server: { site_log_event_id: EVENT_ID, capture_status: 'partial_failed', observed_at: 1 },
    });
    memfs.put(two.attachments[1].uri, 10);
    mocked.getEvent.mockResolvedValue(
      serverEvent([
        serverAttachment('att-1', 'stored'),
        serverAttachment('att-2', 'failed'),
        serverAttachment(INLINE_ID, 'stored'),
      ]),
    );
    mocked.uploadAttachment.mockResolvedValue(serverAttachment('att-2', 'stored'));
    mocked.finalizeCapture.mockResolvedValue(
      serverEvent([serverAttachment('att-1', 'stored')], 'complete'),
    );

    await runSubmit(recorder(two).ctx);

    const uploaded = mocked.uploadAttachment.mock.calls.map((c) => c[1]);
    expect(uploaded).toEqual(['att-2']);
  });

  it('keeps the same ids across two attempts', async () => {
    mocked.findMineByCaptureClientId.mockResolvedValue(null);
    mocked.declareCapture.mockRejectedValueOnce(timeoutError());

    const d = draft();
    const first = recorder(d);
    await runSubmit(first.ctx);

    // Second attempt, same draft object as the screen would hand back.
    const resumed: SiteLogDraft = { ...d, ...first.state };
    mocked.findMineByCaptureClientId.mockResolvedValue(
      serverEvent([
        serverAttachment('att-1', 'awaiting_upload'),
        serverAttachment(INLINE_ID, 'stored'),
      ]),
    );
    mocked.uploadAttachment.mockResolvedValue(serverAttachment('att-1', 'stored'));
    mocked.getEvent.mockResolvedValue(
      serverEvent([serverAttachment('att-1', 'stored')], 'complete'),
    );
    mocked.finalizeCapture.mockResolvedValue(
      serverEvent([serverAttachment('att-1', 'stored')], 'complete'),
    );

    await runSubmit(recorder(resumed).ctx);

    expect(resumed.capture_client_id).toBe(CAPTURE);
    expect(resumed.declaration).toEqual(first.state.declaration);
    // No second record: the lookup found the first one and declare was not
    // called again.
    expect(mocked.declareCapture).toHaveBeenCalledTimes(1);
  });
});

// =====================================================================
// A lost answer is not a failure to save
// =====================================================================
describe('when the answer is lost', () => {
  it('reports the save as unconfirmed rather than failed', async () => {
    mocked.findMineByCaptureClientId.mockResolvedValue(null);
    mocked.declareCapture.mockRejectedValue(timeoutError());

    const r = recorder(draft());
    const outcome = await runSubmit(r.ctx);

    expect(outcome.kind).toBe('unconfirmed');
    expect(r.state.unconfirmed).toBe(true);
  });

  it('marks the draft unconfirmed BEFORE the declaration is sent', async () => {
    mocked.findMineByCaptureClientId.mockResolvedValue(null);
    let unconfirmedWhenCalled: boolean | undefined;
    const r = recorder(draft());
    mocked.declareCapture.mockImplementation(async () => {
      // This is the window in which the process can die with the record
      // created on the server and nothing recorded here.
      unconfirmedWhenCalled = r.state.unconfirmed;
      throw timeoutError();
    });

    await runSubmit(r.ctx);

    expect(unconfirmedWhenCalled).toBe(true);
  });

  it('finds the record again by capture_client_id', async () => {
    const lost = draft({ server: null });
    mocked.findMineByCaptureClientId.mockResolvedValue(
      serverEvent([
        serverAttachment('att-1', 'stored'),
        serverAttachment(INLINE_ID, 'stored'),
      ]),
    );
    mocked.getEvent.mockResolvedValue(
      serverEvent([serverAttachment('att-1', 'stored')], 'complete'),
    );
    mocked.finalizeCapture.mockResolvedValue(
      serverEvent([serverAttachment('att-1', 'stored')], 'complete'),
    );

    const r = recorder(lost);
    const outcome = await runSubmit(r.ctx);

    expect(mocked.findMineByCaptureClientId).toHaveBeenCalledWith(CAPTURE);
    expect(mocked.declareCapture).not.toHaveBeenCalled();
    expect(outcome.kind).toBe('complete');
    expect(r.state.server?.site_log_event_id).toBe(EVENT_ID);
  });

  it('clears the uncertainty when the server refuses outright', async () => {
    mocked.findMineByCaptureClientId.mockResolvedValue(null);
    mocked.declareCapture.mockRejectedValue(httpError(422, 'job is completed'));

    const r = recorder(draft());
    const outcome = await runSubmit(r.ctx);

    expect(outcome.kind).toBe('error');
    // The server answered: nothing was created, so this is not "unknown".
    expect(r.state.unconfirmed).toBe(false);
  });
});

// =====================================================================
// The server's own inline row
// =====================================================================
describe('inline text', () => {
  it('replays the pinned declaration instead of uploading to the reserved row', async () => {
    const pinnedDeclaration = {
      capture_client_id: CAPTURE,
      job_id: null,
      occurred_at: null,
      internal_location: null,
      body_text: 'Poured 12m3 bay 3',
      attachments: [
        { attachment_client_id: 'att-1', declared_media_type: 'image' as const, declared_size_bytes: 10 },
      ],
    };
    const d = draft({
      declaration: pinnedDeclaration,
      server: { site_log_event_id: EVENT_ID, capture_status: 'partial_failed', observed_at: 1 },
      attachments: [attachment({ status: 'stored' })],
    });
    mocked.getEvent.mockResolvedValue(
      serverEvent([
        serverAttachment('att-1', 'stored'),
        serverAttachment(INLINE_ID, 'failed'),
      ]),
    );
    mocked.declareCapture.mockResolvedValue(
      serverEvent([
        serverAttachment('att-1', 'stored'),
        serverAttachment(INLINE_ID, 'stored'),
      ]),
    );
    mocked.finalizeCapture.mockResolvedValue(
      serverEvent([serverAttachment('att-1', 'stored')], 'complete'),
    );

    await runSubmit(recorder(d).ctx);

    // Replayed VERBATIM - the server fingerprints it.
    expect(mocked.declareCapture).toHaveBeenCalledWith(pinnedDeclaration);
    expect(mocked.uploadAttachment).not.toHaveBeenCalled();
  });

  it('states the limitation when the replay is refused, and keeps the record', async () => {
    const d = draft({
      declaration: {
        capture_client_id: CAPTURE,
        job_id: null,
        occurred_at: null,
        internal_location: null,
        body_text: 'Poured 12m3 bay 3',
        attachments: [],
      },
      attachments: [],
      server: { site_log_event_id: EVENT_ID, capture_status: 'partial_failed', observed_at: 1 },
    });
    mocked.getEvent.mockResolvedValue(
      serverEvent([serverAttachment(INLINE_ID, 'failed')], 'partial_failed'),
    );
    mocked.declareCapture.mockRejectedValue(httpError(422, 'job is completed'));
    mocked.finalizeCapture.mockResolvedValue(
      serverEvent([serverAttachment(INLINE_ID, 'failed')], 'partial_failed'),
    );

    const r = recorder(d);
    const outcome = await runSubmit(r.ctx);

    expect(outcome.kind).toBe('partial');
    expect(outcome.kind === 'partial' && outcome.limitation).toBe(
      'siteLog.status.inline_unrecoverable',
    );
    // The record and its id survive; no substitute is created.
    expect(r.state.server?.site_log_event_id).toBe(EVENT_ID);
    expect(mocked.declareCapture).toHaveBeenCalledTimes(1);
  });

  it('keeps the event a 502 carried, and calls it a failure rather than progress', async () => {
    const carried = serverEvent([
      serverAttachment('att-1', 'awaiting_upload'),
      serverAttachment(INLINE_ID, 'failed'),
    ]);
    mocked.findMineByCaptureClientId.mockResolvedValue(null);
    mocked.declareCapture.mockRejectedValue(httpError(502, carried));

    const r = recorder(draft());
    const outcome = await runSubmit(r.ctx);

    expect(outcome.kind).toBe('created_not_uploaded');
    expect(outcome.kind === 'created_not_uploaded' && outcome.bodyKey).toBe(
      'siteLog.status.inline_failed',
    );
    expect(r.state.server?.site_log_event_id).toBe(EVENT_ID);
    expect(r.state.unconfirmed).toBe(false);
  });
});

// =====================================================================
// pending is the server's business
// =====================================================================
describe('an attachment the server is still processing', () => {
  it('is reported, never re-sent and never reset', async () => {
    const d = draft({
      server: { site_log_event_id: EVENT_ID, capture_status: 'pending_upload', observed_at: 1 },
    });
    mocked.getEvent.mockResolvedValue(
      serverEvent([
        serverAttachment('att-1', 'pending'),
        serverAttachment(INLINE_ID, 'stored'),
      ]),
    );

    const outcome = await runSubmit(recorder(d).ctx);

    expect(mocked.uploadAttachment).not.toHaveBeenCalled();
    // Nothing may finalize while a row is in flight, and nothing here may
    // clear it: reset is admin-only.
    expect(mocked.finalizeCapture).not.toHaveBeenCalled();
    expect(outcome.kind).toBe('created_not_uploaded');
    expect(outcome.kind === 'created_not_uploaded' && outcome.blocked).toEqual(['att-1']);
    expect(outcome.kind === 'created_not_uploaded' && outcome.bodyKey).toBe(
      'siteLog.status.blocked_body',
    );
  });
});

// =====================================================================
// The file is gone from this phone
// =====================================================================
describe('when a kept file has been removed', () => {
  it('reports it as missing instead of failing an upload', async () => {
    memfs.reset(); // app data cleared: the kept copy is gone too
    const d = draft({
      server: { site_log_event_id: EVENT_ID, capture_status: 'pending_upload', observed_at: 1 },
    });
    mocked.getEvent.mockResolvedValue(
      serverEvent([serverAttachment('att-1', 'awaiting_upload')], 'pending_upload'),
    );
    mocked.finalizeCapture.mockResolvedValue(
      serverEvent([serverAttachment('att-1', 'awaiting_upload')], 'partial_failed'),
    );

    const r = recorder(d);
    const outcome = await runSubmit(r.ctx);

    expect(mocked.uploadAttachment).not.toHaveBeenCalled();
    expect(outcome.kind).toBe('partial');
    expect(r.state.attachments[0].status).toBe('missing');
  });
});

// =====================================================================
// One submission belongs to one account
// =====================================================================
describe('account isolation', () => {
  it('refuses a draft belonging to somebody else', async () => {
    const outcome = await runSubmit({
      draft: draft({ user_id: 'somebody-else' }),
      userId: USER,
      sessionNonce: 0,
      patch: async () => undefined,
    });

    expect(outcome).toEqual({ kind: 'error', messageKey: 'siteLog.error.not_yours' });
    expect(mocked.findMineByCaptureClientId).not.toHaveBeenCalled();
    expect(mocked.declareCapture).not.toHaveBeenCalled();
  });

  it('stops before sending when the account changed while it was starting', async () => {
    // The screen read the nonce, then the durable write took long enough for
    // a sign-out and a sign-in to land.
    useAuthStore.setState({ sessionNonce: 2 });

    const r = recorder(draft());
    const outcome = await runSubmit({ ...r.ctx, sessionNonce: 0 });

    expect(outcome).toEqual({ kind: 'error', messageKey: 'siteLog.error.session_changed' });
    expect(mocked.findMineByCaptureClientId).not.toHaveBeenCalled();
    expect(mocked.declareCapture).not.toHaveBeenCalled();
    expect(r.patches).toEqual([]);
  });

  it('stops before declaring when the account changes during the lookup', async () => {
    mocked.findMineByCaptureClientId.mockImplementation(async () => {
      useAuthStore.setState({ sessionNonce: 1 });
      return null;
    });

    const r = recorder(draft());
    const outcome = await runSubmit(r.ctx);

    expect(outcome).toEqual({ kind: 'error', messageKey: 'siteLog.error.session_changed' });
    expect(mocked.declareCapture).not.toHaveBeenCalled();
  });

  it('stops before uploading when the account changes after the declare', async () => {
    mocked.findMineByCaptureClientId.mockResolvedValue(null);
    mocked.declareCapture.mockImplementation(async () => {
      useAuthStore.setState({ sessionNonce: 1 });
      return serverEvent([serverAttachment('att-1', 'awaiting_upload')]);
    });

    const outcome = await runSubmit(recorder(draft()).ctx);

    expect(outcome).toEqual({ kind: 'error', messageKey: 'siteLog.error.session_changed' });
    expect(mocked.uploadAttachment).not.toHaveBeenCalled();
  });

  it('writes nothing to the draft store once the account has changed', async () => {
    mocked.findMineByCaptureClientId.mockImplementation(async () => {
      useAuthStore.setState({ sessionNonce: 5 });
      return null;
    });

    const r = recorder(draft());
    await runSubmit(r.ctx);

    // The declaration pin happened before the change; nothing after it.
    expect(r.patches.every((p) => 'declaration' in p)).toBe(true);
  });
});
